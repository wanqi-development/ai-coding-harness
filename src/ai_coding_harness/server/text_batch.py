"""Batch adjacent assistant text frames before they enter the SSE run buffer.

The model can emit one delta every few characters. Sending every delta through Redis and
SSE adds transport overhead without improving what the user can see. Only contiguous
``ai`` text frames are combined; every other frame is a flush boundary so tool calls,
reasoning, todos, approvals, usage and completion keep their ordering.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class TextFrameBatcher:
    """Combine contiguous assistant-text frames with the same run identity."""

    def __init__(
        self,
        *,
        max_chars: int = 10,
        flush_seconds: float = 0.04,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_chars = max(1, int(max_chars))
        self._flush_seconds = max(0.0, float(flush_seconds))
        self._clock = clock
        self._pending: dict[str, Any] | None = None
        self._started_at = 0.0

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    def push(self, frame: dict[str, Any]) -> list[dict[str, Any]]:
        """Add one frame and return every frame that is now ready to be written."""
        if not self._is_batchable(frame):
            ready = self.flush()
            ready.append(frame)
            return ready

        now = self._clock()
        ready: list[dict[str, Any]] = []
        if self._pending is not None and not self._same_stream(self._pending, frame):
            ready.extend(self.flush())

        if self._pending is None:
            self._pending = dict(frame)
            self._started_at = now
        else:
            self._pending["content"] += frame["content"]

        if (
            self._max_chars <= 1
            or len(self._pending["content"]) >= self._max_chars
            or self.flush_delay(now=now) == 0
        ):
            ready.extend(self.flush())
        return ready

    def flush_due(self, now: float | None = None) -> list[dict[str, Any]]:
        """Return the pending text when its maximum latency has elapsed."""
        if self.flush_delay(now=now) != 0:
            return []
        return self.flush()

    def flush_delay(self, *, now: float | None = None) -> float | None:
        """Seconds until the pending batch must flush; ``None`` when no timer is needed."""
        if self._pending is None or self._flush_seconds <= 0:
            return None
        current = self._clock() if now is None else now
        return max(0.0, self._started_at + self._flush_seconds - current)

    def flush(self) -> list[dict[str, Any]]:
        """Return and clear the pending text frame."""
        if self._pending is None:
            return []
        frame = self._pending
        self._pending = None
        self._started_at = 0.0
        return [frame]

    @staticmethod
    def _is_batchable(frame: dict[str, Any]) -> bool:
        return (
            frame.get("type") == "ai"
            and isinstance(frame.get("content"), str)
            and bool(frame["content"])
        )

    @staticmethod
    def _same_stream(left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_shape = {key: value for key, value in left.items() if key != "content"}
        right_shape = {key: value for key, value in right.items() if key != "content"}
        return left_shape == right_shape


__all__ = ["TextFrameBatcher"]
