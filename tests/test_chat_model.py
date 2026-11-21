"""思考正文的补丁层：保留当轮展示，但禁止历史 reasoning 回传。

三层都要钉：

① ``_convert_chunk_to_generation_chunk`` 从**原始 chunk** 里取 ``reasoning_content``；
② ``ReasoningAIMessageChunk.content_blocks`` 把它还原成 reasoning 块 —— OpenAI 的
   provider translator 不读 ``additional_kwargs``，只补 ① 到不了 v3 事件桥；
③ ``_get_request_payload`` 从 outbound 副本中移除历史思考，正文与工具配对保持原样。

不联网：chunk 是手造的 dict，和 ``chunk.model_dump()`` 的形状一致。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk

from ai_coding_harness.agent.chat_model import (
    ReasoningAIMessage,
    ReasoningAIMessageChunk,
    ReasoningContentChatOpenAI,
)
from ai_coding_harness.agent.llm import build_model
from ai_coding_harness.config import ModelConfig, Settings

_BASE = "http://127.0.0.1:1/v1"


def _model() -> ReasoningContentChatOpenAI:
    return ReasoningContentChatOpenAI(
        model="m", api_key="k", base_url=_BASE, stream_usage=True
    )


def _chunk(
    reasoning: str | None = None,
    *,
    content: str = "",
    role: str | None = "assistant",
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    delta: dict[str, Any] = {"content": content}
    if role is not None:
        delta["role"] = role
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": None},
        ]
    }


def _generation(
    chunk: dict[str, Any], default_chunk_class: type = AIMessageChunk
) -> Any:
    generation = _model()._convert_chunk_to_generation_chunk(
        chunk, default_chunk_class, {}
    )
    assert generation is not None
    return generation


def test_reasoning_content_becomes_a_content_block() -> None:
    generation = _generation(_chunk("先算乘法"))

    message = generation.message
    assert message.additional_kwargs["reasoning_content"] == "先算乘法"
    assert message.content_blocks == [{"type": "reasoning", "reasoning": "先算乘法"}]


def test_stream_chunks_merge_reasoning_and_text() -> None:
    first = _generation(_chunk("先算")).message
    second = _generation(_chunk("乘法", content="425")).message

    merged = first + second

    assert isinstance(merged, ReasoningAIMessageChunk)
    assert merged.additional_kwargs["reasoning_content"] == "先算乘法"
    assert {"type": "reasoning", "reasoning": "先算乘法"} in merged.content_blocks
    assert {"type": "text", "text": "425"} in merged.content_blocks


def test_reasoning_content_is_not_replayed_in_the_next_chat_request() -> None:
    """Real-time reasoning is display-only; tool history must survive without it."""
    assistant = AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "先检查目录，再执行命令"},
        tool_calls=[
            {
                "name": "execute",
                "args": {"command": "ls"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )

    payload = _model()._get_request_payload(
        [assistant, ToolMessage(content="README.md", tool_call_id="call_1")]
    )

    assert "reasoning_content" not in payload["messages"][0]
    assert payload["messages"][0]["tool_calls"][0]["id"] == "call_1"
    assert payload["messages"][1]["role"] == "tool"


def test_reasoning_block_is_not_replayed_in_the_next_chat_request() -> None:
    """Reasoning blocks from old checkpoints are stripped from the request copy."""
    assistant = AIMessage(
        content=[{"type": "reasoning", "reasoning": "先算乘法"}],
    )

    payload = _model()._get_request_payload([assistant])

    assert "reasoning_content" not in payload["messages"][0]


def test_a_chunk_without_reasoning_gains_no_empty_block() -> None:
    generation = _generation(_chunk(content="425"))

    assert "reasoning_content" not in generation.message.additional_kwargs
    assert generation.message.content_blocks == [{"type": "text", "text": "425"}]


def test_non_stream_response_keeps_reasoning_content() -> None:
    result = _model()._create_chat_result(
        {
            "model": "m",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "425",
                        "reasoning_content": "先算乘法",
                    },
                    "finish_reason": "stop",
                }
            ],
        }
    )

    message = result.generations[0].message
    assert isinstance(message, ReasoningAIMessage)
    assert message.additional_kwargs["reasoning_content"] == "先算乘法"
    assert message.content_blocks == [
        {"type": "reasoning", "reasoning": "先算乘法"},
        {"type": "text", "text": "425"},
    ]


def test_tool_calls_survive_when_the_default_class_is_the_subclass() -> None:
    """后续 chunk 的 role 是 None，而 default_class 已经是子类 —— 父类会走兜底分支，
    不解析 tool_calls。补 role 后必须还能拿到工具调用（2026-10-19 真机回归）。"""
    chunk = _chunk(
        role=None,
        tool_calls=[
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "execute", "arguments": ""},
            }
        ],
    )

    message = _generation(chunk, ReasoningAIMessageChunk).message

    assert message.tool_call_chunks[0]["name"] == "execute"
    assert message.tool_calls[0]["name"] == "execute"


def test_reasoning_and_tool_calls_merge_together() -> None:
    first = _generation(
        _chunk(
            "想",
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "execute", "arguments": ""},
                }
            ],
        ),
        ReasoningAIMessageChunk,
    ).message
    second = _generation(
        _chunk(
            role=None,
            tool_calls=[
                {
                    "index": 0,
                    "id": None,
                    "type": None,
                    "function": {"name": None, "arguments": '{"command": "ls"}'},
                }
            ],
        ),
        ReasoningAIMessageChunk,
    ).message

    merged = first + second

    assert merged.additional_kwargs["reasoning_content"] == "想"
    assert merged.tool_calls[0]["name"] == "execute"
    assert merged.tool_calls[0]["args"] == {"command": "ls"}


def test_an_existing_reasoning_block_is_not_duplicated() -> None:
    message = ReasoningAIMessageChunk(
        content=[{"type": "reasoning", "reasoning": "想"}],
        additional_kwargs={"reasoning_content": "想"},
        response_metadata={"model_provider": "openai", "output_version": "v1"},
    )

    reasoning = [b for b in message.content_blocks if b.get("type") == "reasoning"]
    assert len(reasoning) == 1


def test_responses_reasoning_summary_block_is_already_protocol_ready() -> None:
    """Responses API 直接给 reasoning block；不要再把它改写成第三方 kwargs 形状。"""
    message = ReasoningAIMessageChunk(
        content=[
            {
                "type": "reasoning",
                "reasoning": "先定位依赖版本，再检查 Timeout 构造参数。",
            }
        ],
        response_metadata={"model_provider": "openai", "output_version": "v1"},
    )

    assert message.content_blocks == [
        {
            "type": "reasoning",
            "reasoning": "先定位依赖版本，再检查 Timeout 构造参数。",
        }
    ]


def test_responses_payload_drops_all_historical_reasoning() -> None:
    model = ReasoningContentChatOpenAI(
        model="gpt-6.1-sol",
        api_key="k",
        base_url=_BASE,
        use_responses_api=True,
        output_version="responses/v1",
    )
    messages = [
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_previous",
                    "reasoning": "来自上一条 Responses 调用的思考",
                },
                {"type": "text", "text": "旧回答"},
            ]
        ),
        HumanMessage(content="继续"),
    ]

    payload = model._get_request_payload(messages)

    assert payload["input"]
    assert not any(item.get("type") == "reasoning" for item in payload["input"])
    assert any(
        item.get("type") == "message" and item.get("role") == "user"
        for item in payload["input"]
    )


def test_build_model_uses_the_reasoning_client() -> None:
    settings = Settings(
        _env_file=None,
        agent_models={
            "qa": ModelConfig(model="m", api_key="k", base_url=_BASE),
        },
    )

    assert isinstance(build_model(settings, role="qa"), ReasoningContentChatOpenAI)


async def test_v3_events_carry_reasoning_deltas() -> None:
    """端到端到事件层：`content-block-delta` 里必须是 ``reasoning-delta``，
    否则 ``stream.py`` 的翻译层拿不到思考文本。"""

    class Fake(ReasoningContentChatOpenAI):
        async def _astream(
            self,
            messages: Any,
            stop: Any = None,
            run_manager: Any = None,
            **kwargs: Any,
        ):
            for reasoning, content in (("先算", ""), ("乘法", "425")):
                yield ChatGenerationChunk(
                    message=ReasoningAIMessageChunk(
                        content=content,
                        additional_kwargs={"reasoning_content": reasoning},
                        response_metadata={"model_provider": "openai"},
                    )
                )

    model = Fake(model="m", api_key="k", base_url=_BASE)
    stream = await model.astream_events("hi", version="v3")
    deltas = [
        event["delta"]
        async for event in stream
        if event["event"] == "content-block-delta"
    ]

    assert {"type": "reasoning-delta", "reasoning": "先算"} in deltas
    assert {"type": "reasoning-delta", "reasoning": "乘法"} in deltas


def test_sync_v3_stream_events_carry_reasoning_deltas() -> None:
    """同步 ``stream_events(version="v3")`` 走同一个 content-block 出口。"""

    class Fake(ReasoningContentChatOpenAI):
        def _stream(
            self,
            messages: Any,
            stop: Any = None,
            run_manager: Any = None,
            **kwargs: Any,
        ):
            yield ChatGenerationChunk(
                message=ReasoningAIMessageChunk(
                    content="",
                    additional_kwargs={"reasoning_content": "同步思考"},
                    response_metadata={"model_provider": "openai"},
                )
            )

    model = Fake(model="m", api_key="k", base_url=_BASE)
    stream = model.stream_events("hi", version="v3")
    deltas = [
        event["delta"]
        for event in stream
        if event["event"] == "content-block-delta"
    ]

    assert {"type": "reasoning-delta", "reasoning": "同步思考"} in deltas
