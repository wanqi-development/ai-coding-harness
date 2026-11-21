"""Strip historical model reasoning before a new model request.

Reasoning is useful as a live UI stream, but it has no portable meaning across
models or protocols. A later request may be served by a different provider, so
replaying ``reasoning_content``, Responses reasoning items, or Anthropic
``thinking`` blocks can either be rejected or leak an unrelated model's
internal state. This module removes those fields from a request copy only; the
checkpoint still retains the original messages for audit and replay.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

_REASONING_KEYS = frozenset(
    {
        "reasoning",
        "reasoning_content",
        "reasoning_details",
        "thinking",
    }
)


def _is_reasoning_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    block_type = str(block.get("type") or "").strip().lower().replace("_", "-")
    return (
        block_type.startswith("reasoning")
        or block_type in {"thinking", "redacted-thinking"}
    )


def _without_reasoning(message: AIMessage) -> AIMessage | None:
    kwargs = dict(message.additional_kwargs)
    removed_keys = [
        key
        for key in kwargs
        if str(key).strip().lower() in _REASONING_KEYS
    ]
    for key in removed_keys:
        kwargs.pop(key, None)

    content = message.content
    changed = bool(removed_keys)
    if isinstance(content, list):
        filtered = [block for block in content if not _is_reasoning_block(block)]
        if len(filtered) != len(content):
            content = filtered
            changed = True
    if not changed:
        return None

    return message.model_copy(
        update={
            "content": content,
            "additional_kwargs": kwargs,
        }
    )


def strip_reasoning_history(
    messages: Sequence[BaseMessage],
) -> list[BaseMessage] | None:
    """Return a sanitized request list, or ``None`` when nothing needs removal.

    Only ``AIMessage`` instances are candidates: system, human, and tool
    messages never carry historical reasoning. Tool-call payloads and response
    text remain byte-for-byte intact, preserving tool-call/output pairing.
    """
    cleaned: list[BaseMessage] = []
    changed = False
    for message in messages:
        replacement = (
            _without_reasoning(message) if isinstance(message, AIMessage) else None
        )
        cleaned.append(replacement if replacement is not None else message)
        changed = changed or replacement is not None
    return cleaned if changed else None
