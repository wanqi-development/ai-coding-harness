"""Approval lookup, resolution, and same-run interrupt waiting."""

from __future__ import annotations

from typing import Any

from ai_coding_harness.db import dao
from ai_coding_harness.session.manager import Session


class TurnApprovalMixin:
    async def pending_git_approval(self, session: Session) -> dict[str, Any] | None:
        try:
            async with self._db.session() as db:
                row = await dao.pending_approval(db, session_id=session.db_id)
                return dao.approval_payload(row) if row is not None else None
        except Exception:  # noqa: BLE001 - UI lookup degrades to no pending approval
            return None

    async def resolve_git_approval(
        self,
        session: Session,
        *,
        approval_id: str,
        choice: str,
    ) -> dict[str, Any] | None:
        async with self._db.session() as db:
            row = await dao.resolve_approval(
                db,
                session_id=session.db_id,
                approval_id=approval_id,
                choice=choice,
            )
            return dao.approval_payload(row) if row is not None else None

    async def expire_git_approval(
        self,
        session: Session,
        approval_id: str,
    ) -> None:
        async with self._db.session() as db:
            await dao.expire_approval(
                db,
                session_id=session.db_id,
                approval_id=approval_id,
            )

__all__ = ["TurnApprovalMixin"]
