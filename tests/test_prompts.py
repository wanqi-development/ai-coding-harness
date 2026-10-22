"""提示词行为契约：三种主控形态、三个子智能体和逐轮意图指令。"""

from __future__ import annotations

from ai_coding_harness.agent.prompts import (
    CODER_PROMPT,
    MAIN_AGENT_PROMPT,
    PLAN_AGENT_PROMPT,
    PLANNER_PROMPT,
    QA_AGENT_PROMPT,
    REVIEWER_PROMPT,
    intent_directive,
)


class TestQa:
    def test_it_merges_lookup_explanation_and_diagnosis(self) -> None:
        assert "问答、解释与排查" in QA_AGENT_PROMPT
        assert "分析、解释与排查" in QA_AGENT_PROMPT
        assert "code-tracing-analysis" in QA_AGENT_PROMPT

    def test_it_can_run_verification_but_cannot_change_code(self) -> None:
        assert "可以执行验证" in QA_AGENT_PROMPT
        assert "不许改任务代码" in QA_AGENT_PROMPT
        assert "不 `git add` / `git commit` / `git push`" in QA_AGENT_PROMPT

    def test_it_has_no_memory_write_role(self) -> None:
        assert "写回不归问答轮" in QA_AGENT_PROMPT
        assert "这一轮唯一的一处写入" not in QA_AGENT_PROMPT

    def test_requirement_discussion_belongs_to_this_form(self) -> None:
        assert "需求澄清" in QA_AGENT_PROMPT
        assert "requirement-clarifier" in QA_AGENT_PROMPT


class TestPlan:
    def test_plan_owns_discussion_design_and_plan_crud(self) -> None:
        assert "你自己出方案" in PLAN_AGENT_PROMPT
        assert "`storePlan`" in PLAN_AGENT_PROMPT

    def test_plan_never_writes_memory_or_implementation_files(self) -> None:
        assert "不写仓库记忆" in PLAN_AGENT_PROMPT
        assert "**本轮不落沙箱文件**" in PLAN_AGENT_PROMPT
        assert "只有方案那一处" in PLAN_AGENT_PROMPT

    def test_planner_and_plan_share_the_method(self) -> None:
        for marker in ("先看仓库记忆", "禁止通读仓库", "提交 PR"):
            assert marker in PLAN_AGENT_PROMPT
            assert marker in PLANNER_PROMPT


class TestCoding:
    def test_orchestrator_dispatches_instead_of_editing(self) -> None:
        assert "写代码/修复PR问题一律派 `coder`" in MAIN_AGENT_PROMPT
        assert "审查一律派 `reviewer`" in MAIN_AGENT_PROMPT

    def test_every_code_change_ends_with_one_review(self) -> None:
        coding = intent_directive("coding")
        rework = intent_directive("rework")

        assert "再派 `reviewer`" in coding
        assert "必须再派一次 `reviewer` 复查" in rework
        assert "不自动进入 coder ↔ reviewer 无限循环" in rework

    def test_rework_supports_review_only_and_change_modes(self) -> None:
        rework = intent_directive("rework")

        assert "只审查 / 只回评" in rework
        assert "要修改代码" in rework
        assert "严禁另开分支、另建 PR" in rework

    def test_memory_writeback_is_limited_to_code_changing_forms(self) -> None:
        assert "save_repo_memory" in intent_directive("coding")
        assert "save_repo_memory" in intent_directive("rework")
        assert intent_directive("qa") == ""
        assert "不写仓库记忆" in intent_directive("plan")

    def test_pr_merge_stays_manual(self) -> None:
        assert "PR 的合并由人工" in MAIN_AGENT_PROMPT or "合并由人工" in MAIN_AGENT_PROMPT


class TestReviewer:
    def test_reviewer_is_diff_first_and_read_only_for_code(self) -> None:
        assert "先看 diff" in REVIEWER_PROMPT
        assert "不改代码" in REVIEWER_PROMPT
        assert "comment_pr" in REVIEWER_PROMPT

    def test_reviewer_does_not_receive_memory(self) -> None:
        assert "本轮不注入仓库记忆" in REVIEWER_PROMPT


class TestCoder:
    def test_coder_gets_the_implementation_method(self) -> None:
        assert "ai-coding-implementation" in CODER_PROMPT
        assert "测试" in CODER_PROMPT
        assert "create_pr" in CODER_PROMPT

    def test_pr_fix_flow_uses_existing_branch(self) -> None:
        assert "工作流程 B：修复 PR" in CODER_PROMPT
        assert "不新建分支、不新建 PR" in CODER_PROMPT


class TestIntentDirectives:
    def test_only_current_intents_have_directives(self) -> None:
        assert intent_directive("analysis") == ""
        assert intent_directive("review") == ""
        assert intent_directive("sync") == ""
        assert intent_directive("qa") == ""
        assert intent_directive("plan")
        assert intent_directive("coding")
        assert intent_directive("rework")

    def test_orchestrator_has_no_duplicate_direct_handling_section(self) -> None:
        assert "### 职责二：直接处理" not in MAIN_AGENT_PROMPT
        assert "仓库代码类问答" not in MAIN_AGENT_PROMPT
        assert "通用问题（非代码相关）" not in MAIN_AGENT_PROMPT

    def test_orchestrator_points_misrouted_readonly_work_to_qa(self) -> None:
        assert "`handoffTask` 交给 `qa`" in MAIN_AGENT_PROMPT

    def test_memory_rules_are_split_between_stable_rules_and_turn_payload(self) -> None:
        coding = intent_directive("coding")

        assert "写回只在 coding / rework" in MAIN_AGENT_PROMPT
        assert 'section="change"' in coding
        assert 'section="gaps"' in coding
        assert "coding / review / analysis" not in coding


class TestCapabilityExit:
    def test_all_main_forms_point_to_handoff(self) -> None:
        for prompt in (QA_AGENT_PROMPT, PLAN_AGENT_PROMPT, MAIN_AGENT_PROMPT):
            assert "handoffTask" in prompt
            assert "先确认真的缺能力" in prompt


class TestReadDiscipline:
    def test_it_lives_in_one_section(self) -> None:
        for prompt in (QA_AGENT_PROMPT, PLAN_AGENT_PROMPT, MAIN_AGENT_PROMPT):
            assert "## 文件读取规则" not in prompt
            assert prompt.count("## 读码纪律（省 token）") == 1

    def test_it_allows_direct_paged_reads_for_known_files(self) -> None:
        assert "已知目标文件且没有合适检索词时" in MAIN_AGENT_PROMPT
        assert "直接从" in MAIN_AGENT_PROMPT
        assert "相关范围分段读" in MAIN_AGENT_PROMPT

    def test_it_blocks_scanning_a_file_through_multiple_offsets(self) -> None:
        assert "用不同 offset 从头扫到尾也算通读" in MAIN_AGENT_PROMPT

    def test_it_keeps_the_hard_per_call_limits(self) -> None:
        assert "`/ai_coding/policies` 必须显式给" in MAIN_AGENT_PROMPT
        assert "≤80 行" in MAIN_AGENT_PROMPT
        assert "`/ai_coding/project` 必须给 `limit` 且 ≤200 行" in MAIN_AGENT_PROMPT

    def test_it_requires_a_conclusion_after_reading(self) -> None:
        assert "读后立即留结论" in MAIN_AGENT_PROMPT
        assert "`<working-note>` / `KEEP:`" in MAIN_AGENT_PROMPT
