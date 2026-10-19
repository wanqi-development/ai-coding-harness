"""委派闸门：主控派子智能体之前，必须先落一份本轮清单。

缘起：2026-10-19 真机上主控频繁直接 ``task`` —— 子智能体在跑，用户却看不到这一轮
准备做哪几步，出了问题也没有可对照的清单。提示词里写过「先写清单」，但这类**顺序**
约束只有放到执行前才拦得住。

判据是**成功执行过** ``write_todos``，不是「发起过」：它自己也可能被边界中间件拒掉、
被循环守卫判重、或者工具本身报错，那些都不该开门（``ToolMessage(status="error")``
不算成功）。成功后的记账落在 ``TurnState`` 上，与循环计数共用同一份本轮状态。

只挂在主控上（``agent/graph.py``）：三个子智能体没有 ``task`` 可调，挂上去只是多一层
空包装。也正因为如此，这里不再需要「按角色分账」那套判断。

被拦下时返回 ``ToolMessage(status="error")`` 而不是抛异常：模型看得到理由、下一步能自己
补清单，图也不会因为一次被拒的调用塌掉 —— 与 ``boundary.py`` / ``guard.py`` 同一个取舍。

这一层必须挂在 ``LoopGuardMiddleware`` **外侧**（``middleware=`` 列表里更靠前）：被拦下的
``task`` 不计入重复次数，模型补完清单后第一次派发仍是第 1 次。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ai_coding_harness.agent.middleware.guard import TurnState
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.stream import TASK_TOOL, TODO_TOOL
from ai_coding_harness.tools.context import get_configurable

logger = logging.getLogger(__name__)


class DelegationGateMiddleware(AgentMiddleware):
    """主控专用：本轮没有成功写过 ``write_todos`` 就不许调用 ``task``。"""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        state = get_configurable("turn_state")
        if not isinstance(state, TurnState):
            return await handler(request)

        call = request.tool_call
        name = str(call.get("name") or "")

        if name == TODO_TOOL:
            result = await handler(request)
            if _tool_succeeded(result):
                state.mark_tool_success(name, MAIN_AGENT)
            return result

        if name == TASK_TOOL and not state.has_successful_tool(TODO_TOOL, MAIN_AGENT):
            logger.warning(
                "委派闸门拦下 `task`：本轮还没有成功调用 `write_todos`，不派子智能体",
                extra={"agent": MAIN_AGENT},
            )
            return ToolMessage(
                content=(
                    "本轮还没有成功调用 `write_todos`。先调用 `write_todos`，"
                    "把这一轮要做的步骤列成清单，再调用 `task` 派子智能体；"
                    "本次 `task` 未执行。"
                ),
                name=name,
                tool_call_id=call.get("id") or "",
                status="error",
            )

        return await handler(request)


def _tool_succeeded(result: Any) -> bool:
    """工具成功才给 ``write_todos`` 记账；``ToolMessage(status="error")`` 不算。"""
    return not (isinstance(result, ToolMessage) and result.status == "error")
