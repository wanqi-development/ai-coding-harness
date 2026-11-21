"""Durable code-task conflict handling and cumulative coder submissions."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from langchain_core.runnables.config import var_child_runnable_config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.db import Base, dao
from ai_coding_harness.db.models import AgentSession
from ai_coding_harness.tools.work_task import WorkTaskContext, build_work_task_tools


@asynccontextmanager
async def _database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.sqlite3'}")
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


def _tool(db, session_id: int, name: str, state: TurnState):
    ctx = WorkTaskContext(
        database=db,
        session_id=session_id,
        platform="gitee",
        owner="owner",
        repo="repo",
    )
    return next(item for item in build_work_task_tools(ctx) if item.name == name)


@pytest.mark.asyncio
async def test_open_task_and_reject_a_new_task_until_current_is_finished(
    tmp_path: Path,
) -> None:
    async with _database(tmp_path) as (db, session_id):
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            opened = await _tool(db, session_id, "openWorkTask", state).ainvoke(
                {
                    "requirement_summary": "实现模型协议适配",
                    "relation": "new",
                    "title": "模型协议适配",
                    "base_ref": "main",
                }
            )
            rejected = await _tool(db, session_id, "openWorkTask", state).ainvoke(
                {
                    "requirement_summary": "顺手再做一个无关功能",
                    "relation": "new",
                }
            )
        finally:
            var_child_runnable_config.reset(token)

        assert '"task_id"' in opened
        assert "当前工作区不能开始新的写代码任务" in rejected
        assert "创建并合并 PR" in rejected
        assert state.work_task_id is not None


@pytest.mark.asyncio
async def test_same_relation_binds_existing_task_and_submissions_accumulate(
    tmp_path: Path,
) -> None:
    async with _database(tmp_path) as (db, session_id):
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            opened = await _tool(db, session_id, "openWorkTask", state).ainvoke(
                {
                    "requirement_summary": "实现接口",
                    "relation": "new",
                    "title": "接口",
                    "base_ref": "main",
                }
            )
            task_id = state.work_task_id
            assert task_id is not None

            rebound = await _tool(db, session_id, "openWorkTask", state).ainvoke(
                {
                    "requirement_summary": "补上同一接口的边界处理",
                    "relation": "same",
                }
            )
            recorded = await _tool(
                db, session_id, "recordTaskSubmission", state
            ).ainvoke(
                {
                    "task_id": task_id,
                    "commit_sha": "a" * 40,
                    "head_sha": "a" * 40,
                    "branch": "feat/api",
                    "base_ref": "main",
                    "summary": "补齐边界处理",
                    "merged_summary": "实现接口并补齐边界处理",
                    "changed_files": ["api.py"],
                    "verification": ["pytest tests/test_api.py"],
                }
            )
        finally:
            var_child_runnable_config.reset(token)

        assert '"task_id"' in opened
        assert f'"task_id": {task_id}' in rebound
        assert "实现接口并补齐边界处理" in recorded
        assert '"changed_files"' in recorded or '"changed_files": [' in recorded


@pytest.mark.asyncio
async def test_submission_requires_pushed_same_head(tmp_path: Path) -> None:
    async with _database(tmp_path) as (db, session_id):
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            await _tool(db, session_id, "openWorkTask", state).ainvoke(
                {
                    "requirement_summary": "实现接口",
                    "relation": "new",
                    "base_ref": "main",
                }
            )
            task_id = state.work_task_id
            assert task_id is not None
            message = await _tool(
                db, session_id, "recordTaskSubmission", state
            ).ainvoke(
                {
                    "task_id": task_id,
                    "commit_sha": "a" * 40,
                    "head_sha": "b" * 40,
                    "branch": "feat/api",
                    "base_ref": "main",
                    "summary": "实现",
                    "merged_summary": "实现",
                }
            )
        finally:
            var_child_runnable_config.reset(token)

        assert "commit_sha 与已推送 head_sha 不一致" in message


@pytest.mark.asyncio
async def test_submission_repairs_a_task_that_saved_head_as_base(
    tmp_path: Path,
) -> None:
    async with _database(tmp_path) as (db, session_id):
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            await _tool(db, session_id, "openWorkTask", state).ainvoke(
                {
                    "requirement_summary": "实现检索契约",
                    "relation": "new",
                    "base_ref": "feature/retrieval-result-contract",
                }
            )
            task_id = state.work_task_id
            assert task_id is not None
            recorded = await _tool(
                db, session_id, "recordTaskSubmission", state
            ).ainvoke(
                {
                    "task_id": task_id,
                    "commit_sha": "a" * 40,
                    "head_sha": "a" * 40,
                    "branch": "feature/retrieval-result-contract",
                    "base_ref": "main",
                    "summary": "完成实现",
                    "merged_summary": "完成实现",
                }
            )
        finally:
            var_child_runnable_config.reset(token)

        assert '"base_ref": "main"' in recorded
        assert '"branch": "feature/retrieval-result-contract"' in recorded


@pytest.mark.asyncio
async def test_same_relation_repairs_legacy_invalid_base_from_submission(
    tmp_path: Path,
) -> None:
    async with _database(tmp_path) as (db, session_id):
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            await _tool(db, session_id, "openWorkTask", state).ainvoke(
                {
                    "requirement_summary": "实现检索契约",
                    "relation": "new",
                    "base_ref": "main",
                }
            )
            task_id = state.work_task_id
            assert task_id is not None
            await _tool(db, session_id, "recordTaskSubmission", state).ainvoke(
                {
                    "task_id": task_id,
                    "commit_sha": "a" * 40,
                    "head_sha": "a" * 40,
                    "branch": "feature/retrieval-result-contract",
                    "base_ref": "main",
                    "summary": "完成实现",
                    "merged_summary": "完成实现",
                }
            )
            async with db.session() as session:
                await dao.update_work_task(
                    session,
                    session_id=session_id,
                    task_id=task_id,
                    base_ref="feature/retrieval-result-contract",
                )
            repaired = await _tool(
                db, session_id, "openWorkTask", state
            ).ainvoke(
                {
                    "requirement_summary": "继续修复审查问题",
                    "relation": "same",
                    "base_ref": "",
                }
            )
        finally:
            var_child_runnable_config.reset(token)

        assert '"base_ref": "main"' in repaired


@pytest.mark.asyncio
async def test_same_relation_without_a_task_or_pr_is_rejected(
    tmp_path: Path,
) -> None:
    async with _database(tmp_path) as (db, session_id):
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            message = await _tool(
                db, session_id, "openWorkTask", state
            ).ainvoke(
                {
                    "requirement_summary": "继续一个不存在的任务",
                    "relation": "same",
                }
            )
        finally:
            var_child_runnable_config.reset(token)

        assert "当前没有可续接的 active task 或 open PR" in message
        assert state.work_task_id is None
