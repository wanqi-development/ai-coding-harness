"""无缓存 PR 快照与状态候选。"""

from __future__ import annotations

from types import SimpleNamespace

from ai_coding_harness.server import repo_state
from ai_coding_harness.server.intent import gate_intent_for_state
from ai_coding_harness.server.repo_state import (
    PullSnapshot,
    describe_snapshot,
    intent_candidates,
)


def _pull(
    number: int,
    *,
    state: str = "open",
    title: str = "PR",
    has_comments: bool | None = None,
    head_ref: str = "feature/x",
    head_sha: str = "a" * 40,
    author: str = "tester",
) -> dict:
    pull = {
        "number": number,
        "title": title,
        "state": state,
        "head": {"ref": head_ref, "sha": head_sha},
        "base": {"ref": "master"},
        "user": {"login": author},
        "html_url": f"https://gitee.com/acme/demo/pulls/{number}",
    }
    if has_comments is not None:
        pull["_has_comments"] = has_comments
    return pull


class _SessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return None


class _Db:
    def session(self):
        return _SessionCtx()


SETTINGS = SimpleNamespace(
    sandbox_git_user_name="ai-coding-agent",
    sandbox_git_user_email="agent@example.com",
)
REPO = SimpleNamespace(
    platform=SimpleNamespace(value="gitee"),
    owner="acme",
    repo="demo",
)


