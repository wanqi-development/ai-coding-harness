"""Persistent Git command approval lifecycle."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_coding_harness.db import Base, dao
from ai_coding_harness.db.models import AgentSession


@asynccontextmanager
async def _database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approvals.sqlite3'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class _Db:
        @asynccontextmanager
        async def session(self):
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

    db = _Db()
    async with db.session() as session:
        row = AgentSession(public_id=uuid.uuid4())
        session.add(row)
        await session.flush()
        session_id = row.id
    try:
        yield db, session_id
    finally:
        await engine.dispose()


OPTIONS = [
    {"id": "deny", "label": "拒绝", "description": "", "tone": "safe"},
    {"id": "allow", "label": "允许", "description": "", "tone": "danger"},
    {"id": "terminate", "label": "终止", "description": "", "tone": "neutral"},
]


@pytest.mark.asyncio
async def test_approval_is_one_shot_and_resolution_is_persisted(
    tmp_path: Path,
) -> None:
    async with _database(tmp_path) as (db, session_id), db.session() as session:
        row = await dao.create_or_get_approval(
            session,
            session_id=session_id,
            request_hash="a" * 64,
            tool_call_id="call-1",
            command="git reset --hard",
            action="reset",
            risk="discard_worktree",
            options=OPTIONS,
            details={"branch": "feature/x", "head_sha": "b" * 40},
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        same = await dao.create_or_get_approval(
            session,
            session_id=session_id,
            request_hash="a" * 64,
            tool_call_id="ignored",
            command="ignored",
            action="",
            risk="",
            options=OPTIONS,
            details={},
            expires_at=datetime.now(UTC),
        )
        assert same.id == row.id

        resolved = await dao.resolve_approval(
            session,
            session_id=session_id,
            approval_id=row.id,
            choice="allow",
        )
        assert resolved is not None
        assert resolved.status == "approved"
        assert await dao.consume_approval(
            session,
            session_id=session_id,
            approval_id=row.id,
        )
        assert not await dao.consume_approval(
            session,
            session_id=session_id,
            approval_id=row.id,
        )
        assert (
            await dao.pending_approval(session, session_id=session_id)
            is None
        )


@pytest.mark.asyncio
async def test_invalid_approval_choice_does_not_change_pending_state(
    tmp_path: Path,
) -> None:
    async with _database(tmp_path) as (db, session_id), db.session() as session:
        row = await dao.create_or_get_approval(
            session,
            session_id=session_id,
            request_hash="c" * 64,
            tool_call_id="call-2",
            command="git clean -fd",
            action="clean",
            risk="delete_untracked",
            options=OPTIONS,
            details={},
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        with pytest.raises(ValueError):
            await dao.resolve_approval(
                session,
                session_id=session_id,
                approval_id=row.id,
                choice="something-else",
            )
        pending = await dao.pending_approval(session, session_id=session_id)
        assert pending is not None
        assert pending.status == "pending"
