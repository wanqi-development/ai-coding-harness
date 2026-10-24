"""PR 镜像的归属、方案关联与工作区同步水位。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import PullRequest

from ._common import _now

PR_SOURCE_AGENT = "agent"
PR_SOURCE_LEGACY_AGENT = "legacy_agent"
PR_SOURCE_EXTERNAL = "external"
PR_SOURCE_UNKNOWN = "unknown"
PR_SOURCE_OWNED = frozenset({PR_SOURCE_AGENT, PR_SOURCE_LEGACY_AGENT})


def is_owned_pull_source(source: str | None) -> bool:
    return str(source or "") in PR_SOURCE_OWNED


async def list_pull_requests(
    db: AsyncSession,
    *,
    platform: str,
    owner: str,
    repo: str,
    numbers: list[int] | tuple[int, ...] | None = None,
) -> list[PullRequest]:
    query = select(PullRequest).where(
        PullRequest.platform == platform,
        PullRequest.repo_owner == owner,
        PullRequest.repo_name == repo,
    )
    if numbers:
        query = query.where(PullRequest.number.in_(tuple(numbers)))
    rows = await db.scalars(query.order_by(PullRequest.number.desc()))
    return list(rows)


async def get_pull_request(
    db: AsyncSession,
    *,
    platform: str,
    owner: str,
    repo: str,
    number: int,
) -> PullRequest | None:
    return await db.scalar(
        select(PullRequest).where(
            PullRequest.platform == platform,
            PullRequest.repo_owner == owner,
            PullRequest.repo_name == repo,
            PullRequest.number == number,
        )
    )


async def newest_pull_number(
    db: AsyncSession, *, platform: str, owner: str, repo: str
) -> int | None:
    """该仓库在 PR 表里的最大编号，用作内部翻页重叠水位。"""
    value = await db.scalar(
        select(func.max(PullRequest.number)).where(
            PullRequest.platform == platform,
            PullRequest.repo_owner == owner,
            PullRequest.repo_name == repo,
        )
    )
    return int(value) if value is not None else None


async def upsert_pull_request(
    db: AsyncSession,
    *,
    platform: str,
    owner: str,
    repo: str,
    number: int,
    title: str = "",
    state: str = "unknown",
    url: str = "",
    author: str = "",
    head_ref: str = "",
    head_sha: str = "",
    synced_head_sha: str | None = None,
    base_ref: str = "",
    source: str = PR_SOURCE_UNKNOWN,
    session_id: int | None = None,
    plan_id: int | None = None,
    remote_created_at: datetime | None = None,
    remote_updated_at: datetime | None = None,
    mark_synced: bool = False,
) -> PullRequest:
    """幂等刷新一条 PR；已登记的归属不会被普通平台快照降级。"""
    row = await get_pull_request(
        db, platform=platform, owner=owner, repo=repo, number=number
    )
    now = _now()
    if row is None:
        row = PullRequest(
            platform=platform,
            repo_owner=owner,
            repo_name=repo,
            number=number,
            title=title,
            state=state,
            url=url,
            author=author,
            head_ref=head_ref,
            head_sha=head_sha,
            synced_head_sha=(
                head_sha if mark_synced else synced_head_sha
            ),
            base_ref=base_ref,
            source=source,
            session_id=session_id,
            plan_id=plan_id,
            remote_created_at=remote_created_at,
            remote_updated_at=remote_updated_at,
            last_synced_at=now,
        )
        db.add(row)
        await db.flush()
        return row

    if row.source == PR_SOURCE_UNKNOWN and (
        source in PR_SOURCE_OWNED or source == PR_SOURCE_EXTERNAL
    ):
        row.source = source
    if session_id is not None and row.session_id is None:
        row.session_id = session_id
    if plan_id is not None and row.plan_id is None:
        row.plan_id = plan_id
    row.title = title or row.title
    row.state = state or row.state
    row.url = url or row.url
    row.author = author or row.author
    row.head_ref = head_ref or row.head_ref
    row.head_sha = head_sha or row.head_sha
    row.base_ref = base_ref or row.base_ref
    row.remote_created_at = remote_created_at or row.remote_created_at
    row.remote_updated_at = remote_updated_at or row.remote_updated_at
    row.last_synced_at = now
    if mark_synced:
        row.synced_head_sha = head_sha
    elif synced_head_sha is not None:
        row.synced_head_sha = synced_head_sha
    await db.flush()
    return row


async def mark_pull_synced(
    db: AsyncSession, *, pull_id: int, head_sha: str
) -> bool:
    result = await db.execute(
        update(PullRequest)
        .where(PullRequest.id == pull_id)
        .values(synced_head_sha=head_sha, updated_at=_now())
    )
    return bool(result.rowcount)


async def pulls_for_plan(db: AsyncSession, *, plan_id: int) -> list[PullRequest]:
    rows = await db.scalars(
        select(PullRequest)
        .where(PullRequest.plan_id == plan_id)
        .order_by(PullRequest.number.desc())
    )
    return list(rows)


def pull_row_payload(row: PullRequest) -> dict[str, Any]:
    """给服务层做只读拼装用的轻量形状。"""
    return {
        "id": row.id,
        "plan_id": row.plan_id,
        "source": row.source,
        "head_sha": row.head_sha,
        "synced_head_sha": row.synced_head_sha,
    }
