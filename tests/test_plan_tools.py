"""plan 工具的纯逻辑单测：步骤规范化、渲染、入参校验、增删改查的行为、注册表。

写库那几条用一个假 dao 验**行为**（软删成 closed、执行中不许改步骤……），DB 层本身
（`jsonb_set` 那条原子 SQL）必须连真库单独验证，不在此文件内。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from ai_coding_harness.config import Settings
from ai_coding_harness.tools import plan as plan_mod
from ai_coding_harness.tools.git import GiteeRepo
from ai_coding_harness.tools.plan import (
    PlanContext,
    _format_plan,
    _normalize_steps,
    build_plan_tools,
)
from ai_coding_harness.tools.register import build_subagent_tools


def _ctx() -> PlanContext:
    return PlanContext(session_db_id=1, owner="owner", repo="repo")


def _plan(**over) -> SimpleNamespace:
    base = {"id": 7, "status": "pending", "title": None, "content": "正文", "steps": None}
    base.update(over)
    return SimpleNamespace(**base)


class TestNormalizeSteps:
    def test_renumbers_and_fills_defaults(self):
        steps, error = _normalize_steps(
            [
                {"goal": "第一步", "files": ["a.py"], "done_criteria": "能跑通"},
                {"goal": "第二步"},
            ]
        )
        assert error is None
        assert [s["index"] for s in steps] == [1, 2]
        assert [s["status"] for s in steps] == ["pending", "pending"]
        assert steps[0]["files"] == ["a.py"]
        assert steps[0]["done_criteria"] == "能跑通"
        assert "files" not in steps[1]
        assert "done_criteria" not in steps[1]

    def test_ignores_model_supplied_index_and_status(self):
        """编号与状态由服务端定：模型常从 0 起编号或乱填状态，采信它会错位。"""
        steps, error = _normalize_steps([{"index": 99, "status": "done", "goal": "只有这项"}])
        assert error is None
        assert steps == [{"index": 1, "goal": "只有这项", "status": "pending"}]

    def test_trims_goal(self):
        steps, _ = _normalize_steps([{"goal": "  第一步  "}])
        assert steps[0]["goal"] == "第一步"

    @pytest.mark.parametrize("raw", [None, "不是数组", [], {}, 0])
    def test_rejects_non_list_or_empty(self, raw):
        steps, error = _normalize_steps(raw)
        assert steps is None
        assert "非空数组" in error

    def test_rejects_non_dict_item(self):
        steps, error = _normalize_steps(["第一步"])
        assert steps is None
        assert "第 1 项" in error

    @pytest.mark.parametrize("goal", [None, "", "   "])
    def test_rejects_missing_goal(self, goal):
        steps, error = _normalize_steps([{"goal": goal}])
        assert steps is None
        assert "第 1 项缺少 goal" in error

    def test_error_names_the_offending_position(self):
        steps, error = _normalize_steps([{"goal": "好"}, {"goal": ""}])
        assert steps is None
        assert "第 2 项" in error


class TestFormatPlan:
    def test_renders_each_step_with_status(self):
        text = _format_plan(
            _plan(
                steps=[
                    {
                        "index": 1,
                        "goal": "改 A",
                        "status": "done",
                        "files": ["a.py"],
                        "done_criteria": "测试过",
                    },
                    {"index": 2, "goal": "改 B", "status": "running"},
                ]
            )
        )
        assert "plan_id=7" in text
        assert "1. [done] 改 A" in text
        assert "   - 涉及文件：['a.py']" in text
        assert "   - 完成标准：测试过" in text
        assert "2. [running] 改 B" in text

    def test_missing_status_renders_as_pending(self):
        text = _format_plan(_plan(steps=[{"index": 1, "goal": "改 A"}]))
        assert "1. [pending] 改 A" in text

    def test_non_dict_step_falls_back_to_plain_line(self):
        text = _format_plan(_plan(steps=["历史遗留的纯文本步骤"]))
        assert "1. 历史遗留的纯文本步骤" in text

    def test_omits_step_section_when_no_steps(self):
        assert "## 步骤" not in _format_plan(_plan())


class TestUpdatePlanStepValidation:
    """非法入参必须直接回错——传 database=None 正是在断言这条路径不碰 DB。"""

    @staticmethod
    async def _call(**args) -> str:
        tools = build_plan_tools(None, _ctx())
        tool = next(t for t in tools if t.name == "updatePlanStep")
        return await tool.ainvoke(args)

    async def test_rejects_unknown_status(self):
        msg = await self._call(plan_id=1, index=1, status="finished")
        assert "非法状态：finished" in msg

    async def test_rejects_index_below_one(self):
        msg = await self._call(plan_id=1, index=0, status="done")
        assert "非法步骤序号：0" in msg


class TestPlanCrud:
    """方案对象的增删改查：plan 形态主控手里那一套（假 dao，不碰真库）。"""

    class _Db:
        @asynccontextmanager
        async def session(self):
            yield None

    async def _call(self, monkeypatch, name: str, *, plan=None, latest=None, **args):
        calls: list[tuple] = []

        async def fake_get_plan(db, *, session_id, plan_id):
            calls.append(("get_plan", plan_id, session_id))
            return plan

        async def fake_get_latest(db, *, session_id):
            calls.append(("get_latest", session_id))
            return latest

        async def fake_set_status(db, plan_id, status, *, session_id=None):
            calls.append(("set_status", plan_id, status, session_id))
            return True

        async def fake_set_steps(db, *, plan_id, session_id, steps):
            calls.append(("set_steps", plan_id, len(steps)))
            return True

        async def fake_update(db, *, plan_id, session_id, content=None, title=None):
            calls.append(("update", plan_id, content is not None, title is not None))
            return True

        for attr, fn in (
            ("get_plan", fake_get_plan),
            ("get_latest_plan", fake_get_latest),
            ("set_plan_status", fake_set_status),
            ("set_plan_steps", fake_set_steps),
            ("update_plan_content", fake_update),
        ):
            monkeypatch.setattr(plan_mod.dao, attr, fn)
        tool = next(t for t in build_plan_tools(self._Db(), _ctx()) if t.name == name)
        return await tool.ainvoke(args), calls

    async def test_delete_closes_the_plan(self, monkeypatch):
        """软删：状态改 closed，行还在（与硬删的区别全在这一条上）。"""
        msg, calls = await self._call(
            monkeypatch, "deletePlan", plan=_plan(status="pending"), plan_id=7
        )

        assert "已删除（状态改为 closed）" in msg
        assert ("set_status", 7, "closed", 1) in calls

    async def test_deleting_twice_says_so(self, monkeypatch):
        msg, calls = await self._call(
            monkeypatch, "deletePlan", plan=_plan(status="closed"), plan_id=7
        )

        assert "之前已经删过" in msg
        assert not [c for c in calls if c[0] == "set_status"]

    async def test_a_missing_plan_reports_not_found(self, monkeypatch):
        msg, _ = await self._call(monkeypatch, "deletePlan", plan=None, plan_id=7)

        assert "未找到方案 7" in msg

    async def test_update_passes_both_fields(self, monkeypatch):
        msg, calls = await self._call(
            monkeypatch, "updatePlan", plan=_plan(), plan_id=7, content="新正文", title="新标题"
        )

        assert "正文、标题已更新" in msg
        assert ("update", 7, True, True) in calls

    async def test_update_needs_at_least_one_field(self, monkeypatch):
        """两个字段都不给：直接回错，不碰库（传 database=None 就是在断言这一点）。"""
        tools = build_plan_tools(None, _ctx())
        tool = next(t for t in tools if t.name == "updatePlan")

        msg = await tool.ainvoke({"plan_id": 1})

        assert "至少要给一个" in msg

    async def test_update_is_refused_after_delete(self, monkeypatch):
        msg, calls = await self._call(
            monkeypatch, "updatePlan", plan=_plan(status="closed"), plan_id=7, content="新正文"
        )

        assert "已删除（closed），不再修改" in msg
        assert not [c for c in calls if c[0] == "update"]

    async def test_update_steps_replaces_and_resets_progress(self, monkeypatch):
        msg, calls = await self._call(
            monkeypatch,
            "updatePlanSteps",
            plan=_plan(status="pending"),
            plan_id=7,
            steps=[{"goal": "改 A"}, {"goal": "改 B"}],
        )

        assert "步骤已整份替换" in msg and "重置为 pending" in msg
        assert ("set_steps", 7, 2) in calls

    async def test_update_steps_is_refused_while_executing(self, monkeypatch):
        """执行中改步骤会让 coder 手上的计划与库里的对不上 —— 宁可拒绝。"""
        msg, calls = await self._call(
            monkeypatch,
            "updatePlanSteps",
            plan=_plan(status="executing"),
            plan_id=7,
            steps=[{"goal": "改 A"}],
        )

        assert "正在执行" in msg
        assert not [c for c in calls if c[0] == "set_steps"]

    async def test_update_steps_validates_before_touching_the_db(self):
        tool = next(t for t in build_plan_tools(None, _ctx()) if t.name == "updatePlanSteps")

        msg = await tool.ainvoke({"plan_id": 1, "steps": []})

        assert "步骤列表不合法" in msg

    async def test_get_plan_defaults_to_the_latest(self, monkeypatch):
        msg, calls = await self._call(monkeypatch, "getPlan", latest=_plan(content="最新正文"))

        assert "最新正文" in msg
        assert calls == [("get_latest", 1)]

    async def test_get_plan_by_id_can_read_a_deleted_one(self, monkeypatch):
        """软删之后记录还在：按 id 仍读得到（状态里看得见 closed）。"""
        msg, calls = await self._call(
            monkeypatch, "getPlan", plan=_plan(status="closed"), plan_id=7
        )

        assert "状态=closed" in msg
        assert calls == [("get_plan", 7, 1)]

    async def test_get_plan_without_any_plan_says_so(self, monkeypatch):
        msg, _ = await self._call(monkeypatch, "getPlan", latest=None)

        assert "还没有方案" in msg


def test_exports_all_eight_tools():
    names = [t.name for t in build_plan_tools(None, _ctx())]
    assert names == [
        "storePlan",
        "getPlan",
        "updatePlan",
        "updatePlanSteps",
        "deletePlan",
        "get_unexecuted_plan",
        "updatePlanStatus",
        "updatePlanStep",
    ]


def test_coder_only_writes_the_progress_not_the_plan():
    """进度回写留在 coder 手上（实施期间的实时进度靠它）；方案内容的改动不归它。"""
    repo = GiteeRepo(owner="owner", repo="repo", token="test-token")
    tools = build_subagent_tools(
        "coder",
        Settings(),
        web_search_tool=None,  # type: ignore[arg-type]
        fetch_url_tool=None,  # type: ignore[arg-type]
        repo=repo,
        database=object(),  # type: ignore[arg-type]  # 只用来通过 build_plan_tools 的真值判断
        plan_ctx=_ctx(),
    )
    assert [t.name for t in tools] == [
        "get_unexecuted_plan",
        "updatePlanStatus",
        "updatePlanStep",
        "create_pr",
    ]
