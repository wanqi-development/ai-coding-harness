"""PR 归属表的稳定口径。"""

from __future__ import annotations

from types import SimpleNamespace

from ai_coding_harness.db import dao
from ai_coding_harness.tools.pr_client import commits_match_agent_identity


class _Db:
    def __init__(self, existing=None) -> None:
        self.existing = existing
        self.added = []
        self.flushes = 0

    async def scalar(self, query):
        return self.existing

    def add(self, row) -> None:
        self.added.append(row)
        self.existing = row

    async def flush(self) -> None:
        self.flushes += 1


async def test_new_agent_pull_starts_at_the_synced_head() -> None:
    db = _Db()

    row = await dao.upsert_pull_request(
        db,  # type: ignore[arg-type]
        platform="gitee",
        owner="acme",
        repo="demo",
        number=24,
        title="fix",
        state="open",
        head_ref="fix/x",
        head_sha="a" * 40,
        source=dao.PR_SOURCE_AGENT,
        plan_id=57,
        session_id=3,
        mark_synced=True,
    )

    assert row.source == dao.PR_SOURCE_AGENT
    assert row.synced_head_sha == "a" * 40
    assert row.plan_id == 57
    assert db.flushes == 1


async def test_external_source_is_never_silently_adopted() -> None:
    existing = SimpleNamespace(
        platform="gitee",
        repo_owner="acme",
        repo_name="demo",
        number=24,
        title="external",
        state="open",
        url="u",
        author="other",
        head_ref="fix/x",
        head_sha="a" * 40,
        synced_head_sha=None,
        base_ref="master",
        source=dao.PR_SOURCE_EXTERNAL,
        session_id=None,
        plan_id=None,
        remote_created_at=None,
        remote_updated_at=None,
    )
    db = _Db(existing)

    row = await dao.upsert_pull_request(
        db,  # type: ignore[arg-type]
        platform="gitee",
        owner="acme",
        repo="demo",
        number=24,
        title="same",
        state="open",
        head_sha="b" * 40,
        source=dao.PR_SOURCE_AGENT,
        session_id=3,
        plan_id=57,
    )

    assert row.source == dao.PR_SOURCE_EXTERNAL
    assert row.session_id == 3
    assert row.plan_id == 57


def test_only_agent_sources_are_owned() -> None:
    assert dao.is_owned_pull_source("agent") is True
    assert dao.is_owned_pull_source("legacy_agent") is True
    assert dao.is_owned_pull_source("external") is False
    assert dao.is_owned_pull_source("unknown") is False


def test_commit_identity_requires_every_commit_to_match() -> None:
    agent = {
        "commit": {
            "author": {"name": "ai-coding-agent", "email": "agent@example.com"}
        }
    }
    human = {
        "commit": {"author": {"name": "alice", "email": "alice@example.com"}}
    }

    assert (
        commits_match_agent_identity(
            [agent], name="ai-coding-agent", email="agent@example.com"
        )
        is True
    )
    assert (
        commits_match_agent_identity(
            [agent, human], name="ai-coding-agent", email="agent@example.com"
        )
        is False
    )
    assert commits_match_agent_identity([], name="a", email="b") is False
