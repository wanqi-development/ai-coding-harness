"""委派闸门：每次 ``task`` 之前都要有一段新的 ``write_todos``。

规则按**消息区间**算：

- 第一次派发：最近一条 ``HumanMessage`` 之后要有 ``write_todos``；
- 之后每次派发：上一次 ``task`` 结果之后要有新的 ``write_todos``；
- 同一条 AI 消息里并行的 ``write_todos`` + ``task`` 不算（``task`` 可能先执行）。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.prebuilt.tool_node import ToolCallRequest

from ai_coding_harness.agent.middleware import (
    DelegationGateMiddleware,
    LoopGuardMiddleware,
    TurnState,
)


@pytest.fixture
def use_state():
    """只给「闸门在循环计数外侧」那条用例用：循环守卫读的是 configurable。"""

    def _use(state: TurnState | None) -> None:
        configurable: dict[str, Any] = {}
        if state is not None:
            configurable["turn_state"] = state
        var_child_runnable_config.set({"configurable": configurable})

    token = var_child_runnable_config.set({"configurable": {}})
    yield _use
    var_child_runnable_config.reset(token)


def _human(text: str = "实施会话") -> HumanMessage:
    return HumanMessage(content=text)


def _todo(call_id: str = "todo-1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "write_todos", "args": {"todos": []}, "id": call_id}
        ],
    )


def _task_call(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "task", "args": {"description": "派子智能体"}, "id": call_id}
        ],
    )


def _task_result(call_id: str) -> ToolMessage:
    """与 deepagents 一致：``task`` 的结果只有 ``tool_call_id``，没有 ``name``。"""
    return ToolMessage(content="子智能体完成", tool_call_id=call_id)


def _request(
    name: str, messages: list[Any] | None, call_id: str = "current"
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": call_id},
        tool=None,
        state=None if messages is None else {"messages": messages},
        runtime=None,
    )


class _Handler:
    """假工具：记下「真跑到了」几次。被拦下的调用不能进工具。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, request: ToolCallRequest) -> ToolMessage:
        self.calls += 1
        name = str(request.tool_call.get("name") or "")
        return ToolMessage(
            content="真跑了",
            name=name,
            tool_call_id=str(request.tool_call.get("id") or ""),
        )


async def _run(
    name: str, messages: list[Any] | None, call_id: str = "current"
) -> tuple[Any, _Handler]:
    handler = _Handler()
    out = await DelegationGateMiddleware().awrap_tool_call(
        _request(name, messages, call_id), handler
    )
    return out, handler


class TestFirstDelegation:
    async def test_task_before_any_todo_is_refused(self):
        messages = [_human(), _task_call("current")]

        out, handler = await _run("task", messages)

        assert handler.calls == 0, "没写清单就不能真的派子智能体"
        assert isinstance(out, ToolMessage) and out.status == "error"
        assert "write_todos" in out.content and "task" in out.content

    async def test_a_todo_after_the_human_message_opens_the_gate(self):
        messages = [_human(), _todo(), _task_call("current")]

        out, handler = await _run("task", messages)

        assert handler.calls == 1
        assert isinstance(out, ToolMessage) and out.status != "error"

    async def test_a_todo_from_the_previous_turn_does_not_count(self):
        """上一轮写过清单，不适用于这一轮。"""
        messages = [_todo(), _human(), _task_call("current")]

        out, handler = await _run("task", messages)

        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_a_parallel_todo_in_the_same_message_does_not_count(self):
        """同一条 AI 消息里并行发 write_todos + task：task 可能先执行，必须拦。"""
        current = AIMessage(
            content="",
            tool_calls=[
                {"name": "write_todos", "args": {"todos": []}, "id": "todo-1"},
                {"name": "task", "args": {"description": "派"}, "id": "current"},
            ],
        )
        messages = [_human(), current]

        out, handler = await _run("task", messages)

        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_a_failed_todo_does_not_count(self):
        """被边界拒掉的 write_todos 会留一条 error ToolMessage，按 id 排除。"""
        messages = [
            _human(),
            _todo(),
            ToolMessage(content="越界", tool_call_id="todo-1", status="error"),
            _task_call("current"),
        ]

        out, handler = await _run("task", messages)

        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"


class TestLaterDelegations:
    async def test_second_task_needs_an_update_after_the_first_result(self):
        """第一次派发前的清单，不能顺延给第二次派发。"""
        messages = [
            _human(),
            _todo(),
            _task_call("task-1"),
            _task_result("task-1"),
            _task_call("current"),
        ]

        out, handler = await _run("task", messages)

        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_second_task_after_an_update_is_allowed(self):
        messages = [
            _human(),
            _todo(),
            _task_call("task-1"),
            _task_result("task-1"),
            _todo("todo-2"),
            _task_call("current"),
        ]

        out, handler = await _run("task", messages)

        assert handler.calls == 1
        assert isinstance(out, ToolMessage) and out.status != "error"

    async def test_parallel_tasks_share_the_same_boundary(self):
        """同一批并行派发：第一个的结果先回来，也不算第二个的边界。"""
        batch = AIMessage(
            content="",
            tool_calls=[
                {"name": "task", "args": {"description": "coder"}, "id": "task-1"},
                {"name": "task", "args": {"description": "reviewer"}, "id": "task-2"},
            ],
        )
        messages = [_human(), _todo(), batch, _task_result("task-1")]

        out, handler = await _run("task", messages, call_id="task-2")

        assert handler.calls == 1
        assert isinstance(out, ToolMessage) and out.status != "error"


class TestStraightThrough:
    async def test_other_tools_are_untouched(self):
        out, handler = await _run("execute", [_human()])

        assert handler.calls == 1
        assert isinstance(out, ToolMessage) and out.status != "error"

    async def test_without_messages_it_straight_through(self):
        """脚本 / 单测直接跑图时没有 state —— 不受影响。"""
        out, handler = await _run("task", None)

        assert handler.calls == 1
        assert isinstance(out, ToolMessage) and out.status != "error"


class TestOrdering:
    async def test_a_refused_task_does_not_consume_the_repeat_budget(self, use_state):
        state = TurnState(repeats=2)
        use_state(state)
        gate = DelegationGateMiddleware()
        guard = LoopGuardMiddleware()

        async def inner(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="真跑了",
                name="task",
                tool_call_id=str(request.tool_call.get("id") or ""),
            )

        async def pass_through(request: ToolCallRequest) -> ToolMessage:
            return await guard.awrap_tool_call(request, inner)

        messages = [_human(), _task_call("current")]
        blocked = await gate.awrap_tool_call(
            _request("task", messages), pass_through
        )
        assert isinstance(blocked, ToolMessage) and blocked.status == "error"

        messages.insert(1, _todo())
        out = await gate.awrap_tool_call(_request("task", messages), pass_through)
        assert isinstance(out, ToolMessage) and out.status != "error"
