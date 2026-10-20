"""委派闸门：主控每次派子智能体之前，都必须先更新一次清单。

缘起：2026-10-19 真机上主控频繁直接 ``task`` —— 子智能体在跑，用户却看不到这一轮
准备做哪几步，出了问题也没有可对照的清单。提示词里写过「先写清单」，但这类**顺序**
约束只有放到执行前才拦得住。

判据是**消息区间**，不是「这一轮调用过一次」—— 2026-10-20 真机：主控第一次派 coder
前写了一次清单，coder 回来后没更新就派 reviewer；本轮级标记会放行，但清单上 coder
那一步还停在 ``in_progress``。规则按用户口径落成两条：

1. **第一次派发**：从最近一条 ``HumanMessage`` 到这次 ``task`` 之间，要有一次
   ``write_todos`` 调用；
2. **之后每次派发**：从上一次 ``task`` 的结果到这次 ``task`` 之间，要有一次新的
   ``write_todos`` 更新。

同一条 AI 消息里并行发 ``write_todos`` + ``task`` **不算**：工具调用没有先后保证，
``task`` 可能先执行；模型必须等清单结果回来再派。同一批并行派发的多个 ``task``
不算彼此的边界（它们之间没有插入清单更新的机会）。

``write_todos`` 正常返回的是 ``Command``（不是 ``ToolMessage``），所以这里数的是
**AI 消息里的 tool_call**；被边界中间件拒掉的那种会留一条 ``status="error"`` 的
``ToolMessage``，按 ``tool_call_id`` 排除。

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
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from ai_coding_harness.core.stream import TASK_TOOL, TODO_TOOL
from ai_coding_harness.observability.logging import MAIN_AGENT

logger = logging.getLogger(__name__)


class DelegationGateMiddleware(AgentMiddleware):
    """主控专用：每次 ``task`` 之前都必须有一段新的 ``write_todos``。"""

    def __init__(self) -> None:
        # 建 agent 时打一行：排查「闸门到底加载了没有」不用再靠推断（2026-10-20 那次
        # 9:20 的漏拦就是跑在了一个没重载代码的旧进程上）。
        logger.info(
            "委派闸门已挂载：主控每次 task 前检查清单更新",
            extra={"agent": MAIN_AGENT},
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        name = str(call.get("name") or "")
        if name != TASK_TOOL:
            return await handler(request)

        messages = _messages(request.state)
        if not messages:
            logger.debug("委派闸门：拿不到消息历史，按直通处理（只应出现在脚本/单测）")
            return await handler(request)

        if not _todo_updated_since_boundary(messages, str(call.get("id") or "")):
            logger.warning(
                "委派闸门拦下 `task`：这一段还没有新的 `write_todos`，不派子智能体",
                extra={"agent": MAIN_AGENT},
            )
            return ToolMessage(
                content=(
                    "派子智能体之前要先更新 `write_todos`：第一次派发前写一份本轮清单；"
                    "之后每次子智能体返回、再派下一个之前，先把刚完成的那一步标成 "
                    "completed、把下一步标成 in_progress。本次 `task` 未执行。"
                ),
                name=name,
                tool_call_id=call.get("id") or "",
                status="error",
            )

        return await handler(request)


def _messages(state: Any) -> list[Any]:
    """``ToolCallRequest.state`` 里的消息列表（dict 与带属性的状态对象都认）。"""
    raw = (
        state.get("messages")
        if isinstance(state, dict)
        else getattr(state, "messages", None)
    )
    return list(raw or [])


def _is_current_call(msg: Any, call_id: str) -> bool:
    """这条 AI 消息是不是「带着当前 task 调用的那一条」。"""
    if not isinstance(msg, AIMessage):
        return False
    return any(str(tc.get("id") or "") == call_id for tc in (msg.tool_calls or []))


def _todo_updated_since_boundary(messages: list[Any], current_call_id: str) -> bool:
    """从最近边界（HumanMessage / 上一次 task 结果）到当前 task 之间，有没有清单更新。

    反向扫，边界之前的东西一律不看。当前 AI 消息整条跳过 —— 同一条消息里并行的
    ``write_todos`` 不算（执行顺序没有保证）。
    """
    task_call_ids: set[str] = set()
    failed_ids: set[str] = set()
    current_ai = next(
        (msg for msg in messages if _is_current_call(msg, current_call_id)), None
    )
    current_batch_tasks = (
        {
            str(tc.get("id") or "")
            for tc in (current_ai.tool_calls or [])
            if tc.get("name") == TASK_TOOL
        }
        if isinstance(current_ai, AIMessage)
        else set()
    )

    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                if tc.get("name") == TASK_TOOL:
                    task_call_ids.add(str(tc.get("id") or ""))
        elif isinstance(msg, ToolMessage) and msg.status == "error":
            failed_ids.add(str(msg.tool_call_id or ""))

    seen_todo = False
    for msg in reversed(messages):
        if _is_current_call(msg, current_call_id):
            continue
        if isinstance(msg, HumanMessage):
            return seen_todo
        if (
            isinstance(msg, ToolMessage)
            and str(msg.tool_call_id or "") in task_call_ids
            and str(msg.tool_call_id or "") not in current_batch_tasks
        ):
            # 同一批并行派发的 task 结果不算边界：它们之间没有插入清单更新的机会。
            return seen_todo
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                if (
                    tc.get("name") == TODO_TOOL
                    and str(tc.get("id") or "") not in failed_ids
                ):
                    seen_todo = True
    return seen_todo
