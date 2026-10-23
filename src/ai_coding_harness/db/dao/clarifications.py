"""意图澄清 pending 状态的 DAO。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import (
    Clarification,
)


# ---------------------------------------------------------------------------
# clarifications（意图澄清的一次性 pending 状态）
# ---------------------------------------------------------------------------
async def save_clarification(
    db: AsyncSession,
    *,
    session_id: int,
    original_message: str,
    question: str,
    options: list[dict[str, Any]],
    steps: list[dict[str, Any]] | None = None,
) -> Clarification:
    """写入本会话的待澄清状态；旧的一条直接替换（每个会话最多一条）。"""
    await db.execute(delete(Clarification).where(Clarification.session_id == session_id))
    row = Clarification(
        id=str(uuid.uuid4()),
        session_id=session_id,
        original_message=original_message,
        question=question,
        options=options,
        steps=steps or [],
    )
    db.add(row)
    await db.flush()
    return row


async def get_clarification(
    db: AsyncSession, *, session_id: int
) -> Clarification | None:
    return await db.scalar(
        select(Clarification).where(Clarification.session_id == session_id)
    )


async def delete_clarification(db: AsyncSession, *, session_id: int) -> bool:
    result = await db.execute(
        delete(Clarification).where(Clarification.session_id == session_id)
    )
    return bool(result.rowcount)
