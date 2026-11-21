"""Persistent one-shot Git command approvals."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import GitApproval

PENDING = "pending"
RESOLVED = frozenset({"approved", "denied", "terminated", "expired", "consumed"})


def approval_payload(row: GitApproval) -> dict[str, Any]:
    details = row.details if isinstance(row.details, dict) else {}
    return {
        "approval_id": row.id,
        "request_hash": row.request_hash,
        "tool_call_id": row.tool_call_id,
        "command": row.command,
        "action": row.action,
        "risk": row.risk,
        "status": row.status,
        "choice": row.choice,
        "options": row.options if isinstance(row.options, list) else [],
        "details": details,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


async def create_or_get_approval(
    db: AsyncSession,
    *,
    session_id: int,
    request_hash: str,
    tool_call_id: str,
    command: str,
    action: str,
    risk: str,
    options: list[dict[str, Any]],
    details: dict[str, Any],
    expires_at: datetime,
) -> GitApproval:
    existing = await db.scalar(
        select(GitApproval).where(
            GitApproval.session_id == session_id,
            GitApproval.request_hash == request_hash,
        )
    )
    if existing is not None:
        return existing
    row = GitApproval(
        id=str(uuid4()),
        session_id=session_id,
        request_hash=request_hash,
        tool_call_id=tool_call_id,
        command=command,
        action=action,
        risk=risk,
        status=PENDING,
        options=options,
        details=details,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    return row


async def get_approval(
    db: AsyncSession, *, session_id: int, approval_id: str
) -> GitApproval | None:
    return await db.scalar(
        select(GitApproval).where(
            GitApproval.id == approval_id,
            GitApproval.session_id == session_id,
        )
    )


async def pending_approval(
    db: AsyncSession, *, session_id: int
) -> GitApproval | None:
    now = datetime.now(UTC)
    row = await db.scalar(
        select(GitApproval)
        .where(
            GitApproval.session_id == session_id,
            GitApproval.status == PENDING,
        )
        .order_by(GitApproval.created_at.desc())
        .limit(1)
    )
    if row is not None and row.expires_at <= now:
        row.status = "expired"
        row.resolved_at = now
        await db.flush()
        return None
    return row


async def resolve_approval(
    db: AsyncSession,
    *,
    session_id: int,
    approval_id: str,
    choice: str,
) -> GitApproval | None:
    row = await get_approval(db, session_id=session_id, approval_id=approval_id)
    if row is None:
        return None
    if row.status != PENDING:
        return row
    valid = {
        str(item.get("id"))
        for item in row.options
        if isinstance(item, dict)
    }
    if choice not in valid:
        raise ValueError(f"无效审批选项：{choice}")
    if row.expires_at <= datetime.now(UTC):
        row.status = "expired"
    else:
        row.choice = choice
        row.status = {
            "allow": "approved",
            "deny": "denied",
            "terminate": "terminated",
        }[choice]
    row.resolved_at = datetime.now(UTC)
    await db.flush()
    return row


async def consume_approval(
    db: AsyncSession,
    *,
    session_id: int,
    approval_id: str,
) -> bool:
    row = await get_approval(db, session_id=session_id, approval_id=approval_id)
    if row is None or row.status != "approved":
        return False
    row.status = "consumed"
    await db.flush()
    return True


async def expire_approval(
    db: AsyncSession,
    *,
    session_id: int,
    approval_id: str,
) -> bool:
    row = await get_approval(db, session_id=session_id, approval_id=approval_id)
    if row is None or row.status != PENDING:
        return False
    row.status = "expired"
    row.resolved_at = datetime.now(UTC)
    await db.flush()
    return True


__all__ = [
    "PENDING",
    "RESOLVED",
    "approval_payload",
    "consume_approval",
    "create_or_get_approval",
    "expire_approval",
    "get_approval",
    "pending_approval",
    "resolve_approval",
]
