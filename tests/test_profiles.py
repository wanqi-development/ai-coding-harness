"""意图 → 形态 → 工具 / 提示词的装配守卫。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_coding_harness.agent import profiles as profiles_module
from ai_coding_harness.agent.profiles import FORM_ALIASES
from ai_coding_harness.agent.prompts import (
    CODER_PROMPT,
    ORCHESTRATE_FORM,
    PLAN_FORM,
    PLANNER_PROMPT,
    QA_FORM,
    REVIEWER_PROMPT,
    build_main_prompt,
)
from ai_coding_harness.config import Settings
from ai_coding_harness.server.intent import TASK_TYPES
from ai_coding_harness.tools.git import GiteeRepo
from ai_coding_harness.tools.handoff import HandoffContext
from ai_coding_harness.tools.plan import PlanContext
from ai_coding_harness.tools.register import build_main_tools

REPO = GiteeRepo(owner="owner", repo="repo", token="test-token")


def _fake(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _main_tools(form: str) -> list[str]:
    handoff = HandoffContext(
        manager=SimpleNamespace(),  # type: ignore[arg-type]
        runtime=SimpleNamespace(sandbox=None, backend=None, agents={}),  # type: ignore[arg-type]
        session=SimpleNamespace(public_id="pub-1"),  # type: ignore[arg-type]
        settings=Settings(),
        form=form,
    )
    tools = build_main_tools(
        Settings(),
        _fake("web_search_tool"),  # type: ignore[arg-type]
        _fake("fetch_url_tool"),  # type: ignore[arg-type]
        REPO,
        SimpleNamespace(awrite=None),  # type: ignore[arg-type]
        form=form,
        database=object(),  # type: ignore[arg-type]
        plan_ctx=PlanContext(session_db_id=1, owner="owner", repo="repo"),
        handoff=handoff,
    )
    return [tool.name for tool in tools]


class TestIntentToProfile:
    def test_mapping_table(self) -> None:
        assert profiles_module.profile_for("qa").form == QA_FORM
        assert profiles_module.profile_for("plan").form == PLAN_FORM
        assert profiles_module.profile_for("coding").form == ORCHESTRATE_FORM
        assert profiles_module.profile_for("rework").form == ORCHESTRATE_FORM

    def test_rework_is_the_only_alias(self) -> None:
        assert FORM_ALIASES == {"rework": "coding"}
        assert profiles_module.profile_for("rework") is profiles_module.profile_for(
            "coding"
        )

    def test_only_coding_mounts_subagents(self) -> None:
        assert profiles_module.profile_for("qa").subagents == ()
        assert profiles_module.profile_for("plan").subagents == ()
        assert profiles_module.profile_for("coding").subagents == (
            "planner",
            "coder",
            "reviewer",
        )

    def test_every_task_type_is_profiled(self) -> None:
        assert set(TASK_TYPES) == profiles_module.PROFILED_INTENTS

    def test_unknown_falls_back_to_the_full_assembly(self) -> None:
        full = profiles_module.profile_for("coding")
        assert profiles_module.profile_for("unknown") is full


class TestMainTools:
    def test_qa_has_pr_reads_but_no_write_side(self) -> None:
        names = _main_tools(QA_FORM)

        assert "get_pr_info" in names
        assert "list_pr_comments" in names
        for name in (
            "save_repo_memory",
            "comment_pr",
            "close_pr",
            "create_pr",
            "storePlan",
            "task",
        ):
            assert name not in names

    def test_plan_owns_the_plan_lifecycle_but_not_memory(self) -> None:
        names = _main_tools(PLAN_FORM)

        for name in (
            "storePlan",
            "getPlan",
            "planExists",
            "updatePlan",
            "updatePlanSteps",
            "deletePlan",
        ):
            assert name in names
        assert "save_repo_memory" not in names
        assert "comment_pr" not in names
        assert "create_pr" not in names

    def test_orchestrate_has_the_full_crew_tools(self) -> None:
        names = _main_tools(ORCHESTRATE_FORM)

        assert "save_repo_memory" in names
        assert "comment_pr" in names
        assert "close_pr" in names
        assert "get_unexecuted_plan" in names
        assert "planExists" in names

    @pytest.mark.parametrize("form", [QA_FORM, PLAN_FORM, ORCHESTRATE_FORM])
    def test_every_form_can_hand_off(self, form: str) -> None:
        assert "handoffTask" in _main_tools(form)


class TestPrompts:
    def test_only_three_main_prompts_exist(self) -> None:
        assert set(profiles_module.PROFILED_INTENTS) == {
            "qa",
            "plan",
            "coding",
            "rework",
        }
        assert set(profiles_module._PROFILES) == {
            "qa",
            "plan",
            "coding",
            "rework",
        }

    def test_qa_merges_lookup_and_analysis(self) -> None:
        prompt = build_main_prompt("https://gitee.com/o/r.git", form=QA_FORM)

        assert "问答、解释与排查" in prompt
        assert "分析、解释与排查" in prompt
        assert "`/ai_coding/output/`：**本轮不用" in prompt

    def test_plan_never_writes_memory(self) -> None:
        prompt = build_main_prompt("https://gitee.com/o/r.git", form=PLAN_FORM)

        assert "`storePlan`" in prompt
        assert "不写仓库记忆" in prompt

    def test_orchestrate_reviews_and_fixes_through_the_same_crew(self) -> None:
        prompt = build_main_prompt("https://gitee.com/o/r.git")

        assert "派 `reviewer`" in prompt
        assert "派 `coder`" in prompt
        assert "写代码/修复PR问题一律派 `coder`" in prompt

    def test_coding_prompt_is_attached_per_turn(self) -> None:
        from ai_coding_harness.agent.prompts import intent_directive

        coding = intent_directive("coding")
        rework = intent_directive("rework")

        assert "本轮意图：coding" in coding
        assert "再派 `reviewer`" in coding
        assert "本轮意图：rework" in rework
        assert "必须再派一次 `reviewer` 复查" in rework

    def test_analysis_and_review_directives_are_gone(self) -> None:
        from ai_coding_harness.agent.prompts import intent_directive

        assert intent_directive("analysis") == ""
        assert intent_directive("review") == ""
        assert intent_directive("sync") == ""

    def test_subagent_prompts_remain_distinct(self) -> None:
        assert PLANNER_PROMPT != CODER_PROMPT
        assert CODER_PROMPT != REVIEWER_PROMPT
