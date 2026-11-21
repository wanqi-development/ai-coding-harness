"""ASGI-side RunHub that submits work to separate Agent workers."""

from __future__ import annotations

from typing import Any

from ai_coding_harness.server.run_queue import RedisRunQueue
from ai_coding_harness.server.runs import RunHub, RunInProgress


class DistributedRunHub(RunHub):
    """Gateway coordinator: no local Agent task is started here."""

    def __init__(self, service, *, queue: RedisRunQueue, **kwargs: Any) -> None:
        kwargs["wait_for_worker"] = True
        super().__init__(service, **kwargs)
        self._queue = queue

    async def start(
        self,
        session_id: str,
        session,
        message: str,
        *,
        clarification_id: str | None = None,
        choice: int | str | None = None,
        answers: list[dict[str, Any]] | None = None,
        open_buffer: bool = True,
    ) -> None:
        if await self._queue.has_job(session_id):
            raise RunInProgress(session_id)
        if await self._buffer.exists(session_id) and not await self._buffer.is_done(
            session_id
        ):
            raise RunInProgress(session_id)
        if not await self._queue.reserve(session_id):
            raise RunInProgress(session_id)
        try:
            await self._buffer.open(session_id)
            await self._queue.publish_reserved(
                session_id=session_id,
                message=message,
                clarification_id=clarification_id,
                choice=choice,
                answers=answers,
            )
        except Exception:
            await self._queue.release_marker(session_id)
            raise

    async def is_running(self, session_id: str) -> bool:
        if await self._queue.has_job(session_id):
            return True
        return await super().is_running(session_id)


__all__ = ["DistributedRunHub"]
