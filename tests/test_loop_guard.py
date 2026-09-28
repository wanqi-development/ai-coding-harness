"""空转刹车：同一工具同一参数反复调用时，先劝、再停。

缘起是 2026-09-21 那轮 686 秒 —— 日志里 ``execute git status`` 与 ``git log`` 交错着
来回转，每一圈都是「同样的输入、同样的输出」，模型却当成进展。所以这一层必须钉住两件事：

1. **数的是本轮出现次数，不要求相邻**。只数「连续重复」会把那次事故整个漏掉。
2. **第 N 次不执行、只回一条带理由的 ToolMessage；第 N+1 次才抛 ``TurnStop``**。前者
   让模型有机会换做法，后者才是刹车 —— 一上来就抛，模型连改的机会都没有。

阈值与开关都在 ``TurnState`` 上（``repeats <= 0`` 整层直通），没有轮次上下文时同样直通 ——
单测、脚本直接跑图不该被这层影响。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from ai_coding_harness.agent.middleware import (
    ABORT_REASON,
    LOOP_ABORT_MARKER,
    LoopGuardMiddleware,
    TurnState,
    TurnStop,
)


@pytest.fixture
def use_state():
    """把 ``turn_state`` 注入 configurable —— 守卫读的是 ``get_configurable`` 这条通道。

    真跑的时候由 server 层经 ``RunnableConfig`` 注入；这里直接改那个 ContextVar，
    走的是同一条路（``langgraph.config.get_config`` 底层就是它）。
    """

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
    """假工具：记下「真跑到了」几次。

    守关键不变量 —— 被判重复的那次**不能**进工具，否则工作照做、只是嘴上劝一句，
    刹车就白装了。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, request: ToolCallRequest) -> ToolMessage:
        self.calls += 1
        name = str(request.tool_call.get("name") or "")
        return ToolMessage(content="真跑了", name=name, tool_call_id="call-1")


async def _call(
    state: TurnState | None, use_state, name: str, args: Any, *, call_id: str = "call-1"
) -> Any:
    """按当前 ``turn_state`` 走一次守卫；返回它给的东西（可能抛）。"""
    use_state(state)
    return await LoopGuardMiddleware().awrap_tool_call(
        _request(name, args, call_id), _Handler()
    )


async def _guarded(middleware_state: TurnState | None, use_state, name: str, args: Any) -> Any:
    """同上，但把 handler 还回来方便断言 —— 用于需要数调用次数的用例。"""
    use_state(middleware_state)
    handler = _Handler()
    out = await LoopGuardMiddleware().awrap_tool_call(_request(name, args), handler)
    return out, handler


class TestNoteCall:
    """计数本身：键是「工具名 + 参数」，参数顺序不该影响结果。"""

    def test_same_name_and_args_count_up(self):
        state = TurnState()
        assert state.note_call("execute", {"command": "git status"}) == 1
        assert state.note_call("execute", {"command": "git status"}) == 2

    def test_different_args_are_different_buckets(self):
        state = TurnState()
        state.note_call("execute", {"command": "git status"})
        assert state.note_call("execute", {"command": "git log"}) == 1

    def test_different_tool_is_a_different_bucket(self):
        state = TurnState()
        state.note_call("read_file", {"file_path": "/a"})
        assert state.note_call("write_file", {"file_path": "/a"}) == 1

    def test_argument_order_does_not_matter(self):
        """``json.dumps(sort_keys=True)`` 的意义：``{a,b}`` 与 ``{b,a}`` 是同一次调用。

        参数顺序由模型生成、每次都可能不同，按顺序比较等于每次都算新调用 —— 那这层就
        永远数不到 3。
        """
        state = TurnState()
        state.note_call("edit_file", {"file_path": "/a", "old": "x"})
        assert state.note_call("edit_file", {"old": "x", "file_path": "/a"}) == 2

    def test_missing_args_do_not_crash(self):
        """``args`` 缺失时是 ``None``，不是 dict —— 得有个桶装着它。"""
        state = TurnState()
        assert state.note_call("execute", None) == 1
        assert state.note_call("execute", None) == 2

    def test_unserializable_args_fall_back_to_repr(self):
        """参数里塞了不可序列化的东西（模型偶尔这么干）时不能炸在计数上。"""
        state = TurnState()
        assert state.note_call("execute", {"blob": object()}) == 1


class TestTurnStateFlags:
    def test_disabled_when_repeats_is_zero(self):
        assert TurnState(repeats=0).enabled is False

    def test_enabled_by_default(self):
        assert TurnState().enabled is True

    def test_handoff_quota_is_consumed_one_at_a_time(self):
        """交接额度：默认一次，用完返回 ``None``（内层那轮还要拿序号拼线程 id）。"""
        state = TurnState()
        assert state.note_handoff() == 1
        assert state.note_handoff() is None

    def test_handoff_quota_follows_the_setting(self):
        state = TurnState(max_handoffs=2)
        assert state.note_handoff() == 1
        assert state.note_handoff() == 2
        assert state.note_handoff() is None


class TestStraightThrough:
    """该直通的一律直通 —— 这层是刹车，不是路障。"""

    async def test_without_turn_state_nothing_is_counted(self, use_state):
        """单测、脚本直接跑图时没有 configurable —— 那时必须完全不受影响。"""
        for _ in range(5):
            out, handler = await _guarded(None, use_state, "execute", {"command": "git status"})
            assert isinstance(out, ToolMessage) and out.status != "error"
            assert handler.calls == 1

    async def test_threshold_zero_turns_the_guard_off(self, use_state):
        """``LOOP_GUARD_REPEATS=0`` 是关掉这一层的开关：配上就得真关。"""
        state = TurnState(repeats=0)
        for _ in range(5):
            out, handler = await _guarded(state, use_state, "execute", {"command": "ls"})
            assert isinstance(out, ToolMessage) and out.status != "error"
            assert handler.calls == 1

    async def test_below_the_threshold_the_tool_really_runs(self, use_state):
        state = TurnState(repeats=3)
        assert (await _guarded(state, use_state, "execute", {"command": "ls"}))[1].calls == 1
        assert (await _guarded(state, use_state, "execute", {"command": "ls"}))[1].calls == 1


