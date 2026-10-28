"""用户、本地凭据、外部身份与登录会话的 DAO。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import (
    AuthSession,
    LocalCredential,
    User,
    UserIdentity,
)

from ._common import _now


async def count_users(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(User)) or 0)


async def get_user(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    return await db.scalar(select(User).where(User.username == username))


async def get_user_with_credential(
    db: AsyncSession, *, user_id: int, for_update: bool = False
) -> tuple[User, LocalCredential | None] | None:
    query = (
        select(User, LocalCredential)
        .outerjoin(LocalCredential, LocalCredential.user_id == User.id)
        .where(User.id == user_id)
    )
    if for_update:
        query = query.with_for_update()
    row = (await db.execute(query)).one_or_none()
    return None if row is None else (row[0], row[1])


async def list_users_with_activity(
    db: AsyncSession, *, now: datetime
) -> list[tuple[User, LocalCredential | None, int, datetime | None]]:
    """用户列表 + 当前有效登录会话数 / 最近活动时间。"""
    activity = (
        select(
            AuthSession.user_id.label("user_id"),
            func.count().label("active_sessions"),
            func.max(AuthSession.last_seen_at).label("last_seen_at"),
        )
        .where(
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .group_by(AuthSession.user_id)
        .subquery()
    )
    rows = await db.execute(
        select(
            User,
            LocalCredential,
            func.coalesce(activity.c.active_sessions, 0),
            activity.c.last_seen_at,
        )
        .outerjoin(LocalCredential, LocalCredential.user_id == User.id)
        .outerjoin(activity, activity.c.user_id == User.id)
        .order_by(User.created_at, User.id)
    )
    return [
        (user, credential, int(active_sessions or 0), last_seen_at)
        for user, credential, active_sessions, last_seen_at in rows.all()
    ]


async def count_active_admins(
    db: AsyncSession, *, exclude_user_id: int | None = None
) -> int:
    query = select(func.count()).select_from(User).where(
        User.role == "admin",
        User.is_active.is_(True),
    )
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    return int(await db.scalar(query) or 0)


async def get_local_login(
    db: AsyncSession, *, username: str, for_update: bool = False
) -> tuple[User, LocalCredential] | None:
    query = (
        select(User, LocalCredential)
        .join(LocalCredential, LocalCredential.user_id == User.id)
        .where(User.username == username)
    )
    if for_update:
        query = query.with_for_update()
    row = await db.execute(query)
    found = row.one_or_none()
    return None if found is None else (found[0], found[1])


async def create_local_user(
    db: AsyncSession,
    *,
    username: str,
    display_name: str,
    password_hash: str,
    role: str = "admin",
    must_change_password: bool = False,
) -> User:
    user = User(
        username=username,
        display_name=display_name,
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        LocalCredential(
            user_id=user.id,
            password_hash=password_hash,
            must_change_password=must_change_password,
        )
    )
    await db.flush()
    return user


async def reset_local_password(
    db: AsyncSession,
    *,
    user_id: int,
    password_hash: str,
    must_change_password: bool = False,
    now: datetime | None = None,
) -> bool:
    stamp = now or _now()
    result = await db.execute(
        update(LocalCredential)
        .where(LocalCredential.user_id == user_id)
        .values(
            password_hash=password_hash,
            must_change_password=must_change_password,
            failed_login_count=0,
            locked_until=None,
            password_changed_at=stamp,
            updated_at=stamp,
        )
    )
    return bool(result.rowcount)


async def record_login_failure(
    db: AsyncSession,
    *,
    credential: LocalCredential,
    locked_until: datetime | None,
) -> None:
    credential.failed_login_count = int(credential.failed_login_count or 0) + 1
    credential.locked_until = locked_until
    await db.flush()


async def record_login_success(
    db: AsyncSession, *, credential: LocalCredential, now: datetime | None = None
) -> None:
    stamp = now or _now()
    credential.failed_login_count = 0
    credential.locked_until = None
    credential.updated_at = stamp
    await db.flush()


async def create_auth_session(
    db: AsyncSession,
    *,
    user_id: int,
    token_hash: str,
    csrf_token_hash: str,
    expires_at: datetime,
    ip: str,
    user_agent: str,
) -> AuthSession:
    row = AuthSession(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=token_hash,
        csrf_token_hash=csrf_token_hash,
        expires_at=expires_at,
        ip=ip,
        user_agent=user_agent,
    )
    db.add(row)
    await db.flush()
    return row


async def get_auth_session(
    db: AsyncSession, *, token_hash: str
) -> tuple[AuthSession, User, LocalCredential | None] | None:
    result = await db.execute(
        select(AuthSession, User, LocalCredential)
        .join(User, User.id == AuthSession.user_id)
        .outerjoin(LocalCredential, LocalCredential.user_id == User.id)
        .where(AuthSession.token_hash == token_hash)
    )
    found = result.one_or_none()
    return None if found is None else (found[0], found[1], found[2])


async def touch_auth_session(
    db: AsyncSession, *, session_id: uuid.UUID, now: datetime
) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id)
        .values(last_seen_at=now)
    )


async def revoke_auth_session(
    db: AsyncSession, *, session_id: uuid.UUID, now: datetime | None = None
) -> bool:
    result = await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now or _now())
    )
    return bool(result.rowcount)


async def revoke_user_sessions(
    db: AsyncSession,
    *,
    user_id: int,
    keep_session_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now or _now())
    )
    if keep_session_id is not None:
        stmt = stmt.where(AuthSession.id != keep_session_id)
    result = await db.execute(stmt)
    return int(result.rowcount or 0)


async def claim_unowned_sessions(db: AsyncSession, *, user_id: int) -> int:
    from ai_coding_harness.db.models import AgentSession

    result = await db.execute(
        update(AgentSession)
        .where(AgentSession.user_id.is_(None))
        .values(user_id=user_id)
    )
    return int(result.rowcount or 0)


async def delete_all_auth_sessions(db: AsyncSession, *, user_id: int) -> None:
    await db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


async def delete_user_identities(db: AsyncSession, *, user_id: int) -> None:
    await db.execute(delete(UserIdentity).where(UserIdentity.user_id == user_id))
