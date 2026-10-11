"""Server 服务层：意图分析 → 直接执行 / 主智能体；并把过程记录到业务库。

API 路由只做 HTTP 转换，业务逻辑集中在这一层。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from ai_coding_harness.agent.llm import build_classifier_model
from ai_coding_harness.agent.middleware import TurnState, TurnStop, refresh_memory
from ai_coding_harness.agent.profiles import (
    ANALYSIS_FORM,
    ORCHESTRATE_FORM,
    REVIEW_FORM,
    profile_for,
)
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database, dao
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.observability.run_log import AgentRunLog
from ai_coding_harness.redact import (
    CRED_TERM,
    detect_credential,
    redact,
    secret_terms,
)
from ai_coding_harness.server.intent import (
    DIRECT_INTENTS,
    IntentDecider,
    IntentDecision,
    detect_intent,
)
from ai_coding_harness.session.manager import (
    Session,
    SessionManager,
    SessionRuntime,
    WorkspaceError,
)
from ai_coding_harness.stream import (
    TASK_TOOL,
    ToolNames,
    V3Translator,
    call_id_of,
    event_name,
    method_of,
    payload_of,
    subagent_name,
)
from ai_coding_harness.tools.git import PrLookupError, build_git_client

logger = logging.getLogger(__name__)


_TITLE_MAX = 30

# 代码工作区建不出来时对用户说的话（原始异常只进日志与 sessions.workspace_error）
WORKSPACE_ERROR_MESSAGE = "代码工作区创建异常，请重试"

# 会改动方案的三个工具：调完就该把最新方案推给前端刷新进度面板。
# 会改动方案的那几个：跑完就重读一份快照推给前端（幂等，同一状态只推一帧）。
# ``getPlan`` 不在里面 —— 它只读，读了不该动面板。
_PLAN_TOOLS = frozenset(
    {
        "storePlan",
        "updatePlan",
        "updatePlanSteps",
        "deletePlan",
        "updatePlanStep",
        "updatePlanStatus",
    }
)

#: 收尾时该给仓库记忆打底的形态 —— 与「谁能写记忆正文」是同一批（``orchestrate`` 上的
#: coding / rework、``review``、``analysis``）：这几轮才有要沉淀的事实（改动 / 审查结论 /
#: 仓库现状），其余形态只读注入（平台侧的刷新由记忆中间件在开工前做掉）。
_MEMORY_WRITE_FORMS: frozenset[str] = frozenset(
    {ORCHESTRATE_FORM, REVIEW_FORM, ANALYSIS_FORM}
)

# 一轮跑到一半炸了时的标记：已经有正文就说明上面是部分输出，一条都没有就是未产出。
INTERRUPTED_PARTIAL = "〔本轮中断，以上为部分输出〕"
INTERRUPTED_EMPTY = "〔本轮中断，未产出内容〕"
# 用户点了「终止」：不是故障，措辞里不出现异常名（前端本地也补同一句，刷新前后一致）
CANCELLED_BY_USER = "〔已由用户终止〕"


def plan_payload(plan: Any) -> dict[str, Any]:
    """把方案行序列化成前端要的快照（SSE 的 plan 事件与 GET plan 接口共用）。

    ``steps`` 容忍历史脏数据（整列不是 list、元素不是 dict）：跳掉渲染不了的条目，
    而不是让一个坏步骤把整个面板打挂。
    """
    raw_steps = plan.steps if isinstance(plan.steps, list) else []
    steps = [
        {
            "index": step.get("index", i),
            "goal": str(step.get("goal") or ""),
            "status": step.get("status", "pending"),
            "files": step.get("files"),
            "done_criteria": step.get("done_criteria"),
        }
        for i, step in enumerate(raw_steps, 1)
        if isinstance(step, dict)
    ]
    return {
        "plan_id": plan.id,
        "title": plan.title,
        "status": plan.status,
        "steps": steps,
        "content": plan.content,
        "updated_at": plan.updated_at.isoformat(),
    }


def _redacted_todos(items: list[dict], terms: tuple[str, ...]) -> list[dict]:
    """待办树脱敏：主项和它的 children 都得过，漏一层等于没做。

    子项只保留 content / status 两个键（树只有两级，children 不再往下递归）。
    """
    safe: list[dict] = []
    for item in items:
        clean: dict[str, Any] = {"content": redact(item["content"], terms), "status": item["status"]}
        children = item.get("children")
        if children:
            clean["children"] = [
                {"content": redact(kid["content"], terms), "status": kid["status"]}
                for kid in children
            ]
        safe.append(clean)
    return safe


def _redacted_parts(parts: list[dict], terms: tuple[str, ...]) -> list[dict]:
    """落库前把每个块过一遍脱敏。

    翻译器手上那份是**未脱敏**的原文（脱敏统一在出口做，免得同一份文本被过滤两遍、
    口径还不一致）——SSE 出口已经过了一道，落库这道不能省。
    """
    safe: list[dict] = []
    for part in parts:
        if part["kind"] == "text":
            safe.append({"kind": "text", "content": redact(part["content"], terms)})
        else:
            safe.append({"kind": "todos", "items": _redacted_todos(part["items"], terms)})
    return safe


def _title_from(message: str) -> str:
    """会话标题取首条用户消息：压成单行（含换行/多空格）再截断。"""
    line = " ".join(message.split())
    return line[:_TITLE_MAX] + ("…" if len(line) > _TITLE_MAX else "")


# 入口拒收的回执：只说命中了哪一类，绝不复述命中的内容（回执本身也要下发与落库）
_REFUSAL_HINTS = {
    CRED_TERM: "请把凭据换成占位符再发。",
    "赋值字面量": "如果是在贴代码，请把字面量换成读环境变量的写法（如 os.getenv）。",
}
_REFUSAL_HINT = "沙箱里的凭据由平台注入，不需要贴进对话。"


def credential_refusal(label: str) -> str:
    """含凭据消息的拒收回执。"""
    return (
        f"这条消息里检测到疑似凭据（{label}），已拒绝执行，也没有发送给模型。"
        f"{_REFUSAL_HINTS.get(label, _REFUSAL_HINT)}"
    )


class HarnessService:
    """会话编排：意图分类、直接执行、主智能体调用、业务库记录。"""

    def __init__(
        self,
        *,
        settings: Settings,
        manager: SessionManager,
        database: Database,
        intent_jev: IntentDecider | None = None,
    ) -> None:
        self._settings = settings
        self._manager = manager
        self._db = database
        #: 判定瀑布的第二关（口令 → **JEV** → 大模型 → 关键字口径）。``None`` ＝ 没配 / SDK
        #: 装不上，这一关直接跳过 —— 判定器缺一层不该影响服务可用性。
        self._intent_jev = intent_jev
        # 分类模型懒建一次即可：每会话每轮都建一个新实例会攒下未关闭的连接
        self._classifier: BaseChatModel | None = None

    # -- 意图判定 -----------------------------------------------------------
    def _classifier_for(self) -> BaseChatModel | None:
        """分类模型实例；未启用或构建失败时返回 None（退回纯关键字判定）。"""
        if not self._settings.intent_llm_enabled:
            return None
        if self._classifier is None:
            try:
                self._classifier = build_classifier_model(self._settings)
            except Exception:
                logger.exception("意图分类模型构建失败，退回关键字判定")
                return None
        return self._classifier

    async def _intent_history(self, session: Session) -> list[str]:
        """最近几轮用户消息（由远及近）。读库失败只降级成「不带上下文」，不打断本轮。

        本轮的这条消息**不在**里面 —— 调用点还没落库。历史只读这一次，两个判定
        （常规判定与 ``handoffTask`` 的重判）与交接给内层的消息都吃同一份。
        """
        if self._settings.intent_history_turns <= 0:
            return []
        try:
            async with self._db.session() as db:
                return await dao.recent_user_summaries(
                    db,
                    session_id=session.db_id,
                    limit=self._settings.intent_history_turns,
                )
        except Exception:
            logger.exception("读最近用户消息失败（分类将不带上下文）")
            return []

    async def _detect_intent(
        self, message: str, history: Sequence[str]
    ) -> IntentDecision:
        """判定本轮意图：口令 → JEV → 大模型（都带最近几轮用户消息）→ 关键字口径。"""
        return await detect_intent(
            message,
            history=history,
            jev=self._intent_jev,
            model=self._classifier_for(),
            timeout=self._settings.intent_timeout_seconds,
        )

    async def _rejudge_intent(
        self, message: str, history: Sequence[str]
    ) -> IntentDecision:
        """跳过口令层重判一次意图（``handoffTask`` 用，签名与它约定好）。

        与 ``_detect_intent`` 只差 ``force_llm=True``：智能体自述做不了时，要纠正的
        往往正是口令层（一句「实施」就把意图钉死），所以这一层不看关键字。模型不可用
        仍是回落，来源会标 ``fallback`` —— 调用方（工具）据此拒绝交接。
        """
        return await detect_intent(
            message,
            history=history,
            jev=self._intent_jev,
            model=self._classifier_for(),
            timeout=self._settings.intent_timeout_seconds,
            force_llm=True,
        )

    # -- 对外：处理一条用户消息，产出事件流 ---------------------------------
    async def chat(self, session: Session, message: str) -> AsyncIterator[dict[str, Any]]:
        """处理用户消息。

        事件形如 ``{"type": "ai"|"tool"|"plan"|"done"|"error", ...}``，
        由 API 层序列化为 SSE。

        出口（进状态前、落日志前、下发前、直接执行返回前）各拦一道，词表在这里算一次，
        按会话取 —— 令牌是每会话一份，不能进模块级常量。此外入口先做一次**拒收**：
        消息里含凭据就整条不收，凭据连模型都不发（见 ``detect_credential``）。

        会话这个日志维度**不在这里设** —— 设在 ``api/session_lookup.resolve_session``：
        每个会话级请求都从那儿过，连 ``/workspace`` 触发的 provisioning 也算这个会话的日志。
        """
        terms = secret_terms(session.repo.token, self._settings.gitee_access_token)
        label = detect_credential(message, terms)
        if label is not None:
            # 拒收在建模之前：意图分类那一跳也会把消息发给模型，晚一步就白拦了
            await self._reject_credential(session, message, label, terms)
            yield {"type": "ai", "content": credential_refusal(label)}
            yield {"type": "done"}
            return
        history = await self._intent_history(session)
        decision = await self._detect_intent(message, history)
        intent = decision.intent
        logger.info(
            "意图分析：intent=%s source=%s 理由=%s public_id=%s",
            intent, decision.source, decision.reason or "-", session.public_id,
        )
        await self._record(
            session,
            type="user",
            summary=message[:200],
            content=message,
            extra={"intent": intent, "intent_source": decision.source},
        )
        await self._name_session(session, message)

        # 每轮一份运行时状态：循环守卫拿它数重复，``handoffTask`` 拿它重判意图并把原话
        # 交给另一个形态（分类模型与超时都在这一层，所以回调也从这里给）。
        # 智能体是长期存活对象，逐轮变化的东西只能经 config 注入。
        state = TurnState(
            message=message,
            history=history,
            repeats=self._settings.loop_guard_repeats,
            rejudge=self._rejudge_intent,
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
            if intent in DIRECT_INTENTS:
                # 可直接执行的任务：不走智能体，直接返回（这条路径中间件够不着，得自己拦）
                result = redact(
                    await self._execute_direct(session, runtime, intent, message), terms
                )
                await self._record(
                    session, type="assistant", summary=result[:200], content=result,
                    extra={"intent": intent, "direct": True},
                )
                yield {"type": "ai", "content": result}
            else:
                async for event in self._run_agent(
                    session, runtime, message, intent, terms, state=state
                ):
                    yield event
                # 轮末打底：coding / rework / review / analysis 这几轮才有要沉淀的事实，
                # 按 Gitee 实况再刷一遍。
                # 放在这一层（而不是 ``_run_agent`` 里）有三个好处：中止与正常收尾两条
                # 出口都覆盖得到、``_run_agent`` 只管事件流不新增依赖、只读轮不查 PR。
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

    # -- 直接执行（sync）--------------------------------------------------
    async def _execute_direct(
        self, session: Session, runtime: SessionRuntime, intent: str, message: str
    ) -> str:
        if intent == "sync":
            return await self._direct_sync(session, runtime)
        return "（无对应的直接执行处理器）"

    async def _direct_sync(self, session: Session, runtime: SessionRuntime) -> str:
        """同步仓库：不存在 clone，已存在 pull。"""
        repo_dir = f"{self._settings.sandbox_project_dir}/{session.repo.repo}"
        exists = await runtime.sandbox.run(f"test -d {repo_dir}/.git && echo yes || echo no")
        if "yes" in exists.stdout:
            result = await runtime.sandbox.run(f"git -C {repo_dir} pull")
            action = "git pull"
        else:
            result = await runtime.sandbox.run(
                f"git clone {session.repo_url} {repo_dir}"
            )
            action = "git clone"
        out = (result.stdout or result.stderr or "").strip()
        return f"已同步仓库（{action}，exit={result.exit_code}）：\n{out[-1000:]}"

    # -- 主智能体（intent 作为 config 传入）-------------------------------
    async def _run_agent(
        self,
        session: Session,
        runtime: SessionRuntime,
        message: str,
        intent: str,
        terms: tuple[str, ...],
        *,
        state: TurnState,
    ) -> AsyncIterator[dict[str, Any]]:
        # 装配形态按本轮意图定：qa / analysis 只读，plan / review 单智能体（主控自己出
        # 方案 / 自己审），coding 与 rework 挂三个子智能体（两者共用一套装配，表见
        # agent/profiles.py）。同一会话换形态不影响历史 —— thread 是共享的。
        agent = self._manager.get_agent(runtime, session, profile_for(intent))
        config = RunnableConfig(
            configurable={
                "thread_id": self._manager.thread_id(session),
                "intent": intent,
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
        run_log = AgentRunLog(
            arg_limit=self._settings.log_tool_arg_max,
            result_limit=self._settings.log_tool_result_max,
            terms=terms,
        )
        translator = V3Translator()
        names = ToolNames()
        # task 的 tool_call_id → 派的是哪个子智能体。只为一件事：工具帧上带角色，前端
        # 才说得出「正在编码」而不是笼统的「正在处理子任务」。收尾那帧也得带 —— 不带的话
        # 子智能体一返回，提示就翻回泛化文案，而主智能体还要想几秒才开口。
        delegated: dict[str, str] = {}

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
                    # 正文与待办树都走这里；子智能体的正文在 translate 里被丢掉，只有它的
                    # write_todos 会作为子任务挂进树里
                    for part in translator.feed(event):
                        if part["type"] == "ai":
                            # 先脱敏再累加：这一处同时管住 SSE、业务库记录与首段摘要
                            answer = redact(part["content"], terms)
                            if answer:
                                text += answer
                                yield {"type": "ai", "content": answer}
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
                        frame = {"type": "tool", "name": called, "content": ""}
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
                        frame = {
                            "type": "tool",
                            "name": called,
                            # 中间件已把进状态的返回值脱敏，这里再兜一道：工具事件的
                            # output 与进 state 的那份是否同一个对象没验证过
                            "content": redact(
                                str((payload_of(event) or {}).get("output", "")), terms
                            )[:500],
                        }
                        role = delegated.pop(call_id_of(event), "")
                        if role:
                            frame["agent"] = role
                        yield frame
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
        except asyncio.CancelledError:
            # 用户点了「终止」：RunHub 把这一轮的 task 取消了。补一条记录（不补的话库里
            # 就只剩一条用户消息空着，刷新后看不出这轮出过事），再原样抛出 —— 上层靠
            # 它区分「用户终止」与「故障」。这条 await 是安全的：task.cancel() 只投递
            # 一次；除非期间又被取消一次，那时这条标记会丢（丢了也只是个标记）。
            await self._record_interrupted(session, text, translator, intent, terms)
            raise
        except TurnStop as stop:
            # 空转刹车（``middleware/guard.py``）：守卫抛出的信号，本轮到此为止。
            # 这里只把它落到 state 上，收尾在流外统一做 —— 那条路也兜住「异常被某层
            # 吃成了 ToolMessage、根本没抛到这里」的情形。
            state.aborted = stop
        except Exception as exc:
            # 下面那次 _record 在循环之外，异常一跳过它就永远不执行 —— 先补一条中断记录，
            # 再把异常原样抛给 chat()（error 帧的产生只留它那一处）。
            await self._record_interrupted(session, text, translator, intent, terms, exc)
            raise

        if state.aborted is not None:
            # 提前收尾：这一轮没跑完，不能落到下面那条「智能体完成」与正常记录里
            stop = state.aborted
            await self._record_interrupted(
                session, text, translator, intent, terms,
                closing=stop.marker, extra=stop.extra,
            )
            # 标记的写法与记录里的一致（``_record_interrupted`` 用空行分隔），
            # 这样实时看到的东西和刷新后回放的一样
            yield {
                "type": "ai",
                "content": f"{text}\n\n{stop.marker}" if text else stop.marker,
            }
            return

        logger.info(
            "智能体完成：public_id=%s intent=%s LLM %d 次 / 工具 %d 次 / 输出 %d 字",
            session.public_id, intent, llm_calls, tool_calls, len(text),
            extra={"agent": MAIN_AGENT},
        )
        extra: dict[str, Any] = {"intent": intent}
        # parts 只在真的出现过待办时才写：纯正文的回合，content 已经够前端回放了
        if translator.parts.has_todos:
            extra["parts"] = _redacted_parts(translator.parts.data, terms)
        await self._record(
            session, type="assistant", summary=text[:200], content=text, extra=extra,
        )

    # -- 仓库记忆 ----------------------------------------------------------
    async def _refresh_repo_memory(
        self, session: Session, runtime: SessionRuntime
    ) -> None:
        """轮末按 Gitee 的实际 PR 状态刷新仓库记忆（coding / review / analysis 收尾时的打底）。

        与启动闸门（``middleware/memory.py`` 的 ``refresh_memory``）是同一件事的
        两次机会，读→对账→写回与「查不到就不写」的纪律都复用那一个函数。打底只补事实
        （PR 编号 / 分支 / 状态），不替模型写散文 —— 散文由 ``save_repo_memory`` 负责。

        **失败不改本轮结论。** 记忆是副产物：查不到 PR 也好、写盘报错也好，都只记日志，
        交给下一轮（或下一次 ``save_repo_memory``）再刷。同理不建新记忆，只刷已存在的
        那份（见 ``refresh_memory``）。

        判据是**形态**（coding / review / analysis 即「这一轮有要沉淀的事实」），
        不是「本轮建出过 PR」—— 后者要对工具返回文案做字符串匹配，改一次文案就会静默失效。
        """
        path = memory_path(
            session.repo.owner, session.repo.repo, self._settings.memories_prefix
        )
        try:
            # 刷没刷由 refresh_memory 自己记（成功 info、失败 warning）
            await refresh_memory(
                runtime.backend,
                path,
                build_git_client(self._settings, session.repo).all_pulls,
                session.repo,
            )
        except PrLookupError as exc:
            logger.warning("查询仓库 PR 失败，仓库记忆本轮未刷新：%s", exc)
        except Exception:
            logger.exception("刷新仓库记忆失败（不影响本轮结论）")

    # -- 业务库记录 --------------------------------------------------------
    async def _plan_snapshot(self, session: Session) -> dict[str, Any] | None:
        """读该会话最新方案并序列化；无方案或读库失败返回 None（不打断对话）。"""
        try:
            async with self._db.session() as db:
                plan = await dao.get_latest_plan(db, session_id=session.db_id)
        except Exception:
            logger.exception("读方案失败（不影响主流程）")
            return None
        return None if plan is None else plan_payload(plan)

    async def _name_session(self, session: Session, message: str) -> None:
        """用首条用户消息命名会话（已有标题的会话不受影响）。"""
        try:
            async with self._db.session() as db:
                await dao.set_session_title_if_empty(
                    db, session.db_id, _title_from(message)
                )
        except Exception:
            logger.exception("写会话标题失败（不影响主流程）")

    async def _record(
        self,
        session: Session,
        *,
        type: str,
        summary: str | None = None,
        ref_id: int | None = None,
        content: str | None = None,
        extra: dict | list | None = None,
    ) -> None:
        try:
            async with self._db.session() as db:
                await dao.add_record(
                    db, session_id=session.db_id, type=type,
                    summary=summary, ref_id=ref_id, content=content, extra=extra,
                )
        except Exception:
            logger.exception("写会话记录失败（不影响主流程）")

    async def _record_plan_once(self, session: Session, plan: dict[str, Any]) -> None:
        """把「方案已生成」写进聊天流（``type='plan'``），**同一份方案只写一条**。

        方案落地后会被 ``updatePlan`` / ``updatePlanSteps`` 反复改；每次改都追加一条记录，
        聊天流就会变成「方案已更新」的流水。所以按 ``plan_id`` 去重，判据放在库里
        （``dao.plan_record_exists``）—— 一次新的运行里内存标记从零起步，只靠内存会重复写。

        记录里存**方案全文**：前端那条链接点开就地展开，不必再补一次请求；刷新页面走
        ``/records`` 拿到的也是同一份内容。
        """
        plan_id = int(plan["plan_id"])
        try:
            async with self._db.session() as db:
                if await dao.plan_record_exists(
                    db, session_id=session.db_id, plan_id=plan_id
                ):
                    return
        except Exception:
            logger.exception("查方案记录失败（不影响主流程）")
            return
        await self._record(
            session,
            type="plan",
            summary=plan.get("title") or "实施计划",
            ref_id=plan_id,
            content=plan.get("content"),
            extra={
                "plan_id": plan_id,
                "status": plan.get("status"),
                "steps": plan.get("steps") or [],
            },
        )

    async def _record_interrupted(
        self,
        session: Session,
        text: str,
        translator: V3Translator,
        intent: str,
        terms: tuple[str, ...],
        exc: BaseException | None = None,
        *,
        closing: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """一轮跑到一半断了：把已产出的部分补一条记录，别让它在历史里凭空消失。

        正文与异常文本都过脱敏（异常文本常把工具返回值整段带上），原文只进日志。
        标记与错误都写进正文，前端收到 error 帧时也是把错误文本追加在正文后面
        （``App.vue``），这样刷新后按记录回放看到的东西跟实时那次一样。

        没有正文时也记 —— 否则时间线上就是一条用户消息后面空着，看不出这轮出过事。
        「未产出内容」的判据是**用户看到过东西没有**：正文或待办树都算，只有待办时
        标记得说部分输出，说「未产出内容」会和上面那个待办框自相矛盾。

        ``exc=None`` 走的是**非故障**那条路（取消，或循环守卫中止本轮）：措辞与错误分开
        写，「错误：CancelledError」对用户毫无意义；有没有产出也不分两句 —— 中止就是中止。
        这条路默认的措辞是用户终止，另外两种情形用 ``closing`` 换措辞、``extra`` 换
        ``extra`` 里的分流标记（见 ``middleware/guard.py`` 的 ``TurnStop``）。
        """
        has_todos = translator.parts.has_todos
        if exc is None:
            head = note = closing or CANCELLED_BY_USER
            marker: dict[str, Any] = dict(extra) if extra else {"cancelled": True}
        else:
            safe_error = redact(str(exc), terms)
            head = INTERRUPTED_PARTIAL if (text or has_todos) else INTERRUPTED_EMPTY
            note = f"{head}\n错误：{safe_error}"
            marker = {
                "error": {"type": type(exc).__name__, "message": safe_error},
            }
        # 每条都是「这一条不是完整的回答」，故 partial 都在；靠 cancelled / aborted / error 再分家
        fields: dict[str, Any] = {"intent": intent, "partial": True, **marker}
        if has_todos:
            # 有 parts 时前端按 parts 回放、content 整段被忽略，标记得补进块序列尾部
            fields["parts"] = [
                *_redacted_parts(translator.parts.data, terms),
                {"kind": "text", "content": note},
            ]
        await self._record(
            session,
            type="assistant",
            summary=text[:200] or head,
            content=f"{text}\n\n{note}" if text else note,
            extra=fields,
        )
