"""Server 轮次编排：runner。"""


import asyncio
import logging
from collections.abc import AsyncIterator
from functools import partial
from typing import Any

from langchain_core.runnables import RunnableConfig

from ai_coding_harness.agent.clarification import parse_clarification
from ai_coding_harness.agent.middleware import TurnState, TurnStop
from ai_coding_harness.agent.profiles import (
    QA_FORM,
    profile_for,
)
from ai_coding_harness.core.stream import (
    CREATE_PR_TOOL,
    RUN_EVENT_KIND,
    TASK_TOOL,
    ToolNames,
    V3Translator,
    call_id_of,
    event_name,
    last_ai_text,
    method_of,
    parent_run_id_of,
    payload_of,
    run_id_of,
    subagent_name,
)
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.observability.run_log import AgentRunLog
from ai_coding_harness.safety.redact import (
    detect_credential,
    redact,
    secret_terms,
)
from ai_coding_harness.server.intent import (
    SOURCE_ACTIVE,
    SOURCE_CLARIFY_CHOICE,
    IntentDecision,
    active_task_override,
    clarification_decision,
    gate_intent_for_state,
)
from ai_coding_harness.server.repo_state import describe_snapshot, intent_candidates
from ai_coding_harness.session.manager import (
    Session,
    SessionRuntime,
    WorkspaceError,
)

logger = logging.getLogger(__name__)

from .common import (
    _MEMORY_WRITE_FORMS,
    _PLAN_TOOLS,
    WORKSPACE_ERROR_MESSAGE,
    _pr_refs,
    _redacted_parts,
    _redacted_todos,
    credential_refusal,
)

_RUN_TEXT_LIMIT = 8_000


def _redact_run_payload(value: Any, terms: tuple[str, ...]) -> Any:
    """脱敏并限制 run trace 的单段文本，避免内层输出把记录撑爆。"""
    if isinstance(value, str):
        text = redact(value, terms)
        return text[:_RUN_TEXT_LIMIT] + ("…（已截断）" if len(text) > _RUN_TEXT_LIMIT else "")
    if isinstance(value, list):
        return [_redact_run_payload(item, terms) for item in value[:200]]
    if isinstance(value, dict):
        return {
            str(key): _redact_run_payload(item, terms)
            for key, item in value.items()
        }
    return value


def _add_run_ids(frame: dict[str, Any], run_id: Any, parent_run_id: Any) -> dict[str, Any]:
    """只在真有 run id 时加字段，旧事件形状与旧断言不受影响。"""
    if run_id:
        frame["run_id"] = str(run_id)
    if parent_run_id:
        frame["parent_run_id"] = str(parent_run_id)
    return frame