class TestWarnThenStop:
    async def test_nth_call_is_refused_with_a_reason(self, use_state):
        """第 N 次：不执行，回一条 ``status="error"`` 的 ``ToolMessage``。"""
        state = TurnState(repeats=3)
        name, args = "execute", {"command": "git status"}
        await _call(state, use_state, name, args)
        await _call(state, use_state, name, args)

        out, handler = await _guarded(state, use_state, name, args)
        assert handler.calls == 0, "判了重复还让工具跑，等于只是嘴上劝一句"
        assert isinstance(out, ToolMessage)
        assert out.status == "error"
        assert out.tool_call_id == "call-1", "配不上 id 就还是个孤儿"
        assert out.name == "execute"
        assert "3 次" in out.content and "execute" in out.content

    async def test_next_call_stops_the_turn(self, use_state):
        """第 N+1 次：抛 ``TurnStop``，本轮到此为止。"""
        state = TurnState(repeats=3)
        name, args = "execute", {"command": "git status"}
        for _ in range(3):
            await _call(state, use_state, name, args)

        with pytest.raises(TurnStop) as caught:
            await _call(state, use_state, name, args)
        stop = caught.value
        assert stop.marker == LOOP_ABORT_MARKER
        assert stop.extra["aborted"] == ABORT_REASON
        assert stop.extra["tool"] == "execute"
        assert stop.extra["repeats"] == 4

    async def test_the_stop_is_also_left_on_the_state(self, use_state):
        """抛出之外还要落在 ``state.aborted`` 上。

        万一异常被某层吃成了 ``ToolMessage``，service 收尾时还得靠这个标记发现「本轮
        其实被刹了」—— 少了它，那一轮会以「智能体完成」记进库。
        """
        state = TurnState(repeats=1)
        await _call(state, use_state, "execute", {"command": "ls"})

        with pytest.raises(TurnStop) as caught:
            await _call(state, use_state, "execute", {"command": "ls"})
        assert state.aborted is caught.value

    async def test_it_returns_a_tool_message_not_a_command(self, use_state):
        """返回 ``Command`` 会改变图怎么走 —— 这里只是想给模型捎句话。"""
        out = await _call(TurnState(repeats=1), use_state, "execute", {"command": "ls"})
        assert isinstance(out, ToolMessage) and not isinstance(out, Command)

    async def test_a_call_without_an_id_still_returns(self, use_state):
        """``tool_call_id`` 是必填 ``str``，写成 ``None`` 会当场校验失败。"""
        use_state(TurnState(repeats=2))
        await LoopGuardMiddleware().awrap_tool_call(
            _request("execute", {"command": "ls"}), _Handler()
        )
        request = ToolCallRequest(
            tool_call={"name": "execute", "args": {"command": "ls"}, "id": None},
            tool=None,
            state=None,
            runtime=None,
        )
        out = await LoopGuardMiddleware().awrap_tool_call(request, _Handler())
        assert isinstance(out, ToolMessage) and out.tool_call_id == ""


class TestInterleavedRepeats:
    """数的是本轮出现次数，不要求相邻 —— 这正是那次事故的形状。"""

    async def test_a_sibling_call_in_between_does_not_reset_the_count(self, use_state):
        """``git status`` / ``git log`` 交错时，第三个 ``git status`` 照样得被拦。"""
        state = TurnState(repeats=3)
        await _call(state, use_state, "execute", {"command": "git status"})
        await _call(state, use_state, "execute", {"command": "git log"})
        await _call(state, use_state, "execute", {"command": "git status"})
        await _call(state, use_state, "execute", {"command": "git log"})

        out, handler = await _guarded(
            state, use_state, "execute", {"command": "git status"}
        )
        assert handler.calls == 0
        assert isinstance(out, ToolMessage) and out.status == "error"

    async def test_a_different_argument_is_still_allowed(self, use_state):
        """换个参数就是新信息：不能连着一并拦掉，否则模型连换做法都做不到。"""
        state = TurnState(repeats=2)
        await _call(state, use_state, "execute", {"command": "git status"})
        await _call(state, use_state, "execute", {"command": "git status"})

        _out, handler = await _guarded(
            state, use_state, "execute", {"command": "git status --short"}
        )
        assert handler.calls == 1

    async def test_the_other_tool_keeps_its_own_budget(self, use_state):
        state = TurnState(repeats=2)
        for _ in range(2):
            await _call(state, use_state, "execute", {"command": "ls"})

        _out, handler = await _guarded(state, use_state, "read_file", {"file_path": "/a"})
        assert handler.calls == 1


class TestWarningShapeReachesTheModel:
    async def test_warning_names_the_tool_and_the_count(self, use_state):
        """理由要能照着改：说清楚是哪个工具、第几次、为什么不再执行。"""
        state = TurnState(repeats=2)
        await _call(state, use_state, "write_todos", {"todos": []})

        out = await _call(state, use_state, "write_todos", {"todos": []})
        assert isinstance(out, ToolMessage)
        assert "write_todos" in out.content
        assert "2 次" in out.content
        assert "换个做法" in out.content
