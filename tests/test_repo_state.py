"""无缓存 PR 快照与状态候选。"""

from __future__ import annotations

from ai_coding_harness.server import repo_state
from ai_coding_harness.server.intent import gate_intent_for_state
from ai_coding_harness.server.repo_state import (
    PullSnapshot,
    describe_snapshot,
    intent_candidates,
)


def _pull(number: int, *, state: str = "open", title: str = "PR") -> dict:
    return {"number": number, "title": title, "state": state}


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
        assert intent_candidates(PullSnapshot((_pull(1),))) == ("qa", "rework")
        assert intent_candidates(PullSnapshot((_pull(1, state="merged"),))) == (
            "qa",
            "plan",
            "coding",
        )

    def test_description_is_explicitly_live(self) -> None:
        assert "本轮实时查询" in describe_snapshot(PullSnapshot(()))

    async def test_loader_always_calls_the_platform_list(self, monkeypatch) -> None:
        calls = 0

        class _Client:
            async def all_pulls(self):
                nonlocal calls
                calls += 1
                return [_pull(1, state="merged")]

        monkeypatch.setattr(
            repo_state, "build_git_client", lambda *_a, **_kw: _Client()
        )

        first = await repo_state.load_pull_snapshot(object(), object())
        second = await repo_state.load_pull_snapshot(object(), object())

        assert first.pulls and second.pulls
        assert calls == 2


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
        assert "没有还开着的 PR" in result.reason