class TurnRunnerMixin:
    """runner 相关方法（由 HarnessService 组合）。"""

    async def chat(
        self,
        session: Session,
        message: str,
        *,
        clarification_id: str | None = None,
        choice: int | str | None = None,
        answers: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """处理用户消息。

        事件形如 ``{"type": "ai"|"tool"|"plan"|"done"|"error", ...}``，
        由 API 层序列化为 SSE。

        出口（进状态前、落日志前、下发前、直接执行返回前）各拦一道，词表在这里算一次，
        按会话取 —— 令牌是每会话一份，不能进模块级常量。此外入口先做一次**拒收**：
        消息里含凭据就整条不收，凭据连模型都不发（见 ``detect_credential``）。

        会话这个日志维度**不在这里设** —— 设在 ``api/session_lookup.resolve_session``：
        每个会话级请求都从那儿过，连 ``/workspace`` 触发的 provisioning 也算这个会话的日志。
        """
        terms = secret_terms(
            session.repo.token,
            self._settings.gitee_access_token,
            # 模型凭据也进词表（2026-10-13）：库里/界面配的 key 同样不该出现在日志与记录里
            *self._settings.model_secret_values(),
        )
        # 结构化选择：先消费 pending；普通消息则立刻让旧澄清失效（用户口径 2026-10-19）。
        chosen: tuple[str, str, str] | None = None
        if clarification_id and (answers or choice is not None) and str(choice) != "other":
            chosen = await self._consume_clarification(
                session,
                clarification_id=clarification_id,
                choice=choice,
                answers=answers,
            )
            if chosen is None:
                if not message.strip():
                    yield {"type": "error", "error": "这次澄清已失效，请重新描述。"}
                    yield {"type": "done"}
                    return
                await self._clear_clarification(session)
        else:
            await self._clear_clarification(session)
        label = detect_credential(message, terms)
        if label is not None:
            # 拒收在建模之前：意图分类那一跳也会把消息发给模型，晚一步就白拦了
            await self._reject_credential(session, message, label, terms)
            yield {"type": "ai", "content": credential_refusal(label)}
            yield {"type": "done"}
            return
        history = await self._intent_history(session)
        # 助手上一轮说了什么：用户回一句「修复吧」时，指代对象只在那里
        previous_reply = await self._last_reply(session)
        # 上一轮是什么意图：追问「为什么不派 coder」时，比字面里的「为什么」可靠
        previous_intent = await self._last_intent(session)
        # 最近两轮对话：用户全文 + 助手首尾截断，判断“继续/修正/解释上一轮”要看它
        dialogue = await self._dialogue(session)
        # 最近一次可执行任务：analysis/qa 不覆盖它，短追问优先沿用它
        active_intent = await self._active_task(session)
        # 每轮实时查一次全量 PR；同一份列表用于记忆同步与意图候选。
        snapshot = await self._repo_snapshot(session)
        if snapshot is not None:
            try:
                await self._sync_repo_memory_before_turn(session, snapshot.pulls)
            except Exception:
                logger.exception("轮前仓库记忆同步失败（本轮继续，轮末会再试）")
        allowed_intents = intent_candidates(snapshot)
        repo_status = describe_snapshot(snapshot)
        # 每轮一份：累加器挂在服务实例上（重判要跨调用累加），所以必须逐轮清零 ——
        # 不清就会把上一轮的判定开销带到这一轮的账上。
        self._intent_usage = {}
        self._last_usage_frame = 0.0
        if chosen is not None:
            message, chosen_intent, chosen_label = chosen
            decision = IntentDecision(
                chosen_intent,
                SOURCE_CLARIFY_CHOICE,
                "用户选择了澄清候选",
            )
        else:
            decision = await self._detect_intent(
                message,
                history,
                previous_reply,
                previous_intent,
                active_intent,
                dialogue,
                allowed_intents=allowed_intents,
                repo_state=repo_status,
            )
        override = (
            active_task_override(message, decision.intent, active_intent)
            if chosen is None
            else None
        )
        if override:
            decision = IntentDecision(
                override, SOURCE_ACTIVE, f"沿用未完成任务 {active_intent}"
            )
        state_block: str | None = None
        gate = gate_intent_for_state(decision.intent, allowed_intents)
        if gate.intent is None:
            state_block = gate.reason
        # 低置信度：先转 qa 向用户确认，避免拿一个猜出来的形态直接干活。
        # active_task_override 命中时不动 —— 用户明确在继续未完成任务，那是更强的信号。
        clarification: dict[str, Any] | None = None
        if not override and state_block is None and (
            clarified := clarification_decision(
                decision, self._settings.intent_clarify_confidence
            )
        ) is not None:
            decision = clarified
            clarification = {
                "confidence": clarified.confidence,
                "candidates": list(clarified.candidates),
            }
        intent = decision.intent
        self._remember_active_task(session, intent, message)
        # 影子路由（默认关）：只记轴，不采用建议、不改变 intent。独立 task 不占本轮首字节。
        if self._settings.intent_router_shadow:
            shadow = asyncio.create_task(
                self._shadow_route(
                    message,
                    history,
                    previous_reply,
                    previous_intent,
                    active_intent,
                    dialogue,
                    decision,
                    str(session.public_id),
                )
            )
            self._shadow_tasks.add(shadow)
            shadow.add_done_callback(self._shadow_tasks.discard)
        logger.info(
            "意图分析：intent=%s source=%s 理由=%s pr_state=%s public_id=%s",
            intent,
            decision.source,
            decision.reason or "-",
            repo_status,
            session.public_id,
        )
        if chosen is not None:
            record_content = f"选择：{chosen_label}"
            record_extra = {
                "intent": intent,
                "intent_source": SOURCE_CLARIFY_CHOICE,
            }
        else:
            record_content = message
            record_extra = {
                "intent": intent,
                "intent_source": decision.source,
            }
        await self._record(
            session,
            type="user",
            summary=record_content[:200],
            content=record_content,
            extra=record_extra,
        )
        await self._name_session(session, message)

        if state_block is not None:
            await self._record(
                session,
                type="assistant",
                summary=state_block,
                content=state_block,
                extra={"intent": intent, "aborted": "intent_state_gate"},
            )
            yield {"type": "ai", "content": state_block}
            yield {"type": "done"}
            return

        # 每轮一份运行时状态：循环守卫拿它数重复，``handoffTask`` 拿它重判意图并把原话
        # 交给另一个形态（分类模型与超时都在这一层，所以回调也从这里给）。
        # 智能体是长期存活对象，逐轮变化的东西只能经 config 注入。
        state = TurnState(
            message=message,
            history=history,
            repeats=self._settings.loop_guard_repeats,
            open_prs=tuple(snapshot.open_prs) if snapshot is not None else (),
            pr_state_known=snapshot is not None,
            allowed_intents=allowed_intents,
            # 回调契约是 (message, history)：把 session 与「助手上一轮末尾」先绑进去，
            # 让重判也能看到「用户在回答哪个问题」（不绑的话它只看得到用户那几句）。
            rejudge=partial(
                self._rejudge_intent,
                session=session,
                previous_reply=previous_reply,
                previous_intent=previous_intent,
                dialogue=dialogue,
                allowed_intents=allowed_intents,
                repo_state=repo_status,
            ),
        )

        try:
            # 工作区到这一跳才准备：拒收的消息上面就 return 了，不会白建一个容器
            runtime = await self._manager.ensure_runtime(session)
        except WorkspaceError as exc:
            logger.error(
                "代码工作区不可用：public_id=%s 原因=%s",
                session.public_id, redact(str(exc), terms),
            )
            yield {"type": "error", "error": WORKSPACE_ERROR_MESSAGE}
            yield {"type": "done"}
            return

        try:
            async for event in self._run_agent(
                session,
                runtime,
                message,
                intent,
                terms,
                state=state,
                clarification=clarification,
            ):
                yield event
            # 轮末打底：只有 coding / rework 共用的 orchestrate 形态才有要沉淀的事实。
            if profile_for(intent).form in _MEMORY_WRITE_FORMS:
                await self._refresh_repo_memory(session, runtime)
        except Exception as exc:
            # exception() 自带堆栈，别再把 exc 塞进 msg（TRY401）
            logger.exception("处理失败")
            # 异常文本可能夹带原始输出（工具报错里常把返回值整段带上）
            yield {"type": "error", "error": redact(str(exc), terms)}
        yield {"type": "done"}

    async def _reject_credential(
        self, session: Session, message: str, label: str, terms: tuple[str, ...]
    ) -> None:
        """拒收含凭据的消息，并把这次拒收留痕。

        原文不进 Postgres、不进日志、不进模型：落库的是脱敏版，``extra`` 里只留类别名。
        也**不调** ``_name_session`` —— 首条消息会变成会话标题写进库，那等于给凭据留了后门。
        """
        safe = redact(message, terms)
        await self._record(
            session,
            type="user",
            summary=safe[:200],
            content=safe,
            extra={"intent": "rejected", "credential": label},
        )
        notice = credential_refusal(label)
        await self._record(
            session, type="assistant", summary=notice, content=notice,
            extra={"rejected": label},
        )

    async def _run_agent(
        self,
        session: Session,
        runtime: SessionRuntime,
        message: str,
        intent: str,
        terms: tuple[str, ...],
        *,
        state: TurnState,
        clarification: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        # 装配形态按本轮意图定：qa / analysis 只读，plan / review 单智能体（主控自己出
        # 方案 / 自己审），coding 与 rework 挂三个子智能体（两者共用一套装配，表见
        # agent/profiles.py）。同一会话换形态不影响历史 —— thread 是共享的。
        agent = self._manager.get_agent(runtime, session, profile_for(intent))
        config = RunnableConfig(
            configurable={
                "thread_id": self._manager.thread_id(session),
                "intent": intent,
                # 低置信度澄清：qa 的中间件读它，决定要不要先追问
                "clarification": clarification,
                # 智能体是长期存活对象，词表只能逐轮经 config 注入（脱敏中间件读它）
                "redact_terms": terms,
                # 同上是逐轮的东西：循环守卫数重复、handoffTask 重判与交接都用它
                "turn_state": state,
            },
            recursion_limit=self._settings.agent_recursion_limit,
        )
        logger.info(
            "派发智能体：public_id=%s intent=%s 消息 %d 字",
            session.public_id, intent, len(message),
            extra={"agent": MAIN_AGENT},
        )
        text = ""
        llm_calls = 0
        tool_calls = 0
        last_plan: dict[str, Any] | None = None
        persistent_run_events: list[dict[str, Any]] = []
        run_log = AgentRunLog(
            arg_limit=self._settings.log_tool_arg_max,
            result_limit=self._settings.log_tool_result_max,
            delegation_result_limit=self._settings.log_delegation_result_max,
            terms=terms,
        )
        # 能力交接的内层那一轮把它的用量交回来：它的事件流被摘掉（见 tools/handoff.py），
        # 不接这一根，Token 面板会漏掉整段。
        state.usage_sink = run_log.merge_usage
        translator = V3Translator(allow_todos=profile_for(intent).form != QA_FORM)
        names = ToolNames()
        # task 的 tool_call_id → 派的是哪个子智能体。只为一件事：工具帧上带角色，前端
        # 才说得出「正在编码」而不是笼统的「正在处理子任务」。收尾那帧也得带 —— 不带的话
        # 子智能体一返回，提示就翻回泛化文案，而主智能体还要想几秒才开口。
        delegated: dict[str, str] = {}
        # 本轮若建了 PR，这里留下它的 base / head —— 收尾时拿它去本地缓存算「这一轮改了什么」。
        # 入参在 tool-started 那一帧上（与「派给谁」同一个来源），之后拿不到。
        pr_refs: dict[str, str] | None = None

        # v3 的 astream_events 返回的是协程，得先 await 出流（v2 直接就是流）
        try:
            stream = await agent.astream_events(
                {"messages": [{"role": "user", "content": message}]},
                config=config,
                version="v3",
            )
            async for event in stream:
                method = method_of(event)
                if method == "messages":
                    run_log.on_message(event)
                    if event_name(event) == "message-finish":
                        llm_calls += 1
                        # 账变了就推一帧（节流在 _live_usage 里）—— 数字跟着跑起来长，
                        # 不用等这一轮收尾。纯文本问答没有工具调用，这里是它唯一的进度点。
                        live = self._live_usage(session, intent, run_log)
                        if live is not None:
                            yield {"type": "usage", "usage": live}
                    # 正文 / 思考 / 待办树都走这里；子智能体的正文在 translate 里被丢掉，
                    # 只有它的思考与 write_todos 会下发（思考不落库）
                    for part in translator.feed(event):
                        if part["type"] == "ai":
                            # 先脱敏再累加：这一处同时管住 SSE、业务库记录与首段摘要
                            answer = redact(part["content"], terms)
                            if answer:
                                text += answer
                                if clarification is None:
                                    yield _add_run_ids({
                                        "type": "ai",
                                        "content": answer,
                                    }, part.get("run_id"), part.get("parent_run_id"))
                        elif part["type"] == "reasoning":
                            # 思考只走 SSE：**不进 text / parts / 记录**。重连时能靠
                            # RunHub 的临时帧缓冲重放，库里不会留。
                            thought = redact(part["content"], terms)
                            if thought:
                                yield _add_run_ids({
                                    "type": "reasoning",
                                    "content": thought,
                                    "agent": part.get("agent") or "main",
                                }, part.get("run_id"), part.get("parent_run_id"))
                        else:
                            yield {
                                "type": "todos",
                                "items": _redacted_todos(part["items"], terms),
                            }
                elif method == "tools":
                    name = event_name(event)
                    if name == "tool-started":
                        tool_calls += 1
                        # 委派要赶在子智能体的 write_todos 之前记下（树靠它认父项）
                        translator.note_delegation(event)
                        called = names.remember(event)
                        frame = _add_run_ids({
                            "type": "tool",
                            "name": called,
                            "content": "",
                        }, run_id_of(event) or call_id_of(event), parent_run_id_of(event))
                        if called == CREATE_PR_TOOL:
                            pr_refs = _pr_refs((payload_of(event) or {}).get("input")) or pr_refs
                        if called == TASK_TOOL:
                            # 「派给谁」只有这帧拿得到（入参在这儿），收尾帧回头查
                            role = subagent_name((payload_of(event) or {}).get("input"))
                            if role:
                                delegated[call_id_of(event)] = role
                                frame["agent"] = role
                        run_log.on_tool_start(event, called)
                        yield frame
                    elif name == "tool-finished":
                        called = names.resolve(event)
                        run_log.on_tool_end(event, called)
                        frame = _add_run_ids({
                            "type": "tool",
                            "name": called,
                            # 中间件已把进状态的返回值脱敏，这里再兜一道：工具事件的
                            # output 与进 state 的那份是否同一个对象没验证过
                            "content": redact(
                                str((payload_of(event) or {}).get("output", "")), terms
                            )[:500],
                        }, run_id_of(event) or call_id_of(event), parent_run_id_of(event))
                        role = delegated.pop(call_id_of(event), "")
                        if role:
                            frame["agent"] = role
                        yield frame
                        live = self._live_usage(session, intent, run_log)
                        if live is not None:
                            yield {"type": "usage", "usage": live}
                        if called in _PLAN_TOOLS:
                            # 方案刚被改过，重读一份最新快照推给前端（全量、幂等；同一状态推一次）。
                            # 前端把它渲染成聊天里那条「方案」链接（就地展开全文），不再有独立面板；
                            # ``latest`` 为 ``None`` 也照推（``plan: null``），前端据此知道方案没了。
                            latest = await self._plan_snapshot(session)
                            if latest != last_plan:
                                last_plan = latest
                                if latest is not None:
                                    # 首次生成时落一条记录，之后同 plan_id 不再追加（见该函数）
                                    await self._record_plan_once(session, latest)
                                yield {"type": "plan", "plan": latest}
                elif method == "custom":
                    payload = payload_of(event)
                    if (
                        isinstance(payload, dict)
                        and payload.get("kind") == RUN_EVENT_KIND
                    ):
                        safe_run = _redact_run_payload(payload, terms)
                        if not (
                            safe_run.get("node_kind") == "model"
                            and (safe_run.get("data") or {}).get("part") == "reasoning"
                        ):
                            persistent_run_events.append(safe_run)
                        yield {"type": "run", "run": safe_run}
        except asyncio.CancelledError:
            # 用户点了「终止」：RunHub 把这一轮的 task 取消了。补一条记录（不补的话库里
            # 就只剩一条用户消息空着，刷新后看不出这轮出过事），再原样抛出 —— 上层靠
            # 它区分「用户终止」与「故障」。这条 await 是安全的：task.cancel() 只投递
            # 一次；除非期间又被取消一次，那时这条标记会丢（丢了也只是个标记）。
            usage = self._turn_usage(session, intent, run_log, interrupted=True)
            self._log_turn_cost(
                session, intent, usage,
                llm_calls=llm_calls, tool_calls=tool_calls, chars=len(text),
            )
            await self._record_interrupted(
                session, text, translator, intent, terms, usage=usage,
            )
            raise
        except TurnStop as stop:
            # 空转刹车（``middleware/guard.py``）：守卫抛出的信号，本轮到此为止。
            # 这里只把它落到 state 上，收尾在流外统一做 —— 那条路也兜住「异常被某层
            # 吃成了 ToolMessage、根本没抛到这里」的情形。
            state.aborted = stop
        except Exception as exc:
            # 下面那次 _record 在循环之外，异常一跳过它就永远不执行 —— 先补一条中断记录，
            # 再把异常原样抛给 chat()（error 帧的产生只留它那一处）。
            usage = self._turn_usage(session, intent, run_log, interrupted=True)
            changes = (
                await self._changes_for_round(session, pr_refs) if pr_refs else None
            )
            self._log_turn_cost(
                session, intent, usage,
                llm_calls=llm_calls, tool_calls=tool_calls, chars=len(text),
            )
            await self._record_interrupted(
                session, text, translator, intent, terms, exc,
                usage=usage, changes=changes,
            )
            raise

        if state.aborted is not None:
            # 提前收尾：这一轮没跑完，不能落到下面那条「智能体完成」与正常记录里
            stop = state.aborted
            usage = self._turn_usage(session, intent, run_log, interrupted=True)
            changes = (
                await self._changes_for_round(session, pr_refs) if pr_refs else None
            )
            self._log_turn_cost(
                session, intent, usage,
                llm_calls=llm_calls, tool_calls=tool_calls, chars=len(text),
            )
            await self._record_interrupted(
                session, text, translator, intent, terms,
                closing=stop.marker, extra=stop.extra,
                usage=usage, changes=changes,
            )
            # 标记的写法与记录里的一致（``_record_interrupted`` 用空行分隔），
            # 这样实时看到的东西和刷新后回放的一样
            if changes is not None:
                yield {"type": "changes", "changes": changes}
            yield {
                "type": "ai",
                "content": f"{text}\n\n{stop.marker}" if text else stop.marker,
            }
            return

        clarification_frame: dict[str, Any] | None = None
        if clarification is not None:
            parsed = parse_clarification(text, state.allowed_intents or None)
            if parsed is not None:
                clarification_frame = await self._save_clarification(
                    session,
                    original_message=message,
                    question=parsed.question,
                    options=[option.as_dict() for option in parsed.options],
                    steps=[step.as_dict() for step in parsed.steps],
                )
                if clarification_frame is not None:
                    text = parsed.question
            # 模型没按协议输出 / 落库失败：把攒着的文本补发，用户至少能看到回答。
            if clarification_frame is None and text:
                yield {"type": "ai", "content": text}

        if not text and clarification_frame is None:
            # 流事件只带模型原始 delta；中间件在 handler 返回后补的正文（例如
            # invalid tool call 的降级说明）没有对应 delta。正常结束后从最终
            # state 补发一次，避免“模型明明回复了，前端却是 0 字”。
            get_state = getattr(agent, "aget_state", None)
            if callable(get_state):
                try:
                    snapshot = await get_state(config)
                    final_messages = snapshot.values.get("messages") or []
                except Exception:
                    logger.warning("读取最终消息失败，跳过正文兜底", exc_info=True)
                else:
                    final_text = redact(last_ai_text(final_messages).strip(), terms)
                    if final_text:
                        text += final_text
                        translator.parts.add_text(final_text)
                        yield {"type": "ai", "content": final_text}

        logger.info(
            "智能体完成：public_id=%s intent=%s LLM %d 次 / 工具 %d 次 / 输出 %d 字",
            session.public_id, intent, llm_calls, tool_calls, len(text),
            extra={"agent": MAIN_AGENT},
        )
        usage = self._turn_usage(session, intent, run_log)
        self._log_turn_cost(
            session, intent, usage,
            llm_calls=llm_calls, tool_calls=tool_calls, chars=len(text),
        )
        extra: dict[str, Any] = {
            "intent": intent,
            "usage": usage,
        }
        if clarification_frame is not None:
            extra["clarification"] = clarification_frame
        # parts 只在真的出现过待办或 handoff run 树时才写；纯正文的回合 content 已够回放。
        if clarification_frame is None and (
            translator.parts.has_todos or persistent_run_events
        ):
            parts = _redacted_parts(translator.parts.data, terms)
            if persistent_run_events:
                parts.append({"kind": "handoff", "events": persistent_run_events})
            extra["parts"] = parts
        # 「这一轮改了什么」：只有本轮**建过 PR** 才算数（用户口径：这就是「写完 + 提了 PR」的场景）。
        # 算不出来（本地没缓存过这个仓库 / 缺分支）就整块不出现 —— 宁可不显示，也不显示半份。
        changes = await self._changes_for_round(session, pr_refs) if pr_refs else None
        if changes is not None:
            extra["changes"] = changes
        await self._record(
            session, type="assistant", summary=text[:200], content=text, extra=extra,
        )
        if clarification_frame is not None:
            yield {"type": "clarification", "clarification": clarification_frame}
        if changes is not None:
            yield {"type": "changes", "changes": changes}
        # 最后把这一轮的账推给前端：**落在正文之后**，这样它贴在回复末尾（前端按顺序处理帧），
        # 也不必让用户刷新页面才看到本轮的消耗与上下文压力。落库那份与这一帧同源。
        yield {"type": "usage", "usage": extra["usage"]}
