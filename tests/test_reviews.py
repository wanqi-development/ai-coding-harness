"""Versioned local/PR reviews and explicit PR delivery authorization."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.runnables.config import var_child_runnable_config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Base, dao
from ai_coding_harness.db.models import (
    AgentSession,
    PullRequest,
    SessionRecord,
    SessionRecordDetail,
)
from ai_coding_harness.tools import git as git_mod
from ai_coding_harness.tools import review as review_mod
from ai_coding_harness.tools.git import GiteeRepo, build_pr_tools
from ai_coding_harness.tools.review import (
    CodeVersion,
    ReviewContext,
    build_review_tools,
    current_code_version,
)
from ai_coding_harness.tools.work_task import WorkTaskContext


@asynccontextmanager
async def _database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'reviews.sqlite3'}")
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


def _version(revision: str = "a" * 64) -> CodeVersion:
    return CodeVersion(
        base_ref="master",
        base_sha="1" * 40,
        head_ref="feature/review-gate",
        head_sha="2" * 40,
        code_revision=revision,
        dirty=True,
    )


async def _store(
    db,
    session_id: int,
    *,
    revision: str = "a" * 64,
    findings: list[dict] | None = None,
    stage: str = "local",
    pull_request_id: int | None = None,
):
    async with db.session() as session:
        return await dao.store_review(
            session,
            session_id=session_id,
            plan_id=None,
            pull_request_id=pull_request_id,
            stage=stage,
            base_ref="master",
            base_sha="1" * 40,
            head_ref="feature/review-gate",
            head_sha="2" * 40,
            code_revision=revision,
            summary="review summary",
            report_markdown="# Review\n\nNo structural problems.",
            findings=findings or [],
        )


async def test_store_review_persists_a_replayable_session_record(tmp_path: Path) -> None:
    async with _database(tmp_path) as (db, session_id):
        review, findings = await _store(db, session_id)

        assert review.status == "completed"
        assert review.stage == "local"
        assert findings == []
        async with db.session() as session:
            record = await session.scalar(
                select(SessionRecord).where(SessionRecord.type == "review")
            )
            assert record is not None
            assert record.ref_id == review.id
            detail = await session.get(SessionRecordDetail, record.id)
            assert detail is not None
            assert detail.extra["review"]["review_id"] == review.id
            assert detail.extra["next_action"] == (
                "本地审查完成。下一步由主控按当前任务目标决定继续修改或交付 PR；"
                "创建并合并 PR 后本阶段任务完成。"
            )


async def test_save_review_updates_turn_state_and_reminds_before_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _database(tmp_path) as (db, session_id):
        async def fake_version(*_args, **_kwargs):
            return _version()

        monkeypatch.setattr(review_mod, "current_code_version", fake_version)
        state = TurnState(plan_id=17)
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            tool = next(
                item
                for item in build_review_tools(
                    ReviewContext(
                        database=db,
                        session_id=session_id,
                        platform="gitee",
                        owner="owner",
                        repo="repo",
                        repo_dir="/ai_coding/project/repo",
                        backend=SimpleNamespace(),
                        plan_id=None,
                    )
                )
                if item.name == "save_review"
            )
            message = await tool.ainvoke(
                {
                    "report_markdown": "# Review\n\nLooks good.",
                    "findings": [],
                }
            )
        finally:
            var_child_runnable_config.reset(token)

        assert "下一步由主控按当前任务目标决定继续修改或交付 PR" in message
        assert state.review_id is not None
        assert state.review_payload is not None
        assert state.review_payload["plan_id"] == 17
        async with db.session() as session:
            latest = await dao.latest_review(session, session_id=session_id)
            assert latest is not None
            assert latest[0].id == state.review_id


async def test_pr_stage_review_resolves_pull_request_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _database(tmp_path) as (db, session_id):
        pull_id = None
        async with db.session() as session:
            pull = PullRequest(
                session_id=session_id,
                platform="gitee",
                repo_owner="owner",
                repo_name="repo",
                number=12,
                state="open",
            )
            session.add(pull)
            await session.flush()
            pull_id = pull.id

        async def fake_version(*_args, **_kwargs):
            return _version()

        monkeypatch.setattr(review_mod, "current_code_version", fake_version)
        tool = next(
            item
            for item in build_review_tools(
                ReviewContext(
                    database=db,
                    session_id=session_id,
                    platform="gitee",
                    owner="owner",
                    repo="repo",
                    repo_dir="/ai_coding/project/repo",
                    backend=SimpleNamespace(),
                )
            )
            if item.name == "save_review"
        )
        message = await tool.ainvoke(
            {
                "report_markdown": "# PR Review\n\nLooks good.",
                "findings": [],
                "stage": "pr",
                "pull_request_number": 12,
            }
        )

        assert "review_id=" in message
        assert "下一步请检查结论并合并 PR；合并后本阶段任务完成" in message
        async with db.session() as session:
            latest = await dao.latest_review(session, session_id=session_id, stage="pr")
            assert latest is not None
            assert latest[0].pull_request_id == pull_id
            record = await session.scalar(
                select(SessionRecord)
                .where(SessionRecord.type == "review")
                .order_by(SessionRecord.id.desc())
            )
            assert record is not None
            detail = await session.get(SessionRecordDetail, record.id)
            assert detail is not None
            assert detail.extra["next_action"] == (
                "PR 审查完成。下一步请检查结论并合并 PR；合并后本阶段任务完成。"
            )


async def test_current_code_version_stays_stable_across_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_sha = {"value": "2" * 40}

    async def fake_exec(_backend, command: str):
        if "rev-parse --verify" in command:
            return "1" * 40 + "\n", 0
        if "rev-parse HEAD" in command:
            return head_sha["value"] + "\n", 0
        if "diff --binary" in command:
            return "d" * 64 + "  -\n", 0
        if "ls-files --others" in command:
            return "e" * 64 + "  -\n", 0
        if "status --porcelain" in command:
            return " M app.py\n", 0
        if "branch --show-current" in command:
            return "feature/review-gate\n", 0
        raise AssertionError(command)

    monkeypatch.setattr(review_mod, "_exec", fake_exec)
    before = await current_code_version(
        SimpleNamespace(), "/ai_coding/project/repo", base_ref="master"
    )
    head_sha["value"] = "3" * 40
    after = await current_code_version(
        SimpleNamespace(), "/ai_coding/project/repo", base_ref="master"
    )

    assert before.code_revision == after.code_revision
    assert before.head_sha != after.head_sha
    assert before.dirty and after.dirty


async def test_create_pr_uses_task_and_submission_without_a_separate_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Client:
        async def find_open_pull(self, *, head, base):
            calls.append("find")
            return {"number": 8, "html_url": "https://example/pr/8"}

    async with _database(tmp_path) as (db, session_id):
        async with db.session() as session:
            task = await dao.create_work_task(
                session,
                session_id=session_id,
                owner="owner",
                repo="repo",
                title="task",
                requirement_summary="实现功能",
                base_ref="master",
                branch="feature/x",
            )
            await dao.append_task_submission(
                session,
                session_id=session_id,
                task_id=task.id,
                commit_sha="a" * 40,
                head_sha="a" * 40,
                branch="feature/x",
                base_ref="master",
                summary="完成实现",
                merged_summary="完成实现",
            )
            task_id = task.id

        monkeypatch.setattr(git_mod, "build_git_client", lambda *_a, **_kw: _Client())
        ctx = WorkTaskContext(
            database=db,
            session_id=session_id,
            platform="gitee",
            owner="owner",
            repo="repo",
        )
        tools = build_pr_tools(
            Settings(_env_file=None),
            GiteeRepo(owner="owner", repo="repo", token="t"),
            database=db,
            session_id=session_id,
            work_task_ctx=ctx,
        )
        create = next(item for item in tools if item.name == "create_pr")

        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            allowed = await create.ainvoke(
                {"title": "t", "task_id": task_id, "body": "b"}
            )
        finally:
            var_child_runnable_config.reset(token)

    assert "PR 已存在，直接复用" in allowed
    assert state.pr == 8
    assert calls == ["find"]


async def test_create_pr_rejects_task_and_submission_ref_mismatch_before_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Client:
        async def find_open_pull(self, *, head, base):
            calls.append(f"find:{head}:{base}")

    async with _database(tmp_path) as (db, session_id):
        async with db.session() as session:
            task = await dao.create_work_task(
                session,
                session_id=session_id,
                owner="owner",
                repo="repo",
                title="task",
                requirement_summary="实现功能",
                base_ref="main",
                branch="feature/x",
            )
            await dao.append_task_submission(
                session,
                session_id=session_id,
                task_id=task.id,
                commit_sha="a" * 40,
                head_sha="a" * 40,
                branch="feature/x",
                base_ref="main",
                summary="完成实现",
                merged_summary="完成实现",
            )
            # 模拟旧版本留下的坏数据：base 被写成了 head。
            await dao.update_work_task(
                session,
                session_id=session_id,
                task_id=task.id,
                base_ref="feature/x",
            )
            task_id = task.id

        monkeypatch.setattr(git_mod, "build_git_client", lambda *_a, **_kw: _Client())
        ctx = WorkTaskContext(
            database=db,
            session_id=session_id,
            platform="gitee",
            owner="owner",
            repo="repo",
        )
        create = next(
            item
            for item in build_pr_tools(
                Settings(_env_file=None),
                GiteeRepo(owner="owner", repo="repo", token="t"),
                database=db,
                session_id=session_id,
                work_task_ctx=ctx,
            )
            if item.name == "create_pr"
        )
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            message = await create.ainvoke(
                {"title": "t", "task_id": task_id, "body": "b"}
            )
        finally:
            var_child_runnable_config.reset(token)

        assert "任务记录与最新 submission 的 branch/base 不一致" in message
        assert calls == []


async def test_create_pr_rejects_identical_head_and_base_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Client:
        async def find_open_pull(self, *, head, base):
            calls.append("find")

    monkeypatch.setattr(git_mod, "build_git_client", lambda *_a, **_kw: _Client())
    create = next(
        item
        for item in build_pr_tools(
            Settings(_env_file=None),
            GiteeRepo(owner="owner", repo="repo", token="t"),
        )
        if item.name == "create_pr"
    )
    message = await create.ainvoke(
        {
            "title": "t",
            "body": "b",
            "head": "feature/x",
            "base": "feature/x",
        }
    )

    assert "head 与 base 不能相同" in message
    assert calls == []
