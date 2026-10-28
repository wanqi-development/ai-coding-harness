"""Administrator-managed user lifecycle for the local multi-user deployment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from ai_coding_harness.auth.security import (
    hash_password,
    normalize_username,
    password_problem,
)
from ai_coding_harness.db import Database, dao
from ai_coding_harness.db.models import LocalCredential, User

ROLES = frozenset({"admin", "member"})


class UserAdminError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ManagedUser:
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    active_sessions: int
    last_active_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _managed_user(
    user: User,
    credential: LocalCredential | None,
    active_sessions: int,
    last_active_at: datetime | None,
) -> ManagedUser:
    return ManagedUser(
        id=int(user.id),
        username=str(user.username),
        display_name=str(user.display_name or user.username),
        role=str(user.role),
        is_active=bool(user.is_active),
        must_change_password=bool(
            credential is not None and credential.must_change_password
        ),
        active_sessions=int(active_sessions),
        last_active_at=last_active_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _clean_username(raw: str) -> str:
    username = normalize_username(raw)
    if not username:
        raise UserAdminError("用户名不能为空。")
    if len(username) > 64:
        raise UserAdminError("用户名不能超过 64 个字符。")
    if any(ch.isspace() or ch in "/\\" or ord(ch) < 32 for ch in username):
        raise UserAdminError("用户名不能包含空白、斜杠或控制字符。")
    return username


def _clean_display_name(raw: str, *, fallback: str) -> str:
    display_name = raw.strip() or fallback
    if len(display_name) > 128:
        raise UserAdminError("显示名不能超过 128 个字符。")
    return display_name


def _check_role(role: str) -> str:
    if role not in ROLES:
        raise UserAdminError("角色只能是 admin 或 member。")
    return role


def _check_password(password: str) -> str:
    problem = password_problem(password)
    if problem is not None:
        raise UserAdminError(problem)
    return password


class UserAdminService:
    """Transactional user management used by ``/users`` and the settings page."""

    def __init__(self, *, database: Database) -> None:
        self._database = database

    async def list_users(self) -> list[ManagedUser]:
        async with self._database.session() as db:
            rows = await dao.list_users_with_activity(db, now=_now())
        return [
            _managed_user(user, credential, active_sessions, last_seen)
            for user, credential, active_sessions, last_seen in rows
        ]

    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        role: str,
        temporary_password: str,
    ) -> ManagedUser:
        normalized = _clean_username(username)
        clean_role = _check_role(role)
        _check_password(temporary_password)
        clean_name = _clean_display_name(display_name, fallback=normalized)
        try:
            async with self._database.session() as db:
                if await dao.get_user_by_username(db, normalized) is not None:
                    raise UserAdminError("用户名已存在。", status_code=409)
                user = await dao.create_local_user(
                    db,
                    username=normalized,
                    display_name=clean_name,
                    password_hash=hash_password(temporary_password),
                    role=clean_role,
                    must_change_password=True,
                )
                await db.flush()
                user_id = int(user.id)
        except IntegrityError:
            raise UserAdminError("用户名已存在。", status_code=409) from None
        return await self._get_managed(user_id)

    async def update_user(
        self,
        *,
        actor_id: int,
        user_id: int,
        display_name: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> ManagedUser:
        async with self._database.session() as db:
            found = await dao.get_user_with_credential(
                db, user_id=user_id, for_update=True
            )
            if found is None:
                raise UserAdminError("用户不存在。", status_code=404)
            user, _credential = found
            next_role = _check_role(role) if role is not None else str(user.role)
            next_active = (
                bool(is_active) if is_active is not None else bool(user.is_active)
            )
            if user_id == actor_id and (not next_active or next_role != "admin"):
                raise UserAdminError(
                    "不能停用自己或取消自己的管理员角色。",
                    status_code=409,
                )
            loses_admin = (
                user.role == "admin"
                and user.is_active
                and (next_role != "admin" or not next_active)
            )
            if loses_admin and await dao.count_active_admins(
                db, exclude_user_id=user_id
            ) == 0:
                raise UserAdminError(
                    "系统必须至少保留一个启用的管理员。",
                    status_code=409,
                )
            if display_name is not None:
                user.display_name = _clean_display_name(
                    display_name, fallback=str(user.username)
                )
            user.role = next_role
            user.is_active = next_active
            if not next_active:
                await dao.revoke_user_sessions(db, user_id=user_id)
            await db.flush()
        return await self._get_managed(user_id)

    async def reset_password(
        self,
        *,
        actor_id: int,
        user_id: int,
        new_password: str,
    ) -> ManagedUser:
        if user_id == actor_id:
            raise UserAdminError(
                "修改自己的密码请使用“修改密码”。",
                status_code=409,
            )
        _check_password(new_password)
        async with self._database.session() as db:
            if await dao.get_user(db, user_id) is None:
                raise UserAdminError("用户不存在。", status_code=404)
            changed = await dao.reset_local_password(
                db,
                user_id=user_id,
                password_hash=hash_password(new_password),
                must_change_password=True,
            )
            if not changed:
                raise UserAdminError("该用户没有本地密码凭据。")
            await dao.revoke_user_sessions(db, user_id=user_id)
        return await self._get_managed(user_id)

    async def revoke_sessions(self, *, user_id: int) -> ManagedUser:
        async with self._database.session() as db:
            if await dao.get_user(db, user_id) is None:
                raise UserAdminError("用户不存在。", status_code=404)
            await dao.revoke_user_sessions(db, user_id=user_id)
        return await self._get_managed(user_id)

    async def _get_managed(self, user_id: int) -> ManagedUser:
        rows = await self.list_users()
        for user in rows:
            if user.id == user_id:
                return user
        raise UserAdminError("用户不存在。", status_code=404)
