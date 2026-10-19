"""委派闸门：主控没写清单就不许 ``task``。

判据是「本轮**成功**执行过 write_todos」：只发起过不算，工具自己报错也不算；
补完清单之后可以连续派发，被拦下的 ``task`` 不占别的额度。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from ai_coding_harness.agent.middleware import (
    DelegationGateMiddleware,
    LoopGuardMiddleware,
    TurnState,
)


@pytest.fixture
def use_state():
    """把 ``turn_state`` 注入 configurable —— 闸门读的是 ``get_configurable`` 这条通道。"""

    def _use(state: TurnState | None) -> None:
        configurable: dict[str, Any] = {}
        if state is not None:
            configurable["turn_state"] = state
        var_child_runnable_config.set({"configurable": configurable})

    token = var_child_runnable_config.set({"configurable": {}})
    yield _use
    var_child_runnable_config.reset(token)


def _request(name: str, args: Any, call_id: str = "call-1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": call_id},
        tool=None,
        state=None,
        runtime=None,
    )


class _Handler:
    """假工具：记下「真跑到了」几次。被拦下的调用不能进工具。"""

    def __init__(self, *, status: str = "success") -> None:
        self.calls = 0
        self.status = status

    async def __call__(self, request: ToolCallRequest) -> ToolMessage:
        self.calls += 1
        name = str(request.tool_call.get("name") or "")
        return ToolMessage(
            content="真跑了", name=name, tool_call_id="call-1", status=self.status
        )


async def _guarded(
    state: TurnState | None, use_state, name: str, args: Any
) -> tuple[Any, _Handler]:
    use_state(state)
    handler = _Handler()
    out = await DelegationGateMiddleware().awrap_tool_call(_request(name, args), handler)
    return out, handler


class TestTaskRequiresTodo:
    async def test_task_is_refused_before_write_todos(self, use_state):
        state = TurnState(repeats=3)

        out, handler = await _guarded(
            state, use_state, "task", {"description": "让 coder 改代码"}
        )

        assert handler.calls == 0, "没写清单就不能真的派子智能体"
        assert isinstance(out, ToolMessage) and out.status == "error"
        assert "write_todos" in out.content
        assert "task" in out.content

    async def test_task_is_allowed_after_a_successful_write_todos(self, use_state):
        state = TurnState(repeats=3)

        todos, todo_handler = await _guarded(
            state, use_state, "write_todos", {"todos": [{"content": "改代码"}]}
        )
        assert isinstance(todos, ToolMessage) and todos.status != "error"
        assert todo_handler.calls == 1

        _out, task_handler = await _guarded(
            state, use_state, "task", {"description": "让 coder 改代码"}
        )
        assert task_handler.calls == 1

    async def test_a_failed_write_todos_does_not_open_the_gate(self, use_state):
        state = TurnState(repeats=3)
        use_state(state)

        fail = _Handler(status="error")
        await DelegationGateMiddleware().awrap_tool_call(
            _request("write_todos", {"todos": []}), fail
        )
        assert fail.calls == 1

        out, handler = await _guarded(
            state, use_state, "task", {"description": "让 coder 改代码"}
        )
        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_a_command_result_counts_as_success(self, use_state):
        """``write_todos`` 正常返回的是 ``Command``（改图状态），不是 ToolMessage。"""
        state = TurnState(repeats=3)
        use_state(state)

        class _CommandTool:
            calls = 0

            async def __call__(self, request: ToolCallRequest) -> Command[Any]:
                self.calls += 1
                return Command(update={"todos": []})

        tool = _CommandTool()
        await DelegationGateMiddleware().awrap_tool_call(
            _request("write_todos", {"todos": []}), tool
        )
        assert tool.calls == 1

        _out, handler = await _guarded(
            state, use_state, "task", {"description": "让 coder 改代码"}
        )
        assert handler.calls == 1

    async def test_one_todo_allows_every_task_of_the_turn(self, use_state):
        """清单是本轮的门票，不是一次性的派发额度。"""
        state = TurnState(repeats=3)
        await _guarded(state, use_state, "write_todos", {"todos": [{"content": "改"}]})

        for role in ("planner", "coder", "reviewer"):
            _out, handler = await _guarded(
                state, use_state, "task", {"description": f"派 {role}"}
            )
            assert handler.calls == 1


class TestStraightThrough:
    async def test_other_tools_are_untouched(self, use_state):
        out, handler = await _guarded(
            TurnState(repeats=3), use_state, "execute", {"command": "git status"}
        )
        assert handler.calls == 1
        assert isinstance(out, ToolMessage) and out.status != "error"

    async def test_without_turn_state_it_straight_through(self, use_state):
        """单测、脚本直接跑图时没有 configurable —— 那时不受影响。"""
        _out, handler = await _guarded(None, use_state, "task", {"description": "x"})
        assert handler.calls == 1

    async def test_a_failed_tool_other_than_write_todos_does_not_open_the_gate(
        self, use_state
    ):
        state = TurnState(repeats=3)
        use_state(state)
        await DelegationGateMiddleware().awrap_tool_call(
            _request("execute", {"command": "false"}), _Handler(status="error")
        )

        out, handler = await _guarded(state, use_state, "task", {"description": "x"})
        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"


class TestOrdering:
    """闸门挂在循环计数外侧：被拦下的 ``task`` 不占重复额度。

    这里按 ``graph.py`` 的装配顺序手工串起来 —— 顺序反了，被拒的派发会先记一次数，
    模型补完清单后第一次真正派发就可能被判重复。
    """

    async def test_a_refused_task_does_not_consume_the_repeat_budget(self, use_state):
        state = TurnState(repeats=2)
        use_state(state)
        gate = DelegationGateMiddleware()
        guard = LoopGuardMiddleware()

        async def inner(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="真跑了",
                name=str(request.tool_call.get("name") or ""),
                tool_call_id="call-1",
            )

        async def pass_through(request: ToolCallRequest) -> ToolMessage:
            return await guard.awrap_tool_call(request, inner)

        blocked = await gate.awrap_tool_call(
            _request("task", {"description": "让 coder 改代码"}), pass_through
        )
        assert isinstance(blocked, ToolMessage) and blocked.status == "error"

        await gate.awrap_tool_call(
            _request("write_todos", {"todos": [{"content": "改代码"}]}), pass_through
        )
        out = await gate.awrap_tool_call(
            _request("task", {"description": "让 coder 改代码"}), pass_through
        )
        assert isinstance(out, ToolMessage) and out.status != "error"
