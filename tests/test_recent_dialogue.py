"""Intent context: three complete rounds plus interrupted messages in the window."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_coding_harness.db import Base, dao
from ai_coding_harness.db.models import (
    AgentSession,
    SessionRecord,
    SessionRecordDetail,
)


@asynccontextmanager
async def _database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dialogue.sqlite3'}")
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


async def _add(
    db,
    session_id: int,
    kind: str,
    content: str,
    *,
    extra: dict | None = None,
) -> None:
    async with db.session() as session:
        record = SessionRecord(
            session_id=session_id,
            type=kind,
            summary=content[:200],
        )
        session.add(record)
        await session.flush()
        session.add(
            SessionRecordDetail(
                record_id=record.id,
                content=content,
                extra=extra or {},
            )
        )


@pytest.mark.asyncio
async def test_recent_dialogue_skips_interrupted_round_limit_and_keeps_them(
    tmp_path: Path,
) -> None:
    async with _database(tmp_path) as (db, session_id):
        await _add(db, session_id, "user", "窗口外的旧问题")
        await _add(db, session_id, "assistant", "窗口外的旧回答")

        await _add(db, session_id, "user", "第一轮：提交 PR")
        await _add(db, session_id, "assistant", "第一轮完整回答")
        await _add(db, session_id, "user", "第二轮：执行到一半")
        await _add(
            db,
            session_id,
            "assistant",
            "第二轮中断前的内容",
            extra={"partial": True, "cancelled": True},
        )
        await _add(db, session_id, "user", "第三轮")
        await _add(db, session_id, "assistant", "第三轮完整回答")
        await _add(
            db,
            session_id,
            "plan",
            "第三轮关联方案",
            extra={},
        )
        await _add(db, session_id, "user", "第四轮")
        await _add(db, session_id, "assistant", "第四轮完整回答")
        await _add(db, session_id, "user", "第五轮：又中断")
        await _add(
            db,
            session_id,
            "assistant",
            "第五轮中断前内容",
            extra={"partial": True},
        )

        async with db.session() as session:
            rows = await dao.recent_dialogue(
                session,
                session_id=session_id,
                rounds=3,
            )

    content = [text for _role, text in rows]
    assert "窗口外的旧问题" not in content
    assert "第一轮：提交 PR" in content
    assert "第二轮中断前的内容" in content
    assert "第五轮中断前内容" in content
    assert "第三轮关联方案" in content
    assert [role for role, _text in rows].count("assistant_interrupted") == 2
