"""Single-user local authentication with database-backed sessions."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_coding_harness.auth.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    new_token,
    normalize_username,
    password_problem,
    token_hash,
    verify_password,
)
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database, dao
from ai_coding_harness.db.models import User


class AuthFailure(Exception):
    """Credentials or the current login are invalid."""


class AuthLocked(AuthFailure):
    """Too many recent failures for this local account."""


@dataclass(frozen=True)
class AuthUser:
    id: int
    username: str
    display_name: str
    role: str
    must_change_password: bool = False


@dataclass(frozen=True)
class AuthPrincipal:
    user: AuthUser
    session_id: uuid.UUID
    csrf_token_hash: str


@dataclass(frozen=True)
class LoginSession:
    user: AuthUser
    token: str
    csrf_token: str
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _user_payload(user: User, *, must_change: bool = False) -> AuthUser:
    return AuthUser(
        id=int(user.id),
        username=str(user.username),
        display_name=str(user.display_name or user.username),
        role=str(user.role),
        must_change_password=must_change,
    )


class AuthService:
    """Shared auth service; all state lives in PostgreSQL for multi-worker safety."""

    def __init__(self, *, settings: Settings, database: Database) -> None:
        self._settings = settings
        self._database = database

    async def login(
        self,
        *,
        username: str,
        password: str,
        ip: str = "",
        user_agent: str = "",
    ) -> LoginSession | None:
        normalized = normalize_username(username)
        now = _now()
        async with self._database.session() as db:
            found = await dao.get_local_login(
                db, username=normalized, for_update=True
            )
            if found is None:
                verify_password(password, DUMMY_PASSWORD_HASH)
                return None
            user, credential = found
            if not user.is_active:
                verify_password(password, credential.password_hash)
                return None
            locked_until = _aware(credential.locked_until)
            if locked_until is not None and locked_until > now:
                return None
            if not verify_password(password, credential.password_hash):
                failures = int(credential.failed_login_count or 0) + 1
                locked_until = (
                    now + timedelta(seconds=self._settings.auth_login_lock_seconds)
                    if failures >= self._settings.auth_login_max_failures
                    else None
                )
                await dao.record_login_failure(
                    db, credential=credential, locked_until=locked_until
                )
                return None
            await dao.record_login_success(db, credential=credential, now=now)
            return await self._issue_session(
                db,
                user=user,
                must_change=bool(credential.must_change_password),
                ip=ip,
                user_agent=user_agent,
                now=now,
            )

    async def _issue_session(
        self,
        db: Any,
        *,
        user: User,
        must_change: bool,
        ip: str,
        user_agent: str,
        now: datetime,
    ) -> LoginSession:
        token = new_token()
        csrf_token = new_token()
        expires_at = now + timedelta(hours=self._settings.auth_session_ttl_hours)
        await dao.create_auth_session(
            db,
            user_id=int(user.id),
            token_hash=token_hash(token),
            csrf_token_hash=token_hash(csrf_token),
            expires_at=expires_at,
            ip=ip[:200],
            user_agent=user_agent[:500],
        )
        return LoginSession(
            user=_user_payload(user, must_change=must_change),
            token=token,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    async def resolve_cookie(self, raw_token: str | None) -> AuthPrincipal | None:
        if not raw_token:
            return None
        now = _now()
        async with self._database.session() as db:
            found = await dao.get_auth_session(
                db, token_hash=token_hash(raw_token)
            )
            if found is None:
                return None
            session, user, credential = found
            if session.revoked_at is not None or _aware(session.expires_at) <= now:
                return None
            idle_deadline = _aware(session.last_seen_at) + timedelta(
                hours=self._settings.auth_idle_timeout_hours
            )
            if idle_deadline <= now or not user.is_active:
                return None
            if now - _aware(session.last_seen_at) >= timedelta(minutes=5):
                await dao.touch_auth_session(
                    db, session_id=session.id, now=now
                )
            return AuthPrincipal(
                user=_user_payload(
                    user,
                    must_change=bool(
                        credential is not None
                        and credential.must_change_password
                    ),
                ),
                session_id=session.id,
                csrf_token_hash=session.csrf_token_hash,
            )

    def csrf_matches(self, principal: AuthPrincipal, *, cookie: str, header: str) -> bool:
        if not cookie or not header or not hmac.compare_digest(cookie, header):
            return False
        return hmac.compare_digest(token_hash(header), principal.csrf_token_hash)

    async def revoke_current(self, principal: AuthPrincipal) -> None:
        async with self._database.session() as db:
            await dao.revoke_auth_session(db, session_id=principal.session_id)

    async def change_password(
        self,
        *,
        principal: AuthPrincipal,
        current_password: str,
        new_password: str,
        ip: str = "",
        user_agent: str = "",
    ) -> LoginSession:
        problem = password_problem(new_password)
        if problem is not None:
            raise AuthFailure(problem)
        now = _now()
        async with self._database.session() as db:
            found = await dao.get_local_login(
                db, username=principal.user.username, for_update=True
            )
            if found is None:
                raise AuthFailure("当前账号不可用。")
            user, credential = found
            if not verify_password(current_password, credential.password_hash):
                raise AuthFailure("当前密码不正确。")
            await dao.reset_local_password(
                db,
                user_id=int(user.id),
                password_hash=hash_password(new_password),
                now=now,
            )
            await dao.revoke_user_sessions(
                db, user_id=int(user.id), now=now
            )
            return await self._issue_session(
                db,
                user=user,
                must_change=False,
                ip=ip,
                user_agent=user_agent,
                now=now,
            )
