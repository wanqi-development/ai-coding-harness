"""Server 轮次编排：records。"""


import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ai_coding_harness.agent.llm import build_model
from ai_coding_harness.agent.middleware.context_inject import now_stamp
from ai_coding_harness.backends.composite import memory_path, memory_store_key
from ai_coding_harness.core.stream import (
    V3Translator,
)
from ai_coding_harness.db import dao
from ai_coding_harness.safety.redact import (
    redact,
)
from ai_coding_harness.server.repo_memory_sync import (
    PrMemorySummary,
    sync_repo_memory,
)
from ai_coding_harness.server.repo_state import load_pull_snapshot
from ai_coding_harness.session.manager import (
    Session,
    SessionRuntime,
)
from ai_coding_harness.tools.git import PrLookupError
from ai_coding_harness.tools.pr_client import pull_summary

logger = logging.getLogger(__name__)

from .common import (
    CANCELLED_BY_USER,
    INTERRUPTED_EMPTY,
    INTERRUPTED_PARTIAL,
    _redacted_parts,
    _title_from,
    plan_payload,
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse_pr_summaries(text: str) -> dict[int, PrMemorySummary]:
    """解析模型给出的一次性 PR 摘要；坏条目跳过，调用方再按标题兜底。"""
    raw = _FENCE.sub("", text.strip())
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    items = value.get("items") if isinstance(value, dict) else value
    if not isinstance(items, list):
        return {}
    out: dict[int, PrMemorySummary] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            continue
        kind = str(item.get("kind") or "修改").strip()
        if kind not in {"新增", "修改", "删除", "修复"}:
            kind = "修改"
        summary = str(item.get("summary") or "").strip()[:120]
        if number > 0 and summary:
            out[number] = PrMemorySummary(kind, summary)
    return out


class TurnRecordsMixin:
    """records 相关方法（由 HarnessService 组合）。"""

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
        usage: dict[str, Any] | None = None,
        changes: Mapping[str, Any] | None = None,
        elapsed_ms: int | None = None,
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
        if usage is not None:
            # 中断的轮次同样入账（钱真花了），``usage.interrupted`` 已标成真
            fields["usage"] = usage
        if elapsed_ms is not None:
            fields["elapsed_ms"] = max(0, int(elapsed_ms))
        if changes is not None:
            # 中止前已经建成的 PR 仍要展示改动（2026-10-20：reviewer 触发循环守卫后
            # 整块「本轮改动」消失，用户以为功能没了）
            fields["changes"] = changes
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

    async def _name_session(self, session: Session, message: str) -> None:
        """用首条用户消息命名会话（已有标题的会话不受影响）。"""
        try:
            async with self._db.session() as db:
                await dao.set_session_title_if_empty(
                    db, session.db_id, _title_from(message)
                )
        except Exception:
            logger.exception("写会话标题失败（不影响主流程）")

    async def _summarize_new_pulls(
        self, session: Session, pulls: tuple[dict[str, Any], ...]
    ) -> dict[int, PrMemorySummary]:
        """用一次模型调用批量摘要本轮新出现的 PR。"""
        user_id = getattr(session, "user_id", None)
        user_key = int(user_id or 0)
        model = self._memory_models.get(user_key)
        if model is None:
            model = build_model(self._settings, role="qa", user_id=user_id)
            self._memory_models[user_key] = model
        compact = []
        for pull in pulls:
            summary = pull_summary(pull)
            compact.append(
                {
                    "number": summary["number"],
                    "title": summary["title"],
                    "state": summary["state"],
                    "branch": summary["branch"],
                    "url": summary["url"],
                    "body": str(pull.get("body") or "").strip()[:800],
                }
            )
        reply = await model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你在维护仓库记忆。根据给定的新 PR，为每个 PR 生成一条中文摘要。"
                        "只输出 JSON 数组，不要 Markdown。格式："
                        '[{"number": 12, "kind": "新增|修改|删除|修复", '
                        '"summary": "不超过80字的一行摘要"}]。'
                        "kind 根据 PR 标题和正文判断；无法判断时用“修改”。"
                    )
                ),
                HumanMessage(
                    content=json.dumps(compact, ensure_ascii=False)
                ),
            ]
        )
        content = getattr(reply, "content", "")
        return _parse_pr_summaries(content if isinstance(content, str) else str(content))

    async def _sync_memory(
        self,
        session: Session,
        backend: Any,
        path: str,
        pulls: tuple[dict[str, Any], ...],
    ):
        """共享写入口：新 PR 摘要 + 状态对账 + 真实落盘。"""
        return await sync_repo_memory(
            backend,
            path,
            owner=session.repo.owner,
            repo=session.repo.repo,
            pulls=pulls,
            now=now_stamp(),
            summarize=lambda new_pulls: self._summarize_new_pulls(session, new_pulls),
        )

    async def _sync_repo_memory_before_turn(
        self, session: Session, pulls: tuple[dict[str, Any], ...]
    ) -> Any:
        """每轮拦截点：在沙箱准备前直接读写持久 Store。"""
        if self._memory_backend is None:
            return None
        path = memory_store_key(
            session.repo.platform.value,
            session.repo.owner,
            session.repo.repo,
            self._settings.memories_prefix,
        )
        result = await self._sync_memory(
            session, self._memory_backend, path, pulls
        )
        logger.info(
            "仓库记忆轮前同步：new=%s restated=%s pending=%s changed=%s",
            result.new_prs,
            result.restated,
            [pull.number for pull in result.pending],
            result.changed,
        )
        return result

    async def _refresh_repo_memory(
        self, session: Session, runtime: SessionRuntime
    ) -> None:
        """coding / rework 轮末再查一次 PR 并走同一套记忆同步。"""
        path = memory_path(
            session.repo.platform.value,
            session.repo.owner,
            session.repo.repo,
            self._settings.memories_prefix,
        )
        try:
            settings = self._manager.settings_for(session)
            snapshot = await load_pull_snapshot(
                settings,
                session.repo,
                database=self._db,
                session_id=session.db_id,
            )
            result = await self._sync_memory(
                session, runtime.backend, path, snapshot.pulls
            )
            logger.info(
                "仓库记忆轮末同步：new=%s restated=%s pending=%s changed=%s",
                result.new_prs,
                result.restated,
                [pull.number for pull in result.pending],
                result.changed,
            )
        except PrLookupError as exc:
            logger.warning("查询仓库 PR 失败，仓库记忆本轮未刷新：%s", exc)
        except Exception:
            logger.exception("刷新仓库记忆失败（不影响本轮结论）")

    async def _plan_snapshot(self, session: Session) -> dict[str, Any] | None:
        """读该会话最新方案并序列化；无方案或读库失败返回 None（不打断对话）。"""
        try:
            async with self._db.session() as db:
                plan = await dao.get_latest_plan(db, session_id=session.db_id)
        except Exception:
            logger.exception("读方案失败（不影响主流程）")
            return None
        return None if plan is None else plan_payload(plan)
