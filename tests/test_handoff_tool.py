"""能力交接：当前形态做不了这一轮请求时，把它交给更合适的形态接着做。

这个工具是「形态裁掉的能力模型看不见」那个死角的出口，所以要钉住的是它**什么时候不交接**
—— 每条拒绝都是一句「这件事该你自己做」，模型据此换做法，而不是当成环境故障去重试。
其中最要紧的一条：**只有模型重判（``SOURCE_LLM``）才放行**。要纠正的常常正是口令层
（句子里一个「实施」把意图钉成 coding，真实诉求是「先出方案」），拿它的复读去换形态，
等于换个姿势再猜一次。

放行之后的形状同样要钉：跑在**派生线程**上（``…::handoff-1``）、内层那一轮**不进外层
事件流**（``var_child_runnable_config`` 被临时摘掉）、结果**截断**在上限里。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import var_child_runnable_config

from ai_coding_harness.agent.middleware import TurnState
from ai_coding_harness.agent.profiles import QA_FORM
from ai_coding_harness.config import Settings
from ai_coding_harness.server.intent import SOURCE_FALLBACK, SOURCE_LLM, IntentDecision
from ai_coding_harness.tools.handoff import (
    ALREADY,
    EMPTY_RESULT,
    NO_REJUDGE,
    NO_STATE,
    NOT_LLM,
    SAME_FORM,
    HandoffContext,
    build_handoff_tools,
    final_text,
)
from tests.v3_events import (
    message_finish,
    message_start,
    reasoning,
    tool_finish,
    tool_start,
)
from tests.v3_events import (
    text as text_delta,
)


@dataclass
class _Inner:
    """内层那一轮：记下收到的 payload 与 config，回一段预设消息。"""

    messages: list[Any]
    error: BaseException | None = None

    def __post_init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.configs: list[dict[str, Any]] = []
        #: 跑内层那一刻的环境 config —— 断言「摘干净了」就靠它
        self.ambient: Any = "未记录"

    async def ainvoke(self, payload: dict[str, Any], config: dict[str, Any] | None = None) -> Any:
        self.payloads.append(payload)
        self.configs.append(config or {})
        self.ambient = var_child_runnable_config.get()
        if self.error is not None:
            raise self.error
        return {"messages": self.messages}

    @property
    def inner_message(self) -> str:
        """内层收到的用户消息（``_inner_input`` 拼出来的那段）。"""
        return self.payloads[0]["messages"][0]["content"]


class _Manager:
    """假会话管理器：只记「按哪个 profile 取的智能体」，并给一个固定线程 id。"""

    def __init__(self, inner: _Inner) -> None:
        self._inner = inner
        self.profiles: list[Any] = []

    def get_agent(self, runtime: Any, session: Any, profile: Any) -> _Inner:
        self.profiles.append(profile)
        return self._inner

    def thread_id(self, session: Any) -> str:
        return "thread-1"


@dataclass
class _StreamingInner:
    """内层那一轮的事件流版本：用来验证 handoff 的日志与用量并账。"""

    messages: list[Any]
    events: list[dict[str, Any]]

    def __post_init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.configs: list[dict[str, Any]] = []

    async def astream_events(
        self, payload: dict[str, Any], config: dict[str, Any] | None = None, version: str = "v3"
    ):
        self.payloads.append(payload)
        self.configs.append(config or {})

        async def gen():
            for event in self.events:
                yield event

        return gen()

    async def aget_state(self, config: dict[str, Any] | None = None) -> Any:
        return SimpleNamespace(values={"messages": self.messages})


@pytest.fixture
def ambient():
    """注入外层 configurable —— 真实路径里由 server 层经 ``RunnableConfig`` 带下来。"""

    def _set(turn_state: Any = None, **extra: Any) -> None:
        configurable = dict(extra)
        if turn_state is not None:
            configurable["turn_state"] = turn_state
        var_child_runnable_config.set({"configurable": configurable})

    token = var_child_runnable_config.set({"configurable": {}})
    yield _set
    var_child_runnable_config.reset(token)


def _llm(intent: str) -> IntentDecision:
    return IntentDecision(intent=intent, source=SOURCE_LLM)


def _context(inner: _Inner, *, form: str = QA_FORM, settings: Settings | None = None) -> HandoffContext:
    return HandoffContext(
        manager=_Manager(inner),
        runtime=SimpleNamespace(sandbox=None, backend=None, agents={}),
        session=SimpleNamespace(public_id="pub-1"),
        settings=settings or Settings(),
        form=form,
    )


async def _call(
    tool: Any,
    *,
    reason: str = "手上没有建 PR 的工具",
    notes: str = "",
    runtime: ToolRuntime | None = None,
) -> str:
    payload: dict[str, Any] = {"reason": reason, "notes": notes}
    if runtime is not None:
        payload["runtime"] = runtime
    return await tool.ainvoke(payload)


def _runtime(events: list[dict[str, Any]]) -> ToolRuntime:
    return ToolRuntime(
        state={},
        context=None,
        config={"run_id": UUID("00000000-0000-0000-0000-000000000001")},
        stream_writer=events.append,
        tool_call_id="handoff-call",
        store=None,
    )


class TestRefusals:
    """不交接的那些情形：每条理由都是给模型的指令，不是错误。"""

    async def test_no_round_context_refuses(self, ambient):
        """没有 ``turn_state``（脚本直调工具）时不能瞎跑：交给用户的话由外层说。"""
        ambient()
        inner = _Inner([AIMessage("不该跑到这里")])
        out = await _call(build_handoff_tools(_context(inner))[0])
        assert out == NO_STATE
        assert inner.payloads == []

    async def test_second_attempt_in_the_same_round_refuses(self, ambient):
        """额度扣在尝试上：同一轮第二次交接直接拒 —— 被拒之后还再试的多半是原地甩锅。"""
        state = TurnState(message="做点事", rejudge=_rejudge("coding"))
        ambient(state)
        inner = _Inner([AIMessage("来过了")])
        tool = build_handoff_tools(_context(inner))[0]

        await _call(tool)
        assert len(inner.payloads) == 1

        assert await _call(tool) == ALREADY
        assert len(inner.payloads) == 1, "第二次不该再跑一趟内层"

    async def test_missing_rejudge_callback_refuses(self, ambient):
        """没有重判回调时宁可不交接：拿关键字口径去换个形态等于再猜一次。"""
        ambient(TurnState(message="做点事"))
        inner = _Inner([AIMessage("不该跑到这里")])
        out = await _call(build_handoff_tools(_context(inner))[0])
        assert out == NO_REJUDGE
        assert inner.payloads == []

    async def test_keyword_fallback_judgement_refuses(self, ambient):
        """重判没走模型（分类器不可用、退回关键字口径）不足以推翻原来的判断。"""
        state = TurnState(
            message="做点事",
            rejudge=_fixed(IntentDecision(intent="coding", source=SOURCE_FALLBACK)),
        )
        ambient(state)
        inner = _Inner([AIMessage("不该跑到这里")])
        out = await _call(build_handoff_tools(_context(inner))[0])
        assert out == NOT_LLM
        assert inner.payloads == []

    async def test_rejudging_to_the_same_form_refuses(self, ambient):
        """重判还落在本形态上：这件事本来就该你做，接着做。"""
        state = TurnState(message="做点事", rejudge=_rejudge("qa"))
        ambient(state)
        inner = _Inner([AIMessage("不该跑到这里")])
        out = await _call(build_handoff_tools(_context(inner, form=QA_FORM))[0])
        assert out == SAME_FORM
        assert inner.payloads == []

    async def test_refusals_do_not_look_like_errors(self, ambient):
        """都是普通字符串：说成「失败 / 错误」会让模型当成环境故障去重试。"""
        ambient()
        tool = build_handoff_tools(_context(_Inner([])))[0]
        out = await _call(tool)
        assert isinstance(out, str)
        assert "失败" not in out and "错误" not in out


class TestDispatch:
    """放行之后：按重判结果取形态、跑在派生线程上、把正文带回来。"""

    async def test_it_assembles_the_target_form(self, ambient):
        manager_inner = _Inner([AIMessage("方案已经改完了")])
        ctx = _context(manager_inner)
        state = TurnState(message="别给方案了，直接改", rejudge=_rejudge("coding"))
        ambient(state)

        out = await _call(build_handoff_tools(ctx)[0])

        assert [p.form for p in ctx.manager.profiles] == ["orchestrate"]
        assert "已交给「coding」形态完成" in out
        assert "方案已经改完了" in out

    async def test_it_logs_the_inner_run_and_merges_usage(self, ambient, caplog):
        """内层不再「隐形」：LLM/工具日志带 handoff 前缀，用量并回外层账。"""
        inner = _StreamingInner(
            [AIMessage("改完了")],
            [
                message_start(),
                message_finish(usage={"input_tokens": 120, "output_tokens": 8}),
                tool_start("read_file", call_id="c1", input={"file_path": "/a.py"}),
                tool_finish(call_id="c1", output="class A: pass"),
            ],
        )
        state = TurnState(message="直接改", rejudge=_rejudge("coding"))
        merged: list[dict[str, Any]] = []
        state.usage_sink = merged.append
        ambient(state)

        with caplog.at_level(logging.INFO):
            out = await _call(build_handoff_tools(_context(inner))[0])

        assert "已交给「coding」形态完成" in out
        lines = [record.getMessage() for record in caplog.records]
        assert any("handoff [run=" in line and "LLM 调用完成" in line for line in lines), lines
        assert any("handoff [run=c1" in line and "工具调用 → read_file" in line for line in lines), lines
        assert merged and merged[0]["totals"]["input"] == 120

    async def test_inner_events_form_a_run_tree_for_the_ui(self, ambient):
        """内层正文 / 思考 / 工具通过 stream_writer 回传，run_id 保持父子关系。"""
        inner = _StreamingInner(
            [AIMessage("改完了")],
            [
                message_start(agent="coder"),
                text_delta("内层正文", agent="coder"),
                reasoning("内层思考", agent="coder"),
                message_finish(agent="coder"),
                tool_start("read_file", call_id="c1", input={"file_path": "/a.py"}),
                tool_finish(call_id="c1", output="class A: pass"),
            ],
        )
        traces: list[dict[str, Any]] = []
        state = TurnState(message="直接改", rejudge=_rejudge("coding"))
        ambient(state)

        await _call(build_handoff_tools(_context(inner))[0], runtime=_runtime(traces))

        assert traces[0]["node_kind"] == "handoff"
        assert traces[0]["phase"] == "start"
        assert traces[0]["run_id"] == "handoff:handoff-call"
        assert traces[0]["parent_run_id"] == "00000000-0000-0000-0000-000000000001"
        assert any(
            trace["node_kind"] == "model"
            and trace["phase"] == "delta"
            and trace["content"] == "内层正文"
            and trace["parent_run_id"] == "handoff:handoff-call"
            for trace in traces
        )
        assert any(
            trace["node_kind"] == "model"
            and (trace.get("data") or {}).get("part") == "reasoning"
            and trace["content"] == "内层思考"
            for trace in traces
        )
        assert any(
            trace["node_kind"] == "tool"
            and trace["name"] == "read_file"
            and trace["run_id"] == "c1"
            for trace in traces
        )

    async def test_inner_run_gets_a_derived_thread(self, ambient):
        """内层不能蹭外层线程：``checkpoint_ns`` 会被重置成 ``""``，两边落到同一个根槽。"""
        inner = _Inner([AIMessage("做完了")])
        state = TurnState(message="直接改", rejudge=_rejudge("coding"))
        ambient(state)

        await _call(build_handoff_tools(_context(inner))[0])

        assert inner.configs[0]["configurable"]["thread_id"] == "thread-1::handoff-1"

    async def test_inner_run_carries_intent_terms_and_the_same_state(self, ambient):
        """意图要与重判一致（指令段读它）、词表要带下去（脱敏）、``TurnState`` 与外层同一个。"""
        inner = _Inner([AIMessage("做完了")])
        state = TurnState(message="直接改", rejudge=_rejudge("coding"))
        ambient(state, redact_terms=("synth-token",))

        await _call(build_handoff_tools(_context(inner))[0])

        configurable = inner.configs[0]["configurable"]
        assert configurable["intent"] == "coding"
        assert configurable["redact_terms"] == ("synth-token",)
        assert configurable["turn_state"] is state, "换个 state，内层的重复计数就与外层脱账了"

    async def test_inner_run_gets_its_own_recursion_limit(self, ambient):
        settings = Settings(agent_recursion_limit=77)
        inner = _Inner([AIMessage("做完了")])
        state = TurnState(message="直接改", rejudge=_rejudge("coding"))
        ambient(state)

        await _call(build_handoff_tools(_context(inner, settings=settings))[0])

        assert inner.configs[0]["recursion_limit"] == 77

    async def test_the_inner_round_is_detached_from_the_outer_stream(self, ambient):
        """跑内层时把环境 config 摘干净。

        不摘的话内层模型的正文会带着**空 namespace** 混进外层 v3 流，被 ``V3Translator``
        当成外层自己的回答下发并落库 —— 正是要避免的那类「一轮里冒出别人的话」。
        """
        inner = _Inner([AIMessage("做完了")])
        state = TurnState(message="直接改", rejudge=_rejudge("coding"))
        ambient(state)

        await _call(build_handoff_tools(_context(inner))[0])

        assert inner.ambient == {}, "内层不该看到外层的事件流上下文"
        # 摘掉之后必须还原：后面还有别的工具要在这个 config 下跑
        assert var_child_runnable_config.get()["configurable"]["turn_state"] is state

    async def test_the_original_wording_is_forwarded(self, ambient):
        """交给对方的是**原话**，不是模型转述 —— 工具上没有「转述」参数就是为了这个。"""
        inner = _Inner([AIMessage("做完了")])
        state = TurnState(
            message="把 src/a.py 里的 create_user 改成不用 ORM",
            history=("先看看这个仓库", "再给我出个方案"),
            rejudge=_rejudge("coding"),
        )
        ambient(state)

        await _call(
            build_handoff_tools(_context(inner))[0],
            reason="手上没有写权限",
            notes="方案已经批准，直接改",
        )

        text = inner.inner_message
        assert "把 src/a.py 里的 create_user 改成不用 ORM" in text
        assert "先看看这个仓库" in text and "再给我出个方案" in text
        assert "手上没有写权限" in text
        assert "方案已经批准，直接改" in text

    async def test_notes_are_optional(self, ambient):
        inner = _Inner([AIMessage("做完了")])
        ambient(TurnState(message="直接改", rejudge=_rejudge("coding")))
        await _call(build_handoff_tools(_context(inner))[0])
        assert "交接补充" not in inner.inner_message


class TestResultShape:
    async def test_empty_result_is_not_padded_by_the_outer_model(self, ambient):
        """内层没正文：明确说「没拿到结果」，别让外层模型替它编。"""
        inner = _Inner([AIMessage("")])
        ambient(TurnState(message="直接改", rejudge=_rejudge("coding")))
        out = await _call(build_handoff_tools(_context(inner))[0])
        assert out == EMPTY_RESULT

    async def test_long_result_is_truncated(self, ambient):
        settings = Settings(handoff_result_max=20)
        inner = _Inner([AIMessage("甲" * 200)])
        ambient(TurnState(message="直接改", rejudge=_rejudge("coding")))

        out = await _call(build_handoff_tools(_context(inner, settings=settings))[0])

        assert "（结果过长，已截断）" in out
        assert "甲" * 21 not in out
        assert "甲" * 20 in out

    async def test_result_within_the_limit_is_untouched(self, ambient):
        body = "乙" * 20
        inner = _Inner([AIMessage(body)])
        ambient(TurnState(message="直接改", rejudge=_rejudge("coding")))
        out = await _call(build_handoff_tools(_context(inner))[0])
        assert "已截断" not in out and body in out


class TestFinalText:
    """取内层正文：最后一条**有正文**的 AI 消息（工具往返也在消息里）。"""

    def test_empty_messages_yield_nothing(self):
        assert final_text({"messages": []}) == ""
        assert final_text({}) == ""

    def test_non_mapping_result_yields_nothing(self):
        assert final_text("随便什么") == ""

    def test_the_last_ai_message_wins(self):
        result = {"messages": [AIMessage("先查一下"), AIMessage("结论在这里")]}
        assert final_text(result) == "结论在这里"

    def test_trailing_blank_ai_message_is_skipped(self):
        """收尾那条常常是空的（工具调用后的占位）—— 不算结论。"""
        result = {"messages": [AIMessage("结论在这里"), AIMessage("   ")]}
        assert final_text(result) == "结论在这里"

    def test_content_blocks_are_recognized(self):
        """v3 的正文可能是内容块列表，不是裸字符串。"""
        result = {"messages": [AIMessage([{"type": "text", "text": "块里的结论"}])]}
        assert final_text(result) == "块里的结论"

    def test_only_ai_messages_count(self):
        class _NotAi:
            content = "别的角色的消息"

        assert final_text({"messages": [_NotAi()]}) == ""


def _fixed(decision: IntentDecision):
    async def _rejudge(message: str, history: Any) -> IntentDecision:
        return decision

    return _rejudge


def _rejudge(intent: str):
    return _fixed(_llm(intent))
