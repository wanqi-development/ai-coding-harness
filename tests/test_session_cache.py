"""Versioned Session snapshots stay explicit and encrypted in Redis."""

from __future__ import annotations

import base64
import json

import pytest

from ai_coding_harness.config import Settings
from ai_coding_harness.core.codeplatform import Platform, RepoSpec
from ai_coding_harness.session.session_cache import (
    MemorySessionCache,
    RedisSessionCache,
    SessionSnapshot,
    build_session_cache,
)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and key in self.data:
            return None
        self.data[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0


def _snapshot(token: str = "ghp_super_secret") -> SessionSnapshot:
    return SessionSnapshot.from_parts(
        public_id="11111111-1111-1111-1111-111111111111",
        db_id=7,
        user_id=3,
        status="active",
        repo_url="https://github.com/acme/demo.git",
        repo=RepoSpec(
            platform=Platform.GITHUB,
            owner="acme",
            repo="demo",
            token=token,
            git_username="x-access-token",
        ),
        credential_version=4,
        session_version=9,
    )


def _key() -> bytes:
    return b"k" * 32


async def test_redis_snapshot_round_trip_is_encrypted() -> None:
    redis = FakeRedis()
    cache = RedisSessionCache(
        redis,
        key=_key(),
        prefix="test:",
        ttl_seconds=300,
        key_version=2,
    )
    source = _snapshot()

    await cache.put(source)

    raw = next(iter(redis.data.values()))
    assert "ghp_super_secret" not in raw
    assert "acme" not in raw
    envelope = json.loads(raw)
    assert envelope["schema_version"] == 1
    assert envelope["key_version"] == 2
    assert envelope["nonce"]
    assert envelope["ciphertext"]
    assert next(iter(redis.ttls.values())) == 300
    assert await cache.get(source.public_id) == source


async def test_an_incompatible_cached_schema_is_discarded() -> None:
    redis = FakeRedis()
    cache = RedisSessionCache(
        redis,
        key=_key(),
        prefix="test:",
        ttl_seconds=300,
        key_version=1,
    )
    snapshot = _snapshot()
    await cache.put(snapshot)
    key = next(iter(redis.data))
    envelope = json.loads(redis.data[key])
    envelope["schema_version"] = 999
    redis.data[key] = json.dumps(envelope)

    assert await cache.get(snapshot.public_id) is None
    assert key not in redis.data


async def test_memory_cache_expires_without_stale_values(monkeypatch) -> None:
    now = 10.0
    monkeypatch.setattr("ai_coding_harness.session.session_cache.time.monotonic", lambda: now)
    cache = MemorySessionCache(ttl_seconds=5)
    snapshot = _snapshot()
    await cache.put(snapshot)
    assert await cache.get(snapshot.public_id) == snapshot
    now = 15.1
    assert await cache.get(snapshot.public_id) is None


def test_redis_without_encryption_key_refuses_to_start() -> None:
    with pytest.raises(RuntimeError, match="SESSION_CACHE_ENCRYPTION_KEY"):
        build_session_cache(
            FakeRedis(),
            Settings(_env_file=None, session_cache_encryption_key=""),
        )


def test_encryption_key_accepts_base64() -> None:
    raw = base64.urlsafe_b64encode(_key()).decode()
    cache = build_session_cache(
        FakeRedis(),
        Settings(_env_file=None, session_cache_encryption_key=raw),
    )
    assert isinstance(cache, RedisSessionCache)


@pytest.mark.parametrize("key", ["short", "z" * 64])
def test_bad_encryption_keys_refuse_to_start(key: str) -> None:
    with pytest.raises(RuntimeError, match="SESSION_CACHE_ENCRYPTION_KEY"):
        build_session_cache(
            FakeRedis(),
            Settings(_env_file=None, session_cache_encryption_key=key),
        )
