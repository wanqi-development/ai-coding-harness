"""方案执行状态回收：before/after_agent 的 PR 判定与幂等更新。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.runnables.config import var_child_runnable_config

from ai_coding_harness.agent.middleware import plan_recovery as recovery_mod
from ai_coding_harness.agent.middleware.plan_recovery import PlanRecoveryMiddleware
from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.db import dao


class _Db:
    @asynccontextmanager
    async def session(self):
        yield None


@pytest.fixture
def ambient():
    def _set(state: TurnState | None) -> None:
        var_child_runnable_config.set(
            {"configurable": {"turn_state": state} if state is not None else {}}
        )

    token = var_child_runnable_config.set({"configurable": {}})
    yield _set
    var_child_runnable_config.reset(token)


def _middleware() -> PlanRecoveryMiddleware:
    return PlanRecoveryMiddleware(database=_Db(), session_id=7, role="coder")  # type: ignore[arg-type]


async def test_before_agent_restores_executing_plan_without_pr(
    ambient, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = TurnState(plan_id=56)
    ambient(state)
    seen: dict[str, Any] = {}

    async def fake_reclaim(db, *, session_id, has_pr, plan_id):
        seen.update(session_id=session_id, has_pr=has_pr, plan_id=plan_id)
        return 56

    monkeypatch.setattr(recovery_mod.dao, "reclaim_executing_plan", fake_reclaim)

    await _middleware().abefore_agent({}, SimpleNamespace())

    assert seen == {"session_id": 7, "has_pr": False, "plan_id": 56}


async def test_after_agent_marks_plan_done_when_turn_created_pr(
    ambient, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = TurnState(plan_id=56)
    state.pr = 88
    ambient(state)
    seen: dict[str, Any] = {}

    async def fake_reclaim(db, *, session_id, has_pr, plan_id):
        seen.update(session_id=session_id, has_pr=has_pr, plan_id=plan_id)
        return 56

    monkeypatch.setattr(recovery_mod.dao, "reclaim_executing_plan", fake_reclaim)

    await _middleware().aafter_agent({}, SimpleNamespace())

    assert seen["has_pr"] is True


async def test_open_pr_snapshot_is_the_cross_round_fallback(
    ambient, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = TurnState(
        plan_id=56,
        open_prs=({"number": 88, "title": "PR", "state": "open"},),
    )
    ambient(state)
    seen: dict[str, Any] = {}

    async def fake_reclaim(db, *, session_id, has_pr, plan_id):
        seen.update(session_id=session_id, has_pr=has_pr, plan_id=plan_id)
        return 56

    monkeypatch.setattr(recovery_mod.dao, "reclaim_executing_plan", fake_reclaim)

    await _middleware().abefore_agent({}, SimpleNamespace())

    assert seen["has_pr"] is True


async def test_missing_turn_state_is_a_noop(ambient, monkeypatch: pytest.MonkeyPatch) -> None:
    ambient(None)

    async def should_not_run(*args, **kwargs):
        raise AssertionError("没有 TurnState 时不应访问数据库")

    monkeypatch.setattr(recovery_mod.dao, "reclaim_executing_plan", should_not_run)

    await _middleware().abefore_agent({}, SimpleNamespace())


def test_turn_state_exposes_round_pr_and_plan_fields() -> None:
    state = TurnState()

    assert state.pr is None
    assert state.plan_id is None
    assert state.has_pr is False

    state.pr = 88
    state.plan_id = 56

    assert state.has_pr is True


class _Result:
    def __init__(self, plan_id: int) -> None:
        self._plan_id = plan_id

    def scalar_one_or_none(self) -> int:
        return self._plan_id


class _PlanDb:
    def __init__(self, plan: SimpleNamespace) -> None:
        self._plan = plan
        self.values: dict[str, Any] = {}

    async def scalar(self, query) -> SimpleNamespace:
        return self._plan

    async def execute(self, statement) -> _Result:
        self.values = dict(statement.compile().params)
        return _Result(self._plan.id)


@pytest.mark.parametrize(
    ("has_pr", "plan_status", "expected_step", "started_at"),
    [
        (True, "done", "done", "unchanged"),
        (False, "pending", "pending", None),
    ],
)
async def test_reclaim_executing_plan_updates_status_and_steps(
    has_pr: bool,
    plan_status: str,
    expected_step: str,
    started_at: str | None,
) -> None:
    plan = SimpleNamespace(
        id=56,
        steps=[
            {"index": 1, "goal": "已完成", "status": "done"},
            {"index": 2, "goal": "进行中", "status": "running"},
            {"index": 3, "goal": "未开始", "status": "pending"},
        ],
    )
    db = _PlanDb(plan)

    result = await dao.reclaim_executing_plan(
        db,  # type: ignore[arg-type]
        session_id=7,
        has_pr=has_pr,
    )

    assert result == 56
    assert db.values["status"] == plan_status
    assert db.values["steps"][1]["status"] == expected_step
    if started_at == "unchanged":
        assert "started_at" not in db.values
    else:
        assert db.values["started_at"] is started_at
