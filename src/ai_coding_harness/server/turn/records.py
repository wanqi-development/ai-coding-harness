"""Server 轮次编排：records。"""


import logging
from collections.abc import Mapping
from typing import Any

from ai_coding_harness.agent.middleware import refresh_memory
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.core.stream import (
    V3Translator,
)
from ai_coding_harness.db import dao
from ai_coding_harness.safety.redact import (
    redact,
)
from ai_coding_harness.session.manager import (
    Session,
    SessionRuntime,
)
from ai_coding_harness.tools.git import PrLookupError, build_git_client

logger = logging.getLogger(__name__)

from .common import (
    CANCELLED_BY_USER,
    INTERRUPTED_EMPTY,
    INTERRUPTED_PARTIAL,
    _redacted_parts,
    _title_from,
    plan_payload,
)


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

    async def _plan_snapshot(self, session: Session) -> dict[str, Any] | None:
        """读该会话最新方案并序列化；无方案或读库失败返回 None（不打断对话）。"""
        try:
            async with self._db.session() as db:
                plan = await dao.get_latest_plan(db, session_id=session.db_id)
        except Exception:
            logger.exception("读方案失败（不影响主流程）")
            return None
        return None if plan is None else plan_payload(plan)
