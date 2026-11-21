"""提示词行为契约：三种主控形态与三个子智能体。"""

from __future__ import annotations

from ai_coding_harness.agent.prompts import (
    CODER_PROMPT,
    MAIN_AGENT_PROMPT,
    PLAN_AGENT_PROMPT,
    PLANNER_PROMPT,
    QA_AGENT_PROMPT,
    REVIEWER_PROMPT,
    build_main_prompt,
)

CODING_PROMPT = build_main_prompt("https://gitee.com/o/r.git")


class TestQa:
    def test_it_merges_lookup_explanation_and_diagnosis(self) -> None:
        assert "问答、解释与排查" in QA_AGENT_PROMPT
        assert "分析、解释与排查" in QA_AGENT_PROMPT
        assert "code-tracing-analysis" in QA_AGENT_PROMPT

    def test_it_can_run_verification_but_cannot_change_code(self) -> None:
        assert "可以执行验证" in QA_AGENT_PROMPT
        assert "不许改任务代码" in QA_AGENT_PROMPT
        assert "`sync_repo_code`" in QA_AGENT_PROMPT
        assert "`git add`" in QA_AGENT_PROMPT
        assert "`git commit`" in QA_AGENT_PROMPT

    def test_it_can_write_verified_analysis_memory(self) -> None:
        assert "QA 可用文档级 `save_repo_memory`" in QA_AGENT_PROMPT
        assert "包括 `gaps` 和 `risks`" in QA_AGENT_PROMPT
        assert "不能写 `decision`" in QA_AGENT_PROMPT
        assert "写回不归问答轮" not in QA_AGENT_PROMPT
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
        for marker in ("先看仓库记忆", "禁止通读仓库", "本地审查与交付准备"):
            assert marker in PLAN_AGENT_PROMPT
            assert marker in PLANNER_PROMPT


class TestCoding:
    def test_orchestrator_dispatches_instead_of_editing(self) -> None:
        assert "写代码/修复PR问题一律派 `coder`" in MAIN_AGENT_PROMPT
        assert "涉及代码的审查一律派 `reviewer`" in MAIN_AGENT_PROMPT

    def test_pure_docs_skip_planner_but_still_get_a_local_review(self) -> None:
        assert "纯文档直做" in CODING_PROMPT
        assert "直接派 `coder`" in CODING_PROMPT
        assert "不派 `planner`" in CODING_PROMPT
        assert "派 `reviewer`" in CODING_PROMPT
        assert "PR 仍在最后按用户授权执行" in CODING_PROMPT

    def test_code_task_and_submissions_are_durable(self) -> None:
        assert "openWorkTask" in CODING_PROMPT
        assert "recordTaskSubmission" in CODING_PROMPT
        assert "合并本次和此前提交的内容" in CODING_PROMPT

    def test_review_is_advisory_and_pr_is_the_last_step(self) -> None:
        assert "reviewer 报告只作为建议" in CODING_PROMPT
        assert "不再作为创建 PR 的硬门禁" in CODING_PROMPT
        assert "PR 最后创建" in CODING_PROMPT
        assert "不另做意图分类" in CODING_PROMPT
        assert "不要依赖固定口令" in CODING_PROMPT

    def test_memory_writeback_is_limited_to_code_changing_forms(self) -> None:
        assert "save_repo_memory" in CODING_PROMPT
        assert "不写仓库记忆" in PLAN_AGENT_PROMPT

    def test_pr_merge_stays_manual(self) -> None:
        assert "PR 的合并由人工" in MAIN_AGENT_PROMPT or "合并由人工" in MAIN_AGENT_PROMPT


class TestReviewer:
    def test_reviewer_is_diff_first_and_read_only_for_code(self) -> None:
        assert "先看 diff" in REVIEWER_PROMPT
        assert "不改代码" in REVIEWER_PROMPT
        assert "comment_pr" in REVIEWER_PROMPT
        assert "save_review" in REVIEWER_PROMPT
        assert "`pr`" in REVIEWER_PROMPT

    def test_reviewer_does_not_receive_memory(self) -> None:
        assert "本轮不注入仓库记忆" in REVIEWER_PROMPT


class TestQA:
    def test_qa_can_update_code_checkout_and_write_analysis_memory(self) -> None:
        assert "sync_repo_code" in QA_AGENT_PROMPT
        assert "QA 可用文档级 `save_repo_memory`" in QA_AGENT_PROMPT
        assert "写回不归问答轮" not in QA_AGENT_PROMPT


class TestCoder:
    def test_coder_gets_the_implementation_method(self) -> None:
        assert "ai-coding-implementation" in CODER_PROMPT
        assert "测试" in CODER_PROMPT
        assert "不创建 PR" in CODER_PROMPT
        assert "`create_pr` 不在你的工具里" in CODER_PROMPT
        assert "commit + push" in CODER_PROMPT
        assert "task_id" in CODER_PROMPT

    def test_pr_fix_flow_uses_existing_branch(self) -> None:
        assert "工作流程 B：修复已有 PR" in CODER_PROMPT
        assert "不新建分支、不新建 PR" in CODER_PROMPT

    def test_pure_docs_have_a_direct_workflow(self) -> None:
        assert "工作流程 C：纯文档直做" in CODER_PROMPT
        assert "不调用 `get_unexecuted_plan`" in CODER_PROMPT
        assert "不得修改源码、测试、配置、依赖或构建文件" in CODER_PROMPT
        assert "`docs/<主题>` 分支" in CODER_PROMPT


class TestIntentDirectives:
    def test_no_dynamic_intent_label_is_added_to_the_static_prompt(self) -> None:
        for prompt in (QA_AGENT_PROMPT, PLAN_AGENT_PROMPT, CODING_PROMPT):
            assert "本轮意图：" not in prompt

    def test_orchestrator_has_no_duplicate_direct_handling_section(self) -> None:
        assert "### 职责二：直接处理" not in MAIN_AGENT_PROMPT
        assert "仓库代码类问答" not in MAIN_AGENT_PROMPT
        assert "通用问题（非代码相关）" not in MAIN_AGENT_PROMPT

    def test_orchestrator_points_misrouted_readonly_work_to_qa(self) -> None:
        assert "`handoffTask` 交给 `qa`" in MAIN_AGENT_PROMPT

    def test_memory_rules_are_split_between_stable_rules_and_turn_payload(self) -> None:
        assert "save_repo_memory(kind=...)" in CODING_PROMPT
        assert 'kind="gaps"' in CODING_PROMPT
        assert 'kind="risks"' in CODING_PROMPT
        assert 'kind="feature"' in CODING_PROMPT


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
