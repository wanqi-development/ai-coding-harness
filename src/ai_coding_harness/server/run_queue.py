"""Redis Streams transport between the ASGI gateway and Agent workers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentJob:
    message_id: str
    session_id: str
    message: str
    clarification_id: str | None = None
    choice: int | str | None = None
    answers: list[dict[str, Any]] | None = None


class RedisRunQueue:
    """At-least-once job delivery with a per-session queue marker.

    The marker prevents duplicate POSTs from creating two jobs before a worker
    starts. Redis pending entries are reclaimed after ``claim_idle_ms`` when a
    worker dies.
    """

    def __init__(
        self,
        client: Any,
        *,
        prefix: str,
        stream: str = "agent-jobs",
        group: str = "agent-workers",
        claim_idle_ms: int = 60_000,
        marker_ttl_seconds: int = 3600,
        active_ttl_seconds: int = 90,
    ) -> None:
        self._redis = client
        self._prefix = prefix
        self._stream = f"{prefix}{stream}"
        self._group = group
        self._claim_idle_ms = max(1_000, int(claim_idle_ms))
        self._marker_ttl = max(1, int(marker_ttl_seconds))
        self._active_ttl = max(10, int(active_ttl_seconds))
        self._group_ready = False

    def _queued_key(self, session_id: str) -> str:
        return f"{self._prefix}queued:{session_id}"

    def _active_key(self, session_id: str) -> str:
        return f"{self._prefix}active-job:{session_id}"

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(
                self._stream,
                self._group,
                id="0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def enqueue(
        self,
        *,
        session_id: str,
        message: str,
        clarification_id: str | None = None,
        choice: int | str | None = None,
        answers: list[dict[str, Any]] | None = None,
    ) -> bool:
        if not await self.reserve(session_id):
            return False
        try:
            await self.publish_reserved(
                session_id=session_id,
                message=message,
                clarification_id=clarification_id,
                choice=choice,
                answers=answers,
            )
        except Exception:
            await self.release_marker(session_id)
            raise
        return True

    async def reserve(self, session_id: str) -> bool:
        marker = await self._redis.set(
            self._queued_key(session_id),
            "1",
            ex=self._marker_ttl,
            nx=True,
        )
        return bool(marker)

    async def publish_reserved(
        self,
        *,
        session_id: str,
        message: str,
        clarification_id: str | None = None,
        choice: int | str | None = None,
        answers: list[dict[str, Any]] | None = None,
    ) -> None:
        payload = {
            "session_id": session_id,
            "message": message,
            "clarification_id": clarification_id,
            "choice": choice,
            "answers": answers,
        }
        await self._redis.xadd(
            self._stream,
            {"job": json.dumps(payload, ensure_ascii=False)},
        )

    async def has_job(self, session_id: str) -> bool:
        return bool(await self._redis.exists(self._queued_key(session_id)))

    async def claim(
        self,
        *,
        consumer: str,
        block_ms: int = 2_000,
    ) -> AgentJob | None:
        await self._ensure_group()
        pending = await self._redis.xautoclaim(
            self._stream,
            self._group,
            consumer,
            self._claim_idle_ms,
            start_id="0-0",
            count=1,
        )
        messages = pending[1] if isinstance(pending, (list, tuple)) and len(pending) > 1 else []
        if messages:
            decoded = await self._decode(message_id=messages[0][0], fields=messages[0][1])
            if decoded is not None and await self._active(decoded.session_id):
                messages = []
            elif decoded is not None:
                await self.activate(decoded.session_id, consumer=consumer)
                return decoded
            else:
                return None
        if not messages:
            rows = await self._redis.xreadgroup(
                self._group,
                consumer,
                {self._stream: ">"},
                count=1,
                block=block_ms,
            )
            messages = rows[0][1] if rows else []
        if not messages:
            return None
        message_id, fields = messages[0]
        decoded = await self._decode(message_id=message_id, fields=fields)
        if decoded is not None:
            await self.activate(decoded.session_id, consumer=consumer)
        return decoded

    async def _decode(self, *, message_id: str, fields: dict) -> AgentJob | None:
        raw = fields.get(b"job") or fields.get("job")
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            payload = json.loads(str(raw))
            return AgentJob(
                message_id=str(message_id),
                session_id=str(payload["session_id"]),
                message=str(payload.get("message") or ""),
                clarification_id=payload.get("clarification_id"),
                choice=payload.get("choice"),
                answers=payload.get("answers"),
            )
        except Exception:
            logger.exception("无法解析 Agent job，直接确认丢弃：%s", message_id)
            await self.ack(session_id="", message_id=str(message_id))
            return None

    async def activate(self, session_id: str, *, consumer: str) -> None:
        await self._redis.set(
            self._active_key(session_id),
            consumer,
            ex=self._active_ttl,
        )

    async def touch(self, session_id: str, *, consumer: str) -> None:
        await self.activate(session_id, consumer=consumer)

    async def _active(self, session_id: str) -> bool:
        return bool(await self._redis.exists(self._active_key(session_id)))

    async def ack(self, *, session_id: str, message_id: str) -> None:
        await self._redis.xack(self._stream, self._group, message_id)
        if session_id:
            await self._redis.delete(
                self._queued_key(session_id),
                self._active_key(session_id),
            )

    async def release_marker(self, session_id: str) -> None:
        await self._redis.delete(self._queued_key(session_id))

    async def remove_session(self, session_id: str) -> None:
        await self.release_marker(session_id)


__all__ = ["AgentJob", "RedisRunQueue"]