class TestSnapshot:
    def test_open_prs_are_derived_from_the_single_list(self) -> None:
        snapshot = PullSnapshot(
            (
                _pull(1, state="open"),
                _pull(2, state="merged"),
                _pull(3, state="closed"),
            )
        )

        assert [pull["number"] for pull in snapshot.open_prs] == [1]
        assert snapshot.has_open_prs is True

    def test_candidates_follow_the_live_snapshot(self) -> None:
        assert intent_candidates(None) == ("qa",)
        assert intent_candidates(PullSnapshot((_pull(1, has_comments=False),))) == (
            "qa",
            "coding",
        )
        assert intent_candidates(PullSnapshot((_pull(1, has_comments=True),))) == (
            "qa",
            "rework",
        )
        assert intent_candidates(PullSnapshot((_pull(1, state="merged"),))) == (
            "qa",
            "plan",
            "coding",
        )

    def test_one_reviewed_pr_blocks_coding_even_if_another_is_unreviewed(self) -> None:
        snapshot = PullSnapshot(
            (
                _pull(1, has_comments=False),
                _pull(2, has_comments=True),
            )
        )
        assert intent_candidates(snapshot) == ("qa", "rework")

    def test_description_is_explicitly_live(self) -> None:
        assert "本轮实时查询" in describe_snapshot(PullSnapshot(()))

    async def test_loader_always_calls_the_platform_list(self, monkeypatch) -> None:
        calls = 0

        class _Client:
            async def all_pulls(self, *, limit, until_number):
                assert limit == 10
                nonlocal calls
                calls += 1
                return [_pull(1, state="merged")]

            async def open_pulls(self):
                return []

            async def list_pull_comments(self, **kwargs):
                raise AssertionError("merged PR 不应查询评论")

        monkeypatch.setattr(
            repo_state, "build_git_client", lambda *_a, **_kw: _Client()
        )

        first = await repo_state.load_pull_snapshot(object(), object())
        second = await repo_state.load_pull_snapshot(object(), object())

        assert first.pulls and second.pulls
        assert calls == 2

    async def test_loader_checks_comments_only_for_open_prs(self, monkeypatch) -> None:
        checked: list[int] = []

        class _Client:
            async def all_pulls(self, *, limit, until_number):
                return [_pull(1), _pull(2, state="merged"), _pull(3)]

            async def open_pulls(self):
                return [_pull(1), _pull(3)]

            async def list_pull_comments(self, *, number, page, limit):
                checked.append(number)
                return [{"id": 1}] if number == 3 else []

        monkeypatch.setattr(
            repo_state, "build_git_client", lambda *_a, **_kw: _Client()
        )
        snapshot = await repo_state.load_pull_snapshot(object(), object())

        assert checked == [1, 3]
        assert [pull["_has_comments"] for pull in snapshot.open_prs] == [False, True]

    async def test_untracked_open_pull_is_bootstrapped_from_commit_identity(
        self, monkeypatch
    ) -> None:
        pull = _pull(11)
        commits = [
            {
                "commit": {
                    "author": {
                        "name": "ai-coding-agent",
                        "email": "agent@example.com",
                    }
                }
            }
        ]

        class _Client:
            async def all_pulls(self, *, limit, until_number):
                assert limit == 10
                return [pull]

            async def open_pulls(self):
                return [pull]

            async def list_pull_commits(self, *, number, page, limit):
                assert number == 11
                return commits

            async def list_pull_comments(self, *, number, page, limit):
                return []

        async def no_existing(db, **kwargs):
            return []

        async def no_watermark(db, **kwargs):
            return None

        async def upsert(db, **kwargs):
            return SimpleNamespace(
                id=1,
                number=kwargs["number"],
                source=kwargs["source"],
                plan_id=None,
                head_sha=kwargs["head_sha"],
                synced_head_sha=None,
            )

        monkeypatch.setattr(repo_state, "build_git_client", lambda *_a, **_kw: _Client())
        monkeypatch.setattr(repo_state.dao, "newest_pull_number", no_watermark)
        monkeypatch.setattr(repo_state.dao, "list_pull_requests", no_existing)
        monkeypatch.setattr(repo_state.dao, "upsert_pull_request", upsert)

        snapshot = await repo_state.load_pull_snapshot(
            SETTINGS, REPO, database=_Db(), session_id=3
        )

        assert snapshot.blocked_reason is None
        assert snapshot.open_prs[0]["_source"] == "legacy_agent"
        assert snapshot.open_prs[0]["_plan_id"] is None

    async def test_external_open_pull_hard_blocks_the_snapshot(
        self, monkeypatch
    ) -> None:
        pull = _pull(12, author="other-user")
        commits = [
            {
                "commit": {
                    "author": {"name": "other-user", "email": "other@example.com"}
                }
            }
        ]

        class _Client:
            async def all_pulls(self, *, limit, until_number):
                return [pull]

            async def open_pulls(self):
                return [pull]

            async def list_pull_commits(self, **kwargs):
                return commits

            async def list_pull_comments(self, **kwargs):
                raise AssertionError("外部 PR 不应查询评论")

        async def no_existing(db, **kwargs):
            return []

        async def no_watermark(db, **kwargs):
            return None

        async def upsert(db, **kwargs):
            return SimpleNamespace(
                id=2,
                number=kwargs["number"],
                source=kwargs["source"],
                plan_id=None,
                head_sha=kwargs["head_sha"],
                synced_head_sha=None,
            )

        monkeypatch.setattr(repo_state, "build_git_client", lambda *_a, **_kw: _Client())
        monkeypatch.setattr(repo_state.dao, "newest_pull_number", no_watermark)
        monkeypatch.setattr(repo_state.dao, "list_pull_requests", no_existing)
        monkeypatch.setattr(repo_state.dao, "upsert_pull_request", upsert)

        snapshot = await repo_state.load_pull_snapshot(
            SETTINGS, REPO, database=_Db(), session_id=3
        )

        assert snapshot.open_prs[0]["_source"] == "external"
        assert "#12" in (snapshot.blocked_reason or "")
        assert intent_candidates(snapshot) == ("qa",)

    def test_owned_snapshot_exposes_head_and_plan(self) -> None:
        owned = {
            **_pull(13, has_comments=False),
            "_source": "agent",
            "_plan_id": 57,
            "_pull_id": 9,
            "_head_changed": True,
        }
        snapshot = PullSnapshot((owned,), open_pulls=(owned,))

        assert snapshot.owned_open_prs[0]["_plan_id"] == 57
        assert snapshot.has_open_prs is True


class TestGate:
    def test_pending_pr_blocks_coding_and_plan_but_allows_rework(self) -> None:
        allowed = ("qa", "rework")

        assert gate_intent_for_state("rework", allowed).intent == "rework"
        coding = gate_intent_for_state("coding", allowed)
        plan = gate_intent_for_state("plan", allowed)

        assert coding.intent is None
        assert "coding 已暂停" in coding.reason
        assert plan.intent is None

    def test_rework_without_a_pr_is_blocked(self) -> None:
        result = gate_intent_for_state("rework", ("qa", "plan", "coding"))
        assert result.intent is None
        assert "当前没有可返工的 PR" in result.reason

    def test_unreviewed_open_pr_keeps_coding_available(self) -> None:
        allowed = ("qa", "coding")

        assert gate_intent_for_state("coding", allowed).intent == "coding"
        rework = gate_intent_for_state("rework", allowed)
        assert rework.intent is None
        assert "当前没有可返工的 PR" in rework.reason
