"""Single-user auth service against an in-memory SQL database."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_coding_harness.api.routes import auth as auth_routes
from ai_coding_harness.auth.middleware import AuthMiddleware
from ai_coding_harness.auth.security import hash_password
from ai_coding_harness.auth.service import AuthService
from ai_coding_harness.config import Settings
from ai_coding_harness.db import dao
from ai_coding_harness.db.models import AuthSession, LocalCredential, User


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
async def auth_env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(LocalCredential.__table__.create)
        await conn.run_sync(AuthSession.__table__.create)
    db = _Db(engine)
    settings = Settings(
        _env_file=None,
        auth_login_max_failures=2,
        auth_login_lock_seconds=60,
    )
    async with db.session() as session:
        await dao.create_local_user(
            session,
            username="owner",
            display_name="Owner",
            password_hash=hash_password("a-secure-password"),
        )
    yield settings, db
    await engine.dispose()


async def test_login_resolve_csrf_and_logout(auth_env) -> None:
    settings, db = auth_env
    service = AuthService(settings=settings, database=db)

    login = await service.login(
        username=" OWNER ",
        password="a-secure-password",
        ip="127.0.0.1",
        user_agent="pytest",
    )

    assert login is not None
    principal = await service.resolve_cookie(login.token)
    assert principal is not None
    assert principal.user.username == "owner"
    assert service.csrf_matches(
        principal,
        cookie=login.csrf_token,
        header=login.csrf_token,
    )
    assert not service.csrf_matches(
        principal,
        cookie=login.csrf_token,
        header="wrong",
    )

    await service.revoke_current(principal)
    assert await service.resolve_cookie(login.token) is None


async def test_repeated_failures_lock_the_local_account(auth_env) -> None:
    settings, db = auth_env
    service = AuthService(settings=settings, database=db)

    assert await service.login(username="owner", password="wrong-password") is None
    assert await service.login(username="owner", password="wrong-password") is None
    assert await service.login(username="owner", password="a-secure-password") is None


async def test_password_change_revokes_old_sessions(auth_env) -> None:
    settings, db = auth_env
    service = AuthService(settings=settings, database=db)
    first = await service.login(username="owner", password="a-secure-password")
    assert first is not None
    principal = await service.resolve_cookie(first.token)
    assert principal is not None

    changed = await service.change_password(
        principal=principal,
        current_password="a-secure-password",
        new_password="another-secure-password",
    )

    assert await service.resolve_cookie(first.token) is None
    assert await service.resolve_cookie(changed.token) is not None
    assert await service.login(
        username="owner", password="another-secure-password"
    ) is not None


async def test_http_login_cookie_and_csrf_flow(auth_env) -> None:
    settings, db = auth_env
    service = AuthService(settings=settings, database=db)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.state.settings = settings
    app.state.database = db
    app.state.auth_service = service
    app.include_router(auth_routes.router)

    @app.post("/protected")
    async def protected(request: Request) -> dict[str, str]:
        return {"username": request.state.auth.user.username}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/auth/me")).status_code == 401
        login = await client.post(
            "/auth/login",
            json={"username": "owner", "password": "a-secure-password"},
        )
        assert login.status_code == 200
        assert client.cookies.get("harness_session")
        assert client.cookies.get("harness_csrf")
        assert (await client.post("/protected")).status_code == 403
        csrf = client.cookies.get("harness_csrf")
        allowed = await client.post(
            "/protected", headers={"X-CSRF-Token": csrf}
        )
        assert allowed.status_code == 200
        assert allowed.json() == {"username": "owner"}
