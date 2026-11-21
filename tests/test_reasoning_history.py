"""Historical reasoning is stripped before every model protocol."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ai_coding_harness.agent.middleware import ReasoningHistoryMiddleware
from ai_coding_harness.agent.middleware.summarization import (
    HarnessSummarizationMiddleware,
)
from ai_coding_harness.agent.reasoning_history import strip_reasoning_history


def _mixed_history() -> tuple[
    AIMessage,
    ToolMessage,
    HumanMessage,
]:
    assistant = AIMessage(
        id="ai-1",
        content=[
            {"type": "reasoning", "reasoning": "Responses summary"},
            {"type": "thinking", "thinking": "Anthropic thought"},
            {"type": "redacted_thinking", "data": "encrypted-thought"},
            {"type": "text", "text": "检查完成了。"},
            {
                "type": "tool_call",
                "id": "call_1",
                "name": "execute",
                "args": {"command": "ls"},
            },
        ],
        additional_kwargs={
            "reasoning_content": "第三方网关思考",
            "reasoning_details": [{"text": "structured thought"}],
            "thinking": {"text": "另一家的形状"},
            "trace": "keep-me",
        },
        tool_calls=[
            {
                "id": "call_1",
                "name": "execute",
                "args": {"command": "ls"},
                "type": "tool_call",
            }
        ],
    )
    return (
        assistant,
        ToolMessage(content="README.md", tool_call_id="call_1"),
        HumanMessage(content="继续"),
    )


def test_strip_reasoning_keeps_text_tools_and_unrelated_metadata() -> None:
    assistant, tool, human = _mixed_history()

    cleaned = strip_reasoning_history([assistant, tool, human])

    assert cleaned is not None
    ai = cleaned[0]
    assert isinstance(ai, AIMessage)
    assert ai.content == [
        {"type": "text", "text": "检查完成了。"},
        {
            "type": "tool_call",
            "id": "call_1",
            "name": "execute",
            "args": {"command": "ls"},
        },
    ]
    assert ai.additional_kwargs == {"trace": "keep-me"}
    assert ai.tool_calls == assistant.tool_calls
    assert cleaned[1] is tool
    assert cleaned[2] is human


def test_strip_reasoning_does_not_mutate_the_checkpoint_message() -> None:
    assistant, _, _ = _mixed_history()
    original_content = assistant.content
    original_kwargs = dict(assistant.additional_kwargs)

    strip_reasoning_history([assistant])

    assert assistant.content == original_content
    assert assistant.additional_kwargs == original_kwargs


async def test_middleware_strips_every_protocol_shape_before_the_model() -> None:
    assistant, tool, human = _mixed_history()
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> Any:
        seen.append(request)
        return AIMessage(content="done")

    await ReasoningHistoryMiddleware().awrap_model_call(
        ModelRequest(model=None, messages=[assistant, tool, human]),  # type: ignore[arg-type]
        handler,
    )

    assert len(seen) == 1
    assert isinstance(seen[0].messages[0], AIMessage)
    assert seen[0].messages[0].content == [
        {"type": "text", "text": "检查完成了。"},
        {
            "type": "tool_call",
            "id": "call_1",
            "name": "execute",
            "args": {"command": "ls"},
        },
    ]
    assert seen[0].messages[0].additional_kwargs == {"trace": "keep-me"}
    assert seen[0].messages[1].tool_call_id == "call_1"


async def test_untouched_history_is_passed_through_without_a_copy(
    monkeypatch: Any,
) -> None:
    messages = [HumanMessage(content="hello")]
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> Any:
        seen.append(request)
        return AIMessage(content="done")

    request = ModelRequest(model=None, messages=messages)  # type: ignore[arg-type]
    await ReasoningHistoryMiddleware().awrap_model_call(request, handler)

    assert seen[0] is request
    assert seen[0].messages is messages


async def test_summary_request_gets_reasoning_free_messages(
    monkeypatch: Any,
) -> None:
    assistant, tool, human = _mixed_history()
    seen: list[list[Any]] = []

    async def fake_summary(self: Any, messages: list[Any]) -> str:
        seen.append(messages)
        return "summary"

    monkeypatch.setattr(
        "ai_coding_harness.agent.middleware.summarization."
        "SummarizationMiddleware._acreate_summary",
        fake_summary,
    )
    middleware = object.__new__(HarnessSummarizationMiddleware)

    result = await middleware._acreate_summary([assistant, tool, human])

    assert result == "summary"
    assert "reasoning" not in seen[0][0].additional_kwargs
    assert seen[0][0].additional_kwargs == {"trace": "keep-me"}
