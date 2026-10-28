"""Versioned, expiring cache for reconstructable session snapshots.

The cache is deliberately not a serialized ``Session`` runtime object.  It carries only
stable session identity and credential fields, while user runtime settings continue to be
loaded from their own source.  Redis values are encrypted with AES-GCM.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, replace
from typing import Any

from ai_coding_harness.config import Settings
from ai_coding_harness.core.codeplatform import Platform, RepoSpec

logger = logging.getLogger(__name__)

SESSION_SNAPSHOT_SCHEMA_VERSION = 1


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _encryption_key(raw: str) -> bytes:
    text = (raw or "").strip()
    if not text:
        raise ValueError("SESSION_CACHE_ENCRYPTION_KEY 未配置")
    try:
        key = bytes.fromhex(text) if len(text) == 64 else _b64d(text)
    except Exception as exc:
        raise ValueError("SESSION_CACHE_ENCRYPTION_KEY 必须是 64 位十六进制或 base64") from exc
    if len(key) != 32:
        raise ValueError("SESSION_CACHE_ENCRYPTION_KEY 解码后必须是 32 字节")
    return key


@dataclass(frozen=True)
class SessionSnapshot:
    """Explicit cache contract. Never add runtime-only objects to this dataclass."""

    schema_version: int
    session_version: int
    credential_version: int
    public_id: str
    db_id: int
    user_id: int | None
    status: str
    repo_url: str
    platform: str
    owner: str
    repo: str
    git_username: str
    git_token: str

    @classmethod
    def from_parts(
        cls,
        *,
        public_id: str,
        db_id: int,
        user_id: int | None,
        status: str,
        repo_url: str,
        repo: RepoSpec,
        credential_version: int,
        session_version: int,
    ) -> SessionSnapshot:
        return cls(
            schema_version=SESSION_SNAPSHOT_SCHEMA_VERSION,
            session_version=int(session_version),
            credential_version=int(credential_version),
            public_id=public_id,
            db_id=int(db_id),
            user_id=user_id,
            status=status,
            repo_url=repo_url,
            platform=repo.platform.value,
            owner=repo.owner,
            repo=repo.repo,
            git_username=repo.git_username,
            git_token=repo.token,
        )

    def with_credentials(self, *, token: str, git_username: str) -> SessionSnapshot:
        return replace(
            self,
            git_token=token,
            git_username=git_username,
            credential_version=self.credential_version + 1,
            session_version=self.session_version + 1,
        )

    def to_session(self) -> Any:
        from ai_coding_harness.session.manager import Session

        return Session(
            public_id=self.public_id,
            db_id=self.db_id,
            user_id=self.user_id,
            repo=RepoSpec(
                platform=Platform.of(self.platform),
                owner=self.owner,
                repo=self.repo,
                token=self.git_token,
                git_username=self.git_username,
            ),
            repo_url=self.repo_url,
            status=self.status,
            credential_version=self.credential_version,
            session_version=self.session_version,
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SessionSnapshot:
        if int(payload.get("schema_version") or 0) != SESSION_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported session cache schema")
        fields = cls.__dataclass_fields__
        values = {key: payload[key] for key in fields if key in payload}
        return cls(**values)


class SessionSnapshotCache:
    async def get(self, public_id: str) -> SessionSnapshot | None:
        raise NotImplementedError

    async def put(self, snapshot: SessionSnapshot) -> None:
        raise NotImplementedError

    async def put_if_absent(self, snapshot: SessionSnapshot) -> None:
        raise NotImplementedError

    async def invalidate(self, public_id: str) -> None:
        raise NotImplementedError


class MemorySessionCache(SessionSnapshotCache):
    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = max(1, int(ttl_seconds))
        self._items: dict[str, tuple[float, SessionSnapshot]] = {}

    def _live(self, public_id: str) -> SessionSnapshot | None:
        item = self._items.get(public_id)
        if item is None:
            return None
        expires, snapshot = item
        if expires <= time.monotonic():
            self._items.pop(public_id, None)
            return None
        return snapshot

    async def get(self, public_id: str) -> SessionSnapshot | None:
        return self._live(public_id)

    async def put(self, snapshot: SessionSnapshot) -> None:
        self._items[snapshot.public_id] = (
            time.monotonic() + self._ttl,
            snapshot,
        )

    async def put_if_absent(self, snapshot: SessionSnapshot) -> None:
        if self._live(snapshot.public_id) is None:
            await self.put(snapshot)

    async def invalidate(self, public_id: str) -> None:
        self._items.pop(public_id, None)


class RedisSessionCache(SessionSnapshotCache):
    def __init__(
        self,
        client: Any,
        *,
        key: bytes,
        prefix: str,
        ttl_seconds: int,
        key_version: int,
    ) -> None:
        self._redis = client
        self._key = key
        self._prefix = prefix
        self._ttl = max(1, int(ttl_seconds))
        self._key_version = max(1, int(key_version))
        self._fallback = MemorySessionCache(ttl_seconds)

    def _redis_key(self, public_id: str) -> str:
        return f"{self._prefix}session:{public_id}:snapshot"

    def _aad(self, public_id: str) -> bytes:
        return (
            f"{self._prefix}session:{public_id}:"
            f"{SESSION_SNAPSHOT_SCHEMA_VERSION}:{self._key_version}"
        ).encode()

    def _encrypt(self, snapshot: SessionSnapshot) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        plaintext = json.dumps(
            snapshot.payload(), ensure_ascii=False, separators=(",", ":")
        ).encode()
        ciphertext = AESGCM(self._key).encrypt(
            nonce, plaintext, self._aad(snapshot.public_id)
        )
        return json.dumps(
            {
                "schema_version": SESSION_SNAPSHOT_SCHEMA_VERSION,
                "key_version": self._key_version,
                "nonce": _b64e(nonce),
                "ciphertext": _b64e(ciphertext),
            },
            separators=(",", ":"),
        )

    def _decrypt(self, public_id: str, raw: str) -> SessionSnapshot:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        envelope = json.loads(raw)
        if int(envelope.get("key_version") or 0) != self._key_version:
            raise ValueError("session cache key version mismatch")
        if int(envelope.get("schema_version") or 0) != SESSION_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("session cache schema mismatch")
        plaintext = AESGCM(self._key).decrypt(
            _b64d(str(envelope["nonce"])),
            _b64d(str(envelope["ciphertext"])),
            self._aad(public_id),
        )
        payload = json.loads(plaintext)
        snapshot = SessionSnapshot.from_payload(payload)
        if snapshot.public_id != public_id:
            raise ValueError("session cache id mismatch")
        return snapshot

    async def get(self, public_id: str) -> SessionSnapshot | None:
        try:
            raw = await self._redis.get(self._redis_key(public_id))
        except Exception:
            logger.warning("读取 Redis Session 缓存失败，本次退回进程内", exc_info=True)
            return await self._fallback.get(public_id)
        if raw is None:
            return None
        try:
            return self._decrypt(public_id, raw)
        except Exception:  # noqa: BLE001
            logger.warning("Session 缓存损坏或版本不兼容，已丢弃：%s", public_id)
            await self.invalidate(public_id)
            return None

    async def put(self, snapshot: SessionSnapshot) -> None:
        await self._fallback.put(snapshot)
        try:
            await self._redis.set(
                self._redis_key(snapshot.public_id),
                self._encrypt(snapshot),
                ex=self._ttl,
            )
        except Exception:
            logger.warning("写入 Redis Session 缓存失败，仅保留进程内副本", exc_info=True)

    async def put_if_absent(self, snapshot: SessionSnapshot) -> None:
        await self._fallback.put_if_absent(snapshot)
        try:
            await self._redis.set(
                self._redis_key(snapshot.public_id),
                self._encrypt(snapshot),
                ex=self._ttl,
                nx=True,
            )
        except Exception:
            logger.warning("写入 Redis Session 缓存失败，仅保留进程内副本", exc_info=True)

    async def invalidate(self, public_id: str) -> None:
        await self._fallback.invalidate(public_id)
        try:
            await self._redis.delete(self._redis_key(public_id))
        except Exception:
            logger.warning("删除 Redis Session 缓存失败", exc_info=True)


def build_session_cache(redis: Any | None, settings: Settings) -> SessionSnapshotCache:
    ttl = settings.redis_session_cache_ttl_seconds
    if redis is None:
        return MemorySessionCache(ttl)
    try:
        key = _encryption_key(settings.session_cache_encryption_key)
    except ValueError as exc:
        raise RuntimeError(
            f"{exc}；Redis 已启用，禁止用进程内缓存替代，否则多 worker 一致性失效"
        ) from exc
    return RedisSessionCache(
        redis,
        key=key,
        prefix=settings.redis_prefix,
        ttl_seconds=ttl,
        key_version=settings.session_cache_key_version,
    )
