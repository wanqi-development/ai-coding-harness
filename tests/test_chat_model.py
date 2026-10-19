"""思考正文的补丁层：ChatOpenAI 丢掉 ``reasoning_content``，这里把它捞回来。

两层都要钉：

① ``_convert_chunk_to_generation_chunk`` 从**原始 chunk** 里取 ``reasoning_content``；
② ``ReasoningAIMessageChunk.content_blocks`` 把它还原成 reasoning 块 —— OpenAI 的
   provider translator 不读 ``additional_kwargs``，只补 ① 到不了 v3 事件桥。

不联网：chunk 是手造的 dict，和 ``chunk.model_dump()`` 的形状一致。
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from ai_coding_harness.agent.chat_model import (
    ReasoningAIMessageChunk,
    ReasoningChatOpenAI,
)
from ai_coding_harness.agent.llm import build_model
from ai_coding_harness.config import ModelConfig, Settings

_BASE = "http://127.0.0.1:1/v1"


def _model() -> ReasoningChatOpenAI:
    return ReasoningChatOpenAI(
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


def test_a_chunk_without_reasoning_gains_no_empty_block() -> None:
    generation = _generation(_chunk(content="425"))

    assert "reasoning_content" not in generation.message.additional_kwargs
    assert generation.message.content_blocks == [{"type": "text", "text": "425"}]


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


def test_build_model_uses_the_reasoning_client() -> None:
    settings = Settings(
        _env_file=None,
        agent_models={
            "qa": ModelConfig(model="m", api_key="k", base_url=_BASE),
        },
    )

    assert isinstance(build_model(settings, role="qa"), ReasoningChatOpenAI)


async def test_v3_events_carry_reasoning_deltas() -> None:
    """端到端到事件层：`content-block-delta` 里必须是 ``reasoning-delta``，
    否则 ``stream.py`` 的翻译层拿不到思考文本。"""

    class Fake(GenericFakeChatModel):
        def _get_ls_params(self, **kwargs: object) -> dict[str, str]:
            return {"ls_provider": "openai"}

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

    model = Fake(messages=iter([AIMessage(content="ok")]))
    stream = await model.astream_events("hi", version="v3")
    deltas = [
        event["delta"]
        async for event in stream
        if event["event"] == "content-block-delta"
    ]

    assert {"type": "reasoning-delta", "reasoning": "先算"} in deltas
    assert {"type": "reasoning-delta", "reasoning": "乘法"} in deltas
