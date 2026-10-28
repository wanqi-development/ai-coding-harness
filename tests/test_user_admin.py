"""Multi-user administration, RBAC and forced password-change behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_coding_harness.api.routes import auth as auth_routes
from ai_coding_harness.api.routes import users as users_routes
from ai_coding_harness.auth.middleware import AuthMiddleware
from ai_coding_harness.auth.security import hash_password
from ai_coding_harness.auth.service import AuthService
from ai_coding_harness.auth.user_service import UserAdminError, UserAdminService
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
async def user_admin_env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(LocalCredential.__table__.create)
        await conn.run_sync(AuthSession.__table__.create)
    db = _Db(engine)
    settings = Settings(_env_file=None)
    async with db.session() as session:
        owner = await dao.create_local_user(
            session,
            username="owner",
            display_name="Owner",
            password_hash=hash_password("a-secure-password"),
            role="admin",
        )
        owner_id = int(owner.id)
    service = UserAdminService(database=db)
    yield settings, db, service, owner_id
    await engine.dispose()


async def test_user_lifecycle_and_password_reset(user_admin_env) -> None:
    settings, db, service, owner_id = user_admin_env
    created = await service.create_user(
        username=" Member ",
        display_name="普通成员",
        role="member",
        temporary_password="temporary-password",
    )
    assert created.username == "member"
    assert created.must_change_password is True

    users = await service.list_users()
    assert [item.username for item in users] == ["owner", "member"]

    updated = await service.update_user(
        actor_id=owner_id,
        user_id=created.id,
        display_name="成员甲",
        role="member",
        is_active=True,
    )
    assert updated.display_name == "成员甲"

    auth = AuthService(settings=settings, database=db)
    login = await auth.login(
        username="member",
        password="temporary-password",
    )
    assert login is not None
    assert login.user.must_change_password is True
    assert await auth.resolve_cookie(login.token) is not None

    reset = await service.reset_password(
        actor_id=owner_id,
        user_id=created.id,
        new_password="changed-temporary-password",
    )
    assert reset.must_change_password is True
    assert await auth.resolve_cookie(login.token) is None


async def test_last_admin_cannot_be_disabled_or_demoted(user_admin_env) -> None:
    _settings, _db, service, owner_id = user_admin_env
    with pytest.raises(UserAdminError, match="至少保留一个"):
        await service.update_user(
            actor_id=owner_id + 100,
            user_id=owner_id,
            is_active=False,
        )
    with pytest.raises(UserAdminError, match="至少保留一个"):
        await service.update_user(
            actor_id=owner_id + 100,
            user_id=owner_id,
            role="member",
        )


async def test_admin_rbac_and_forced_password_change(user_admin_env) -> None:
    settings, db, users, owner_id = user_admin_env
    auth = AuthService(settings=settings, database=db)
    member = await users.create_user(
        username="member",
        display_name="Member",
        role="member",
        temporary_password="temporary-password",
    )

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.state.settings = settings
    app.state.database = db
    app.state.auth_service = auth
    app.state.user_admin_service = users
    app.include_router(auth_routes.router)
    app.include_router(users_routes.router)

    @app.get("/protected")
    async def protected(request: Request) -> dict[str, str]:
        return {"username": request.state.auth.user.username}

    @app.get("/model-config/dummy")
    async def member_model_config() -> dict[str, bool]:
        return {"ok": True}

    @app.put("/model-config/providers/demo")
    async def admin_provider_edit() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/app-settings/dummy")
    async def admin_app_settings() -> dict[str, bool]:
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        member_login = await client.post(
            "/auth/login",
            json={"username": "member", "password": "temporary-password"},
        )
        assert member_login.status_code == 200
        blocked = await client.get("/protected")
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "password_change_required"

    async with db.session() as session:
        await dao.reset_local_password(
            session,
            user_id=member.id,
            password_hash=hash_password("temporary-password"),
            must_change_password=False,
        )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        admin_login = await client.post(
            "/auth/login",
            json={"username": "owner", "password": "a-secure-password"},
        )
        assert admin_login.status_code == 200
        listed = await client.get("/users")
        assert listed.status_code == 200
        assert [item["username"] for item in listed.json()] == ["owner", "member"]
        csrf = client.cookies.get("harness_csrf")
        made = await client.post(
            "/users",
            headers={"X-CSRF-Token": csrf},
            json={
                "username": "operator",
                "display_name": "Operator",
                "role": "member",
                "temporary_password": "operator-temporary",
            },
        )
        assert made.status_code == 201
        assert made.json()["id"] != owner_id
        assert made.json()["must_change_password"] is True

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/auth/login",
            json={"username": "member", "password": "temporary-password"},
        )
        denied = await client.get("/users")
        assert denied.status_code == 403
        assert denied.json()["code"] == "admin_required"
        assert (await client.get("/model-config/dummy")).status_code == 200
        assert (await client.get("/app-settings/dummy")).status_code == 403
        provider = await client.put("/model-config/providers/demo")
        assert provider.status_code == 403
        assert provider.json()["code"] == "admin_required"

    assert member.is_active is True
