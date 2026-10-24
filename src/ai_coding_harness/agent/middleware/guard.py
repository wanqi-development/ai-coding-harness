"""空转刹车：同一工具、同一参数在本轮反复调用时，先劝、再停。

缘起：2026-09-21 一轮编码任务跑了 686 秒，日志里从头到尾是 ``execute git status`` 与
同一份 ``write_todos`` —— 模型每一圈都得到「同样的输入、同样的输出」，却把这当成进展
接着转。提示词层面的「别重复」拦不住这种循环，得在执行前数。

计的是**本轮出现次数**，不要求相邻：那次事故里 ``git status`` 与 ``git log`` 是交错的，
只数「连续重复」会漏。

计数**按执行主角分账**（``LoopGuardMiddleware`` 的 ``agent`` 那格）：主控与三个子智能体
各数各的。共用一份的后果实测过（2026-10-16 真机）：``TurnState`` 经 ``configurable`` 注入，
而上游会把父级 configurable 原样传进子智能体（``deepagents/middleware/subagents.py`` 的
``task``），于是一个 coder 把某条探针命令的额度用满之后，**下一个 coder 第一次调用就直接
触发中止**；主控只看到「工具 task 执行失败」，换个说法再派一次 —— 那轮 4 分钟里连派了
20 次。同一角色的多次派发**仍然合账**，那正是要拦的（同一件事反复派同一个角色）。

拦下与中止各记一条 WARNING（署名就是 ``agent`` 那格）：这两条路以前**一行日志都没有**，
模型那头只看得到一句「同样的调用不会有新结果」，事后排查连「是哪条工具、第几次」都无从查起。

- 第 N 次（``LOOP_GUARD_REPEATS``，默认 3）：**不执行**这次调用，回一条 ``status="error"``
  的 ``ToolMessage``。与 ``boundary.py`` 同一个取舍 —— 模型看得到理由、能换做法，
  图也不会因为一个被拒的调用塌掉；这一条不再经过内侧的脱敏中间件，理由里没有凭据值。
- 第 N+1 次：抛 ``TurnStop``，本轮到此为止（service 层收下，补一条中断记录）。

阈值 0 ＝ 关掉。``TurnState`` 由 server 层每轮建一个、经 ``configurable`` 注入；**没有
state 就整层直通**（单测、脚本直接跑图时不受影响）。

只实现异步版：服务端统一走 ``astream_events``。子智能体不继承主智能体的中间件，
三个角色各自要挂一份（见 ``subagents.py``）。

「派子智能体前必须先写清单」不在这里 —— 那是**执行前置条件**，不是空转刹车，
见 ``delegation_gate.py``。两者共用同一份 ``TurnState``，但开关与职责各自独立。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.tools.context import get_configurable

logger = logging.getLogger(__name__)


def _warning(name: str, count: int) -> str:
    return (
        f"`{name}` 本轮已经用同样的参数调用过 {count} 次（含这次），本次不再执行。"
        "同样的调用不会有新结果：换个做法，或者把卡住的地方、已掌握的情况直接讲给用户。"
    )


class LoopGuardMiddleware(AgentMiddleware):
    """按本轮出现次数拦重复调用。阈值读自 ``TurnState``（本身就来自配置项）。"""

    def __init__(self, *, agent: str = MAIN_AGENT) -> None:
        #: 这一份守卫替谁数（主控还是某个子智能体角色）：既是计数的分账键，也是日志署名。
        #: 子智能体那三份在 ``subagents.py`` 里按角色各建一个实例。
        self._agent = agent

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
        if not state.enabled:
            return await handler(request)

        count = state.note_call(name, call.get("args"), self._agent)

        if count < state.repeats:
            return await handler(request)
        if count == state.repeats:
            logger.warning(
                "循环守卫拦下 `%s`：本轮第 %d 次同样的调用（阈值 %d），不执行，回一条错误响应",
                name or "该工具",
                count,
                state.repeats,
                extra={"agent": self._agent},
            )
            return ToolMessage(
                content=_warning(name or "该工具", count),
                name=name or "unknown",
                tool_call_id=call.get("id") or "",
                status="error",
            )
        logger.warning(
            "循环守卫中止本轮：`%s` 本轮第 %d 次同样的调用（阈值 %d）",
            name or "该工具",
            count,
            state.repeats,
            extra={"agent": self._agent},
        )
        raise state.abort(name, count)
