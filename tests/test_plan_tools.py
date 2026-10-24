"""plan 工具的纯逻辑单测：步骤规范化、渲染、入参校验、增删改查的行为、注册表。

写库那几条用一个假 dao 验**行为**（软删成 closed、执行中不许改步骤……），DB 层本身
（`jsonb_set` 那条原子 SQL）必须连真库单独验证，不在此文件内。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain_core.runnables.config import var_child_runnable_config

from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.config import Settings
from ai_coding_harness.tools import git as git_mod
from ai_coding_harness.tools import plan as plan_mod
from ai_coding_harness.tools.git import GiteeRepo, build_pr_tools
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


class _Db:
    @asynccontextmanager
    async def session(self):
        yield None


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


class TestContentChecks:
    """正文自检：坏正文不许落库。

    实测 2026-10-12（plan 41）：9,521 字的正文写到第 4 步（共 6 步）就断了、里面还带着
    「### 步骤 3（更正稿）：」这种自我修补片段 —— 它「合法」，于是原样落库，下游 coder
    拿到的就是一份坏方案。``steps`` 早就在校验，正文一个检查都没有。
    """

    @staticmethod
    def _steps(count: int = 6) -> list[dict]:
        return [{"index": i, "goal": f"第 {i} 步", "status": "pending"} for i in range(1, count + 1)]

    def test_a_clean_body_passes(self):
        body = "# 方案\n\n## 一、目标\n\n把登出接上黑名单。\n\n## 二、步骤\n\n" + "".join(
            f"### 步骤 {i}：改 {i} 处\n- 改哪个文件、怎么改、怎么验。\n\n" for i in range(1, 7)
        )
        body += "## 三、验收\n\n401 + A0403 命中，未命中放行。"

        assert plan_mod._check_content(body, self._steps()) is None

    def test_a_too_short_body_is_rejected(self):
        assert "太短" in plan_mod._check_content("就这样。", self._steps())

    def test_an_overlong_body_is_rejected(self):
        body = "甲" * (plan_mod.MAX_CONTENT_CHARS + 1)

        message = plan_mod._check_content(body, self._steps())

        assert "超过上限" in message and str(plan_mod.MAX_CONTENT_CHARS) in message

    def test_a_half_sentence_tail_is_rejected(self):
        """结尾是半句（真机那次是「…修改 `wanqi-m」+ 「（更正稿见正文）」）。"""
        body = "甲" * 200 + "\n\n- 结尾写着（"

        assert "半句" in plan_mod._check_content(body, self._steps())

    def test_self_patching_markers_are_rejected(self):
        body = "甲" * 200 + "\n\n### 步骤 3（更正稿）：\n\n### 歨骤 3 更正稿（以此为准）：\n" + "乙" * 100

        assert "自我修补" in plan_mod._check_content(body, self._steps())

    def test_a_body_that_stops_short_of_the_steps_is_rejected(self):
        """用了「步骤 N」小节标题，就得覆盖到 steps 的最后一步（那次断在第 4 步）。"""
        body = "# 方案\n\n" + "".join(
            f"### 步骤 {i}：改 {i} 处\n- 细节写得足够长。\n\n" for i in range(1, 5)
        )
        body += "甲" * 120

        assert "没写完" in plan_mod._check_content(body, self._steps())

    def test_prose_mentions_of_step_numbers_do_not_trigger_it(self):
        """正文里顺口提到「步骤 3」不算编号小节：这条判据只认**标题行**，免得不该拒的被拒。"""
        body = "# 方案\n\n正文里提到步骤 3 是拦截器那一步，但不写编号小节。\n" + "甲" * 200

        assert plan_mod._check_content(body, self._steps()) is None

    async def test_store_plan_refuses_a_bad_body_and_writes_nothing(self, monkeypatch):
        calls: list[tuple] = []

        async def fake_store(*args, **kwargs):
            calls.append(("store", kwargs.get("content")))
            return _plan(id=9)

        monkeypatch.setattr(plan_mod.dao, "store_plan", fake_store)
        tool = next(
            t
            for t in build_plan_tools(None, _ctx())  # database=None 就是在断言「不碰库」
            if t.name == "storePlan"
        )

        msg = await tool.ainvoke(
            {
                "content": "甲" * 200 + "（更正稿见正文）",
                "steps": [{"goal": f"第 {i} 步"} for i in range(1, 7)],
            }
        )

        assert "正文不合法" in msg and "方案未写入" in msg
        assert calls == []


class TestPlanExists:
    """``planExists``：按 id 判「还能不能拿去实施」，**只回一个布尔、不拉正文**。

    2026-10-13 用户口径：主控在派 coder 之前只要确认「这份方案还在、还没被执行」，
    方案正文是大对象（一份一万字），不该为了判存在性把它整个读回来。
    """

    class _Db:
        @asynccontextmanager
        async def session(self):
            yield None

    async def _call(self, monkeypatch, status: str | None, plan_id: int = 7):
        seen: dict = {}

        async def fake_status(db, *, session_id, plan_id):
            seen.update(session_id=session_id, plan_id=plan_id)
            return status

        async def no_full_read(*_a, **_kw):
            raise AssertionError("判存在性不该拉正文（那是大对象）")

        monkeypatch.setattr(plan_mod.dao, "plan_status", fake_status)
        monkeypatch.setattr(plan_mod.dao, "get_plan", no_full_read)
        tool = next(
            t for t in build_plan_tools(self._Db(), _ctx()) if t.name == "planExists"
        )
        return await tool.ainvoke({"plan_id": plan_id}), seen

    async def test_a_pending_plan_is_true(self, monkeypatch):
        assert await self._call(monkeypatch, "pending") == (True, {"session_id": 1, "plan_id": 7})

    async def test_a_closed_plan_is_false(self, monkeypatch):
        """软删（closed）＝ 用户已经把它删了，不能再拿去实施。"""
        result, _ = await self._call(monkeypatch, "closed")

        assert result is False

    async def test_a_done_plan_is_false(self, monkeypatch):
        """执行完的也不能再派 coder（那会重复实施）。"""
        result, _ = await self._call(monkeypatch, "done")

        assert result is False

    async def test_a_missing_plan_is_false(self, monkeypatch):
        """不存在 / 不属于本会话：``plan_status`` 返回 None。"""
        result, _ = await self._call(monkeypatch, None, plan_id=999)

        assert result is False


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
        body = "新" * 120  # 正文自检要求它像一份方案（≥100 字），占位短串会被拒
        msg, calls = await self._call(
            monkeypatch, "updatePlan", plan=_plan(), plan_id=7, content=body, title="新标题"
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
            monkeypatch,
            "updatePlan",
            plan=_plan(status="closed"),
            plan_id=7,
            content="新" * 120,
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


class TestRoundStateCapture:
    async def test_store_plan_writes_plan_id_to_turn_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_store(db, **kwargs):
            return _plan(id=9)

        monkeypatch.setattr(plan_mod.dao, "store_plan", fake_store)
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            tool = next(
                t for t in build_plan_tools(_Db(), _ctx()) if t.name == "storePlan"
            )
            await tool.ainvoke(
                {
                    "content": "甲" * 120,
                    "steps": [{"goal": "改 A"}],
                }
            )
        finally:
            var_child_runnable_config.reset(token)

        assert state.plan_id == 9

    async def test_get_unexecuted_plan_writes_plan_id_to_turn_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_get(db, *, session_id):
            return _plan(id=11, content="方案正文", steps=[])

        monkeypatch.setattr(plan_mod.dao, "get_unexecuted_plan", fake_get)
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            tool = next(
                t
                for t in build_plan_tools(_Db(), _ctx())
                if t.name == "get_unexecuted_plan"
            )
            await tool.ainvoke({})
        finally:
            var_child_runnable_config.reset(token)

        assert state.plan_id == 11

    async def test_create_pr_records_the_reused_number(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Client:
            async def find_open_pull(self, *, head, base):
                return {"number": 88, "html_url": "https://example/pr/88"}

        monkeypatch.setattr(git_mod, "build_git_client", lambda *_a, **_kw: _Client())
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            tool = next(
                t
                for t in build_pr_tools(
                    Settings(_env_file=None),
                    GiteeRepo(owner="owner", repo="repo", token="t"),
                )
                if t.name == "create_pr"
            )
            await tool.ainvoke(
                {"title": "t", "head": "feature/x", "base": "master", "body": "b"}
            )
        finally:
            var_child_runnable_config.reset(token)

        assert state.pr == 88

    async def test_create_pr_records_a_new_number(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Client:
            async def find_open_pull(self, *, head, base):
                return None

            async def create_pr(self, *, title, head, base, body):
                return {"number": 99, "html_url": "https://example/pr/99"}

        monkeypatch.setattr(git_mod, "build_git_client", lambda *_a, **_kw: _Client())
        state = TurnState()
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            tool = next(
                t
                for t in build_pr_tools(
                    Settings(_env_file=None),
                    GiteeRepo(owner="owner", repo="repo", token="t"),
                )
                if t.name == "create_pr"
            )
            await tool.ainvoke(
                {"title": "t", "head": "feature/x", "base": "master", "body": "b"}
            )
        finally:
            var_child_runnable_config.reset(token)

        assert state.pr == 99

    async def test_create_pr_registers_agent_pull_with_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Client:
            async def find_open_pull(self, *, head, base):
                return None

            async def create_pr(self, *, title, head, base, body):
                return {
                    "number": 100,
                    "title": title,
                    "state": "open",
                    "html_url": "https://example/pr/100",
                    "user": {"login": "agent-owner"},
                    "head": {"ref": head, "sha": "a" * 40},
                    "base": {"ref": base},
                }

        seen: dict = {}

        async def fake_upsert(db, **kwargs):
            seen.update(kwargs)
            return _plan(id=1)

        monkeypatch.setattr(git_mod, "build_git_client", lambda *_a, **_kw: _Client())
        monkeypatch.setattr(git_mod.dao, "upsert_pull_request", fake_upsert)
        state = TurnState(plan_id=57)
        token = var_child_runnable_config.set(
            {"configurable": {"turn_state": state}}
        )
        try:
            tool = next(
                t
                for t in build_pr_tools(
                    Settings(_env_file=None),
                    GiteeRepo(owner="owner", repo="repo", token="t"),
                    database=_Db(),
                    session_id=7,
                )
                if t.name == "create_pr"
            )
            await tool.ainvoke(
                {"title": "t", "head": "feature/x", "base": "master", "body": "b"}
            )
        finally:
            var_child_runnable_config.reset(token)

        assert seen["number"] == 100
        assert seen["plan_id"] == 57
        assert seen["session_id"] == 7
        assert seen["source"] == "agent"
        assert seen["mark_synced"] is True
        assert seen["head_sha"] == "a" * 40


def test_exports_all_nine_tools():
    names = [t.name for t in build_plan_tools(None, _ctx())]
    assert names == [
        "storePlan",
        "getPlan",
        "planExists",
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
        "list_pr_comments",
    ]
