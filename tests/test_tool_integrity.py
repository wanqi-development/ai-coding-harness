"""工具调用配对自洽：三类漏洞各自的回归用例，外加装配点。

这个模块的每个用例都对应一条**实测过**的坏数据形状：

- 端点吐出 ``"id": ""`` 的调用（历史里五条，空 id 的一整批）；
- 工具抛异常导致同一个 superstep 的响应全丢，留下孤儿；
- 上游 ``PatchToolCallsMiddleware`` 用「全历史 tool 响应 id 集合」判「答没答」，
  空串在这个集合里没有身份 —— 历史里只要有过一条空 id 响应，此后所有空 id 调用
  都被判成已答而永不修补。最后一条的回归用例（``test_blank_orphan_...``）就是
  为它写的：那样的历史在上游判据下「无可修补」，在我们判据下必须被修。

``None`` 也钉住了：上游对 ``None`` 是无条件放行（``ToolMessage.tool_call_id`` 是必填
``str``，拿 None 去补会直接 pydantic 报错），我们给它补真 id。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents.middleware import (
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langgraph.errors import GraphBubbleUp
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from ai_coding_harness.agent.graph import build_agent
from ai_coding_harness.agent.middleware import (
    HistoryRepairMiddleware,
    ToolCallIdMiddleware,
    ToolErrorGuardMiddleware,
)
from ai_coding_harness.agent.middleware.tool_integrity import repair_messages
from ai_coding_harness.agent.profiles import ORCHESTRATE_FORM
from ai_coding_harness.agent.subagents import build_subagents
from ai_coding_harness.safety.policy import policy_for

# 这组用例只看工具配对自洽，边界取最宽的那个形态（不隐藏工具）以免混入别的变量
_POLICY = policy_for(ORCHESTRATE_FORM)
_SUBAGENT_POLICIES = {
    role: policy_for(role) for role in ("planner", "coder", "reviewer")
}

# -- 造数据 -----------------------------------------------------------------


def ai(*calls: dict[str, Any], content: str = "") -> AIMessage:
    return AIMessage(content=content, tool_calls=list(calls))


def call(name: str = "task", call_id: Any = "") -> dict[str, Any]:
    return {"name": name, "args": {}, "id": call_id}


def tool(name: str = "task", call_id: Any = "") -> ToolMessage:
    return ToolMessage(content="done", name=name, tool_call_id=call_id)


def ids(message: BaseMessage) -> list[str]:
    assert isinstance(message, AIMessage)
    return [c["id"] for c in message.tool_calls]


def responses(messages: list[BaseMessage]) -> list[ToolMessage]:
    return [m for m in messages if isinstance(m, ToolMessage)]


def repaired_or_fail(messages: list[BaseMessage]) -> list[BaseMessage]:
    out = repair_messages(messages)
    assert out is not None, "这份历史应当被判为需要修补"
    return out


# -- 空 id 配对 -------------------------------------------------------------


class TestBlankIds:
    def test_blank_call_and_blank_response_get_the_same_new_id(self):
        """空 id 的调用与空 id 的响应是**一对**，换 id 必须两边同时换。

        只换一边就等于制造新的不匹配 —— 那正是上游「假补」分支的处境。
        """
        history = [HumanMessage("做点事"), ai(call()), tool(), HumanMessage("继续")]
        out = repaired_or_fail(history)

        new_call_id = ids(out[1])[0]
        assert new_call_id.startswith("call_")
        assert len(new_call_id) == 29, "与端点自己的 id 同形，便于并排比对"
        assert out[2].tool_call_id == new_call_id
        assert len(responses(out)) == 1, "空串那一对配上了就不该再补一条"

    def test_none_id_is_replaced_too(self):
        """``None`` 与空串同罪：上游对 ``None`` 无条件放行，放了就是永久孤儿。"""
        history = [ai(call(call_id=None)), HumanMessage("继续")]
        out = repaired_or_fail(history)

        assert ids(out[0])[0].startswith("call_")
        assert out[1].tool_call_id == ids(out[0])[0]

    def test_two_blank_calls_pair_with_the_two_blank_responses_in_order(self):
        """一批五个空 id 调用是实测形状：响应按调用顺序写回，配对也得按顺序。"""
        history = [ai(call("read_file"), call("execute")), tool("read_file"), tool("execute")]
        out = repaired_or_fail(history)

        first, second = ids(out[0])
        assert first != second
        assert out[1].tool_call_id == first
        assert out[2].tool_call_id == second
        assert len(responses(out)) == 2

    def test_blank_orphan_after_an_answered_blank_is_still_repaired(self):
        """上游判据漏掉的那条：空串「已答」过一次，后面的空串就被永久豁免。

        上游算的是**全历史**的响应 id 集合 —— 集合里有 ``""``，于是这两条空 id 调用
        全被判成已答，一个字节都不写，每轮重试都 400。我们只看「这条 AI 消息之后」的
        响应，所以第二条必须补上。
        """
        history = [
            ai(call("get_pr_info")),
            tool("get_pr_info"),
            ai(call("task")),
            HumanMessage("换了个话题"),
        ]
        out = repaired_or_fail(history)

        assert out[1].tool_call_id == ids(out[0])[0]
        orphan = ids(out[2])[0]
        assert out[3].tool_call_id == orphan
        assert isinstance(out[3], ToolMessage)
        assert "未执行" in out[3].content

    def test_real_id_orphan_gets_a_synthetic_response(self):
        """真 id 的孤儿上游也能修 —— 这条是防我们改坏它的回归。"""
        history = [ai(call("task", "call_abc")), HumanMessage("继续")]
        out = repaired_or_fail(history)

        assert out[1].tool_call_id == "call_abc"
        assert out[1].name == "task"


class TestUnexecutedWording:
    def test_invalid_call_is_downcast_to_text_without_a_fake_tool_response(self):
        """invalid call 不能进 provider payload；它只保留一条普通文本说明。"""
        history = [
            AIMessage(
                content="",
                invalid_tool_calls=[{"name": "execute", "args": "坏的", "id": "call_x"}],
            ),
            HumanMessage("继续"),
        ]
        out = repaired_or_fail(history)

        rebuilt = out[0]
        assert isinstance(rebuilt, AIMessage)
        assert rebuilt.invalid_tool_calls == []
        assert "[工具调用未执行]" in rebuilt.content
        assert "execute" in rebuilt.content
        assert "参数" in rebuilt.content
        assert not responses(out), "invalid call 没有 tool_calls，不能伪造 ToolMessage"

    def test_invalid_call_is_cleared_while_valid_calls_survive(self):
        """invalid 字段必须清空；同一条消息里的正常调用仍保持可执行形态。"""
        history = [
            AIMessage(
                content="",
                tool_calls=[call("read_file", "call_a")],
                invalid_tool_calls=[{"name": "execute", "args": "坏的", "id": ""}],
            ),
            tool("read_file", "call_a"),
            HumanMessage("继续"),
        ]
        out = repaired_or_fail(history)

        rebuilt = out[0]
        assert isinstance(rebuilt, AIMessage)
        assert [c["name"] for c in rebuilt.tool_calls] == ["read_file"]
        assert rebuilt.invalid_tool_calls == []
        assert "execute" in rebuilt.content
        assert len(responses(out)) == 1
        assert responses(out)[0].tool_call_id == "call_a"

    def test_invalid_content_block_is_downcast_without_invalid_field(self):
        """真实坏 checkpoint 形状：invalid 在 content block 里，字段本身是空列表。"""
        history = [
            AIMessage(
                content=[
                    {
                        "type": "invalid_tool_call",
                        "id": "call_bad",
                        "name": "save_repo_memory",
                        "args": "{坏的 JSON",
                        "error": "Failed to parse tool call arguments as JSON",
                    }
                ],
                invalid_tool_calls=[],
            ),
            HumanMessage("继续"),
        ]

        out = repaired_or_fail(history)

        rebuilt = out[0]
        assert isinstance(rebuilt, AIMessage)
        assert rebuilt.invalid_tool_calls == []
        assert isinstance(rebuilt.content, list)
        assert all(
            not (
                isinstance(block, dict)
                and block.get("type") == "invalid_tool_call"
            )
            for block in rebuilt.content
        )
        assert "[工具调用未执行]" in str(rebuilt.content)
        assert "save_repo_memory" in str(rebuilt.content)
        assert not responses(out)

    def test_old_fake_response_for_an_invalid_call_is_removed(self):
        """旧修复逻辑生成过伪 ToolMessage；升级后要把这条孤儿一并清掉。"""
        history = [
            AIMessage(
                content="",
                invalid_tool_calls=[{"name": "execute", "args": "坏的", "id": "call_x"}],
            ),
            ToolMessage(content="工具未执行", tool_call_id="call_x", name="execute"),
            HumanMessage("继续"),
        ]

        out = repaired_or_fail(history)

        assert isinstance(out[0], AIMessage)
        assert out[0].invalid_tool_calls == []
        assert not responses(out)
        assert isinstance(out[1], HumanMessage)


# -- 无事可做 ---------------------------------------------------------------


class TestNoOp:
    def test_healthy_history_is_left_alone(self):
        history = [ai(call("task", "call_a")), tool("task", "call_a"), HumanMessage("好")]
        assert repair_messages(history) is None

    def test_second_pass_is_clean(self):
        """修一次就够：下一轮（以及每一轮）不该再为重写付费。"""
        history = [ai(call()), tool(), ai(call("task")), HumanMessage("继续")]
        once = repaired_or_fail(history)
        assert repair_messages(once) is None

    def test_a_response_far_below_still_counts_as_answered(self):
        """中间隔着一条别的消息也算答过 —— 判据是「之后有没有」，不是「紧挨着有没有」。

        紧挨着的那一串只是补配对时的落点（provider 要求它紧挨），不是「答没答」的依据。
        """
        history = [
            ai(call("task", "call_a")),
            AIMessage(content="自言自语"),
            tool("task", "call_a"),
            HumanMessage("好"),
        ]
        assert repair_messages(history) is None

    def test_empty_history(self):
        assert repair_messages([]) is None

    def test_non_ai_messages_pass_through(self):
        assert repair_messages([HumanMessage("嗨")]) is None

    def test_unrelated_ids_are_untouched(self):
        history = [ai(call("a", "call_k1"), call("b", "call_k2")), tool("a", "call_k1")]
        out = repaired_or_fail(history)

        assert ids(out[0]) == ["call_k1", "call_k2"], "已有的 id 不能动"
        assert {r.tool_call_id for r in responses(out)} == {"call_k1", "call_k2"}


# -- 中间件三个钩子 ---------------------------------------------------------


def _tool_request(name: str = "execute", call_id: Any = "call-1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": call_id},
        tool=None,
        state=None,
        runtime=None,
    )


class TestToolErrorGuard:
    async def test_exception_becomes_an_error_tool_message(self):
        """异常上抛会掀掉整个 superstep，同批工具的结果一起丢 —— 留下的就是孤儿。"""

        async def handler(_request: ToolCallRequest) -> ToolMessage:
            raise RuntimeError("沙箱掉线了")

        out = await ToolErrorGuardMiddleware().awrap_tool_call(_tool_request(), handler)

        assert isinstance(out, ToolMessage)
        assert out.tool_call_id == "call-1", "配不上 id 就还是个孤儿"
        assert out.status == "error"
        assert "RuntimeError" in out.content and "沙箱掉线了" in out.content

    async def test_blank_id_stays_a_str(self):
        """``ToolMessage.tool_call_id`` 是必填 ``str``，写成 None 会当场校验失败。"""

        async def handler(_request: ToolCallRequest) -> ToolMessage:
            raise ValueError("x")

        out = await ToolErrorGuardMiddleware().awrap_tool_call(
            _tool_request(call_id=""), handler
        )

        assert isinstance(out, ToolMessage)
        assert out.tool_call_id == ""

    async def test_interrupt_is_not_swallowed(self):
        """中断靠异常穿过工具节点冒到图上去，吞了它就没法打断。"""

        async def handler(_request: ToolCallRequest) -> ToolMessage:
            raise GraphBubbleUp

        with pytest.raises(GraphBubbleUp):
            await ToolErrorGuardMiddleware().awrap_tool_call(_tool_request(), handler)

    async def test_normal_result_passes_through(self):
        message = tool("execute", "call-1")

        async def handler(_request: ToolCallRequest) -> ToolMessage:
            return message

        assert await ToolErrorGuardMiddleware().awrap_tool_call(_tool_request(), handler) is message


def _model_request() -> ModelRequest:
    # 这个中间件只看响应，不碰 request —— 给个最小可构造的实例就够
    return ModelRequest(model=None, messages=[])  # type: ignore[arg-type]


async def _through_id_middleware(response: Any) -> Any:
    async def handler(_request: ModelRequest) -> Any:
        return response

    return await ToolCallIdMiddleware().awrap_model_call(_model_request(), handler)


class TestToolCallId:
    async def test_blank_id_is_normalized_at_the_model_exit(self):
        """在同一轮里就补上：ToolNode 是按 state 里这条 AI 消息的 id 写回响应的。"""
        out = await _through_id_middleware(
            ModelResponse(result=[ai(call("read_file")), ai(call("execute"))])
        )

        first, second = ids(out.result[0])[0], ids(out.result[1])[0]
        assert first.startswith("call_") and second.startswith("call_")
        assert first != second, "同一条消息里的多个空 id 不能补成同一个"

    async def test_none_id_is_normalized(self):
        out = await _through_id_middleware(ai(call(call_id=None)))
        assert ids(out)[0].startswith("call_")

    async def test_invalid_call_is_downcast_at_the_model_exit(self):
        """坏调用在写入 checkpoint 前就降级，避免下一轮所有 provider 一起 400。"""
        response = ModelResponse(
            result=[
                AIMessage(
                    content="",
                    invalid_tool_calls=[
                        {
                            "name": "execute",
                            "args": "这不是 JSON",
                            "id": "call_bad",
                            "error": "JSONDecodeError",
                        }
                    ],
                )
            ]
        )

        out = await _through_id_middleware(response)
        message = out.result[0]

        assert isinstance(message, AIMessage)
        assert message.invalid_tool_calls == []
        assert message.tool_calls == []
        assert "[工具调用未执行]" in message.content
        assert "JSONDecodeError" in message.content

    async def test_real_ids_are_returned_as_is(self):
        """没命中就别造新对象：状态无谓更新会连带下游一堆重算。"""
        response = ModelResponse(result=[ai(call("read_file", "call_real"))])
        assert await _through_id_middleware(response) is response

    async def test_extended_response_is_unwrapped(self):
        """外层中间件会用 ExtendedModelResponse 捎带一条 Command —— 漏认就等于放行。"""
        extended = ExtendedModelResponse(
            model_response=ModelResponse(result=[ai(call())]),
            command=Command(goto="model"),
        )
        out = await _through_id_middleware(extended)

        assert ids(out.model_response.result[0])[0].startswith("call_")
        assert out.command.goto == "model"

    async def test_non_ai_message_passes_through(self):
        message = HumanMessage("嗨")
        assert await _through_id_middleware(message) is message


class TestHistoryRepairHook:
    async def test_dirty_history_is_rewritten_whole(self):
        """孤儿可能在历史中间，图输入只能追加 —— 只能整表重写。"""
        state = {
            "messages": [ai(call("task")), HumanMessage("继续"), ai(call("task"))]
        }
        out = await HistoryRepairMiddleware().abefore_agent(state, None)  # type: ignore[arg-type]

        assert out is not None
        written = out["messages"]
        assert written[0].id == REMOVE_ALL_MESSAGES, "整表重写必须先清空再追加"
        # 原始 3 条，两个孤儿各补一条
        assert len(written) == 1 + 3 + 2

    async def test_clean_history_writes_nothing(self):
        state = {"messages": [ai(call("task", "call_a")), tool("task", "call_a")]}
        assert await HistoryRepairMiddleware().abefore_agent(state, None) is None  # type: ignore[arg-type]

    async def test_missing_messages_key_is_safe(self):
        assert await HistoryRepairMiddleware().abefore_agent({}, None) is None  # type: ignore[arg-type]


# -- 装配点 -----------------------------------------------------------------


def _openai_shaped_model() -> BaseChatModel:
    """provider 解析成 ``openai`` 的客户端 —— 只有它能命中 ``graph.py`` 注册的 profile。

    用假 key 与不可达的 base_url：这一组用例只构建图，不发请求。
    """
    return init_chat_model(
        model="glm-4.6",
        model_provider="openai",
        api_key="dummy",
        base_url="http://127.0.0.1:1/v1",
    )


def _agents(**kwargs: Any) -> Any:
    return build_agent(
        model=kwargs.pop("model", _openai_shaped_model()),
        backend=None,  # type: ignore[arg-type]
        tools=[],
        store=None,
        checkpointer=None,
        system_prompt="SYS",
        policy=_POLICY,
        subagents=kwargs.pop("subagents", None),
        skills=None,
        **kwargs,
    )


class TestWiring:
    async def test_history_repair_is_mounted_as_a_node(self):
        assert "HistoryRepairMiddleware.before_agent" in set(_agents().get_graph().nodes)

    async def test_upstream_patch_is_excluded(self):
        """上游那份判据会被空 id 骗过，留着它只会白白多写一遍（且写的是错的）。

        这条同时钉住 ``excluded_middleware`` 的覆盖校验：条目要是没在任何栈里匹配到，
        deepagents 会直接抛错，图根本建不起来。
        """
        assert "PatchToolCallsMiddleware.before_agent" not in set(_agents().get_graph().nodes)

    async def test_every_subagent_carries_the_three_hooks(self):
        """主智能体的自定义中间件不下传，三个子智能体各挂一份（它们自己也跑工具）。"""
        specs = build_subagents(
            tools_by_role={"planner": [], "coder": [], "reviewer": []},
            model_for=lambda role: _openai_shaped_model(),
            policies=_SUBAGENT_POLICIES,
        )
        assert len(specs) == 3
        for spec in specs:
            kinds = [type(middleware) for middleware in spec["middleware"]]
            for expected in (
                ToolCallIdMiddleware,
                ToolErrorGuardMiddleware,
                HistoryRepairMiddleware,
            ):
                assert expected in kinds, f"{spec['name']} 少了 {expected.__name__}"

    async def test_openai_shaped_subagents_build_with_the_exclusion(self):
        """子智能体的模型也是 openai 形态 —— 排除对它们同样生效，覆盖校验也要过。

        条目在任何栈里都没匹配到，deepagents 会直接抛错，这个 build 就建不起来。
        """
        specs = build_subagents(
            tools_by_role={"planner": []},
            model_for=lambda role: _openai_shaped_model(),
            policies=_SUBAGENT_POLICIES,
        )
        agent = _agents(subagents=specs)
        nodes = set(agent.get_graph().nodes)
        assert "HistoryRepairMiddleware.before_agent" in nodes
        assert "PatchToolCallsMiddleware.before_agent" not in nodes
