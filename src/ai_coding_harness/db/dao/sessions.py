"""会话与仓库绑定相关的 DAO。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.core.codeplatform import Platform
from ai_coding_harness.db.models import (
    AgentSession,
    Repository,
)


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
async def create_session(
    db: AsyncSession,
    *,
    public_id: uuid.UUID,
    gitee_token: str | None = None,
    gitee_username: str | None = None,
    git_platform: str | None = None,
    title: str | None = None,
) -> AgentSession:
    row = AgentSession(
        public_id=public_id,
        gitee_token=gitee_token,
        gitee_username=gitee_username,
        # 认不出的取值一律回落 Gitee（与 Platform.of 同一口径），列本身非空
        git_platform=Platform.of(git_platform).value,
        title=title,
    )
    db.add(row)
    await db.flush()
    return row


async def get_session_by_public_id(db: AsyncSession, public_id: uuid.UUID) -> AgentSession | None:
    return await db.scalar(select(AgentSession).where(AgentSession.public_id == public_id))


async def get_session(db: AsyncSession, session_id: int) -> AgentSession | None:
    return await db.get(AgentSession, session_id)


async def list_sessions(db: AsyncSession, *, limit: int = 50) -> list[AgentSession]:
    rows = await db.scalars(
        select(AgentSession).order_by(AgentSession.created_at.desc()).limit(limit)
    )
    return list(rows)


async def list_sessions_with_repo(
    db: AsyncSession, *, limit: int = 50
) -> list[tuple[AgentSession, Repository | None]]:
    """会话列表（左连仓库），用于前端历史侧栏。

    删除是软删除（状态置 destroyed，保留记录供审计），故列表按状态排除已删除会话。
    """
    rows = await db.execute(
        select(AgentSession, Repository)
        .outerjoin(Repository, Repository.session_id == AgentSession.id)
        .where(AgentSession.status != "destroyed")
        .order_by(AgentSession.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())
