"""User-scoped cache/branch/model settings must not leak across users."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_coding_harness.agent.llm import effective_for, intent_mode_for, model_rows_for
from ai_coding_harness.api.app import apply_model_endpoints
from ai_coding_harness.api.routes import me as me_routes
from ai_coding_harness.config import Settings
from ai_coding_harness.db import dao
from ai_coding_harness.db.models import (
    AgentSession,
    AuthSession,
    LocalCredential,
    ModelEndpoint,
    Repository,
    User,
    UserSetting,
)
from ai_coding_harness.session.session_cache import MemorySessionCache
from ai_coding_harness.session.user_runtime import (
    default_repo_view_dir,
    load_user_runtime_settings,
    settings_for_user,
)


class _Db:
    def __init__(self, engine) -> None:
        self._factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self):
        async with self._factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


@pytest.fixture
async def scoped_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(LocalCredential.__table__.create)
        await conn.run_sync(AuthSession.__table__.create)
        await conn.run_sync(UserSetting.__table__.create)
        await conn.run_sync(ModelEndpoint.__table__.create)
        await conn.run_sync(AgentSession.__table__.create)
        await conn.run_sync(Repository.__table__.create)
    db = _Db(engine)
    async with db.session() as session:
        session.add_all(
            [
                User(username="alice", display_name="Alice", role="admin", is_active=True),
                User(username="bob", display_name="Bob", role="member", is_active=True),
            ]
        )
    yield db
    await engine.dispose()


async def test_model_rows_and_intent_mode_do_not_leak(scoped_db) -> None:
    async with scoped_db.session() as db:
        await dao.upsert_model_endpoint(
            db,
            user_id=1,
            agent="qa",
            base_url="https://alice.example/v1",
            model_name="alice-model",
            api_key="alice-key",
        )
        await dao.upsert_model_endpoint(
            db,
            user_id=1,
            agent="intent_mode",
            model_name="llm",
        )

    settings = Settings(_env_file=None)
    await apply_model_endpoints(settings, scoped_db)
    alice = model_rows_for(settings, 1)
    bob = model_rows_for(settings, 2)

    assert alice["qa"].model == "alice-model"
    assert bob == {}
    assert intent_mode_for(settings, 1) == "llm"
    assert intent_mode_for(settings, 2) == "jev"
    assert effective_for(settings, "qa", 2)["source"] == "unset"
    runtime = await load_user_runtime_settings(
        Settings(_env_file=None), scoped_db, user_id=1
    )
    local = settings_for_user(Settings(_env_file=None), runtime)
    assert effective_for(local, "qa", 1)["model"] == "alice-model"


async def test_repo_cache_default_is_namespaced_by_platform_user(scoped_db) -> None:
    async with scoped_db.session() as db:
        await dao.upsert_user_settings(
            db,
            user_id=1,
            repo_view_dir=str(default_repo_view_dir(1)),
            gitee_base_branch="develop",
        )

    alice = await load_user_runtime_settings(
        Settings(_env_file=None), scoped_db, user_id=1
    )
    bob = await load_user_runtime_settings(
        Settings(_env_file=None), scoped_db, user_id=2
    )

    assert Path(alice.repo_view_dir).parts[-3:] == ("users", "1", "repo_cache")
    assert bob.repo_view_dir != alice.repo_view_dir
    assert alice.gitee_base_branch == "develop"
    assert bob.gitee_base_branch == ""
    copy = settings_for_user(Settings(_env_file=None), alice)
    assert copy.gitee_base_branch == "develop"


async def test_me_settings_only_changes_the_current_user(scoped_db, tmp_path) -> None:
    settings = Settings(_env_file=None)
    app = FastAPI()
    session_id = uuid.uuid4()
    async with scoped_db.session() as db:
        db.add(
            AuthSession(
                id=session_id,
                user_id=1,
                token_hash="a" * 64,
                csrf_token_hash="b" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        request.state.auth = SimpleNamespace(
            user=SimpleNamespace(id=int(request.headers["X-Test-User"])),
            session_id=session_id,
        )
        return await call_next(request)

    app.include_router(me_routes.router)
    app.state.settings = settings
    app.state.database = scoped_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        alice = await client.put(
            "/me/settings",
            headers={"X-Test-User": "1"},
            json={
                "repo_view_dir": str(tmp_path / "alice-cache"),
                "gitee_base_branch": "develop",
            },
        )
        assert alice.status_code == 200
        assert alice.json()["repo_view_dir_source"] == "db"
        bob = (await client.get("/me/settings", headers={"X-Test-User": "2"})).json()
        assert bob["repo_view_dir_source"] == "default"
        assert bob["gitee_base_branch"] == ""
        profile = (
            await client.get("/me/profile", headers={"X-Test-User": "1"})
        ).json()
        assert profile["username"] == "alice"
        assert profile["active_sessions"] == 1
        assert profile["current_session_started_at"]


async def test_token_update_rewrites_user_sessions_and_cache(scoped_db, monkeypatch) -> None:
    app = FastAPI()
    auth_session_id = uuid.uuid4()
    public_id = uuid.uuid4()
    async with scoped_db.session() as db:
        db.add(
            AuthSession(
                id=auth_session_id,
                user_id=1,
                token_hash="c" * 64,
                csrf_token_hash="d" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session = AgentSession(
            id=1,
            public_id=public_id,
            user_id=1,
            status="active",
            gitee_token="old-token",
            gitee_username="alice",
            git_platform="gitee",
            workspace_status="ready",
        )
        db.add(session)
        await db.flush()
        db.add(
            Repository(
                id=1,
                session_id=session.id,
                owner="acme",
                name="demo",
                url="https://gitee.com/acme/demo.git",
                platform="gitee",
            )
        )

    async def allowed(settings, repo):
        assert repo.owner == "acme" and repo.repo == "demo"

    monkeypatch.setattr(me_routes, "verify_repo_access", allowed)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        request.state.auth = SimpleNamespace(
            user=SimpleNamespace(id=1),
            session_id=auth_session_id,
        )
        return await call_next(request)

    app.include_router(me_routes.router)
    app.state.settings = Settings(_env_file=None)
    app.state.database = scoped_db
    app.state.session_cache = MemorySessionCache()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            "/me/settings",
            json={"gitee_access_token": "new-secret", "gitee_username": "alice-new"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["gitee_access_token"] == ""
        assert body["gitee_access_token_configured"] is True
        assert body["gitee_username"] == "alice-new"

        cached = await app.state.session_cache.get(str(public_id))
        assert cached is not None
        assert cached.git_token == "new-secret"
        assert cached.credential_version == 1

        cleared = await client.put("/me/settings", json={"clear_gitee_token": True})
        assert cleared.status_code == 200
        assert cleared.json()["gitee_access_token_configured"] is False

    async with scoped_db.session() as db:
        row = await db.get(AgentSession, 1)
        settings = await dao.get_user_settings(db, user_id=1)
        assert row.gitee_token is None
        assert row.credential_version == 2
        assert not settings.gitee_access_token


async def test_token_update_is_rejected_when_an_active_repo_loses_access(
    scoped_db, monkeypatch
) -> None:
    app = FastAPI()
    auth_session_id = uuid.uuid4()
    async with scoped_db.session() as db:
        db.add(
            AuthSession(
                id=auth_session_id,
                user_id=1,
                token_hash="e" * 64,
                csrf_token_hash="f" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session = AgentSession(
            id=1,
            public_id=uuid.uuid4(),
            user_id=1,
            status="active",
            gitee_token="old-token",
            gitee_username="alice",
            git_platform="gitee",
            workspace_status="ready",
        )
        db.add(session)
        await db.flush()
        db.add(
            Repository(
                id=1,
                session_id=session.id,
                owner="acme",
                name="demo",
                url="https://gitee.com/acme/demo.git",
                platform="gitee",
            )
        )

    async def denied(settings, repo):
        raise me_routes.RepoAccessError("没有 acme/demo 权限")

    monkeypatch.setattr(me_routes, "verify_repo_access", denied)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        request.state.auth = SimpleNamespace(
            user=SimpleNamespace(id=1),
            session_id=auth_session_id,
        )
        return await call_next(request)

    app.include_router(me_routes.router)
    app.state.settings = Settings(_env_file=None)
    app.state.database = scoped_db
    app.state.session_cache = MemorySessionCache()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            "/me/settings", json={"gitee_access_token": "bad-secret"}
        )
    assert response.status_code == 409

    async with scoped_db.session() as db:
        row = await db.get(AgentSession, 1)
        assert row.gitee_token == "old-token"
        assert row.credential_version == 0
