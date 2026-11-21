"""Keep historical reasoning out of every model request."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)

from ai_coding_harness.agent.reasoning_history import strip_reasoning_history


class ReasoningHistoryMiddleware(AgentMiddleware):
    """Project a reasoning-free request without mutating graph state.

    The middleware is protocol-neutral: provider-specific thinking blocks and
    third-party reasoning fields are all removed from the copy sent to the
    model. The current response is untouched, so live reasoning events and
    checkpoint history remain available outside the outbound request.
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        cleaned = strip_reasoning_history(request.messages)
        if cleaned is not None:
            request = request.override(messages=cleaned)
        return await handler(request)
