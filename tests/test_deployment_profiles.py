"""Standalone SQLite and multi-worker deployment profiles."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from ai_coding_harness.auth.security import hash_password
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database, dao
from ai_coding_harness.db.models import User
from ai_coding_harness.session.state import (
    build_checkpointer,
    build_store,
    close_persistence,
)


def _standalone(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        deployment_mode="standalone",
        sqlite_path=str(tmp_path / "harness.sqlite3"),
        sandbox_provider="wsl",
    )


def test_default_profile_keeps_multi_worker_dependencies() -> None:
    settings = Settings(
        _env_file=None,
        redis_url="redis://127.0.0.1:6379/0",
    )

    assert settings.deployment_mode == "multi"
    assert settings.effective_sandbox_provider == "opensandbox"
    assert settings.resolved_database_url.startswith("postgresql+psycopg://")


def test_standalone_resolves_one_sqlite_file(tmp_path: Path) -> None:
    settings = _standalone(tmp_path)

    assert settings.is_standalone
    assert settings.effective_sandbox_provider == "wsl"
    assert settings.resolved_database_url == (
        f"sqlite+aiosqlite:///{settings.standalone_database_path.as_posix()}"
    )
    settings.validate_deployment()


def test_standalone_database_url_is_the_same_langgraph_file(tmp_path: Path) -> None:
    database = tmp_path / "single.sqlite3"
    settings = Settings(
        _env_file=None,
        deployment_mode="standalone",
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        sandbox_provider="wsl",
    )

    assert settings.standalone_database_path == database
    settings.validate_deployment()


def test_standalone_rejects_two_sqlite_paths(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        deployment_mode="standalone",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'one.sqlite3').as_posix()}",
        sqlite_path=str(tmp_path / "two.sqlite3"),
        sandbox_provider="wsl",
    )

    with pytest.raises(ValueError, match="同一个 SQLite 文件"):
        settings.validate_deployment()


def test_standalone_rejects_postgres(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        deployment_mode="standalone",
        database_url="postgresql+psycopg://localhost/db",
        sandbox_provider="wsl",
    )

    with pytest.raises(ValueError, match="standalone 必须使用 SQLite"):
        settings.validate_deployment()


def test_multi_requires_redis_and_opensandbox() -> None:
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(_env_file=None, deployment_mode="multi").validate_deployment()

    # Existing deployments may already have REDIS_URL but predate REDIS_REQUIRED.
    # Redis remains enabled; the explicit flag only controls fail-closed behavior.
    Settings(
        _env_file=None,
        deployment_mode="multi",
        redis_url="redis://127.0.0.1:6379/0",
    ).validate_deployment()

    settings = Settings(
        _env_file=None,
        deployment_mode="multi",
        redis_url="redis://127.0.0.1:6379/0",
        redis_required=True,
        sandbox_provider="wsl",
    )
    with pytest.raises(ValueError, match="opensandbox"):
        settings.validate_deployment()


async def test_standalone_bootstraps_business_and_langgraph_tables(
    tmp_path: Path,
) -> None:
    settings = _standalone(tmp_path)
    database = Database(settings)
    await database.prepare()
    try:
        async with database.session() as db:
            await dao.create_local_user(
                db,
                username="owner",
                display_name="Owner",
                password_hash=hash_password("a-secure-password"),
                role="admin",
            )
        async with database.session() as db:
            user = await db.scalar(select(User).where(User.username == "owner"))
            assert user is not None
    finally:
        await database.dispose()

    store = await build_store(settings)
    checkpointer = await build_checkpointer(settings)
    try:
        await store.aput(("tests",), "probe", {"content": "ok"})
        item = await store.aget(("tests",), "probe")
        assert item is not None and item.value["content"] == "ok"
    finally:
        await close_persistence(store, checkpointer)

    with sqlite3.connect(settings.standalone_database_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"users", "sessions", "plans", "store", "checkpoints"} <= tables
    assert settings.standalone_database_path.is_file()

