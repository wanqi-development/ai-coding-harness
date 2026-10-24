"""方案执行状态回收：before_agent 修复遗留，after_agent 收口正常结束。"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.db import Database, dao
from ai_coding_harness.tools.context import get_configurable

logger = logging.getLogger(__name__)


class PlanRecoveryMiddleware(AgentMiddleware):
    """只处理 ``executing`` 方案，按本轮是否存在 PR 决定完成还是恢复待执行。

    - before_agent：新一轮进入 coding / planner / coder 时，回收上一轮遗留的 executing；
    - after_agent：coder 正常结束但忘记回写时，立即收口。

    ``CancelledError`` 不会走 after_agent；这种情况由下一轮 before_agent 修复。
    """

    def __init__(self, *, database: Database, session_id: int, role: str) -> None:
        self._database = database
        self._session_id = session_id
        self._role = role

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        await self._reclaim(reason="before_agent")
        return None

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        await self._reclaim(reason="after_agent")
        return None

    async def _reclaim(self, *, reason: str) -> None:
        turn_state = get_configurable("turn_state")
        if not isinstance(turn_state, TurnState):
            return
        try:
            async with self._database.session() as db:
                plan_id = await dao.reclaim_executing_plan(
                    db,
                    session_id=self._session_id,
                    has_pr=turn_state.has_pr,
                    plan_id=turn_state.plan_id,
                )
        except Exception:
            logger.exception(
                "方案状态回收失败：role=%s session_id=%s reason=%s",
                self._role,
                self._session_id,
                reason,
            )
            return
        if plan_id is not None:
            logger.warning(
                "回收 executing 方案：role=%s session_id=%s plan_id=%s has_pr=%s "
                "target=%s reason=%s",
                self._role,
                self._session_id,
                plan_id,
                turn_state.has_pr,
                "done" if turn_state.has_pr else "pending",
                reason,
            )
