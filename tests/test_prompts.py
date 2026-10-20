"""提示词硬约束的守卫测试。

这些字符串就是智能体的行为契约，改它们等于改行为。本文件把几条明确写死的
规则钉住，防止后续编辑悄悄回退（本轮就清掉了一个指向不存在工具的引用）。
"""

from __future__ import annotations

import inspect

from ai_coding_harness.agent.prompts import (
    _MAIN_PROMPTS,
    ANALYSIS_AGENT_PROMPT,
    BASE_PROMPT,
    CODER_PROMPT,
    MAIN_AGENT_PROMPT,
    PLAN_AGENT_PROMPT,
    PLANNER_PROMPT,
    QA_AGENT_PROMPT,
    REVIEW_AGENT_PROMPT,
    REVIEWER_PROMPT,
    TODO_PROMPT,
    intent_directive,
)
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.tools import memory as memory_tools
from ai_coding_harness.tools.plan import PlanContext, build_plan_tools


class TestPlanEndsWithPr:
    def test_planner_requires_pr_as_last_step(self):
        assert "最后一步必须是「提交 PR」" in PLANNER_PROMPT

    def test_planner_still_does_not_open_pr_itself(self):
        """计划里写这一步，但 planner 自己不执行 —— 两者不冲突，都要在。"""
        assert "不亲自提 PR" in PLANNER_PROMPT


class TestPlannerReadsOnDemand:
    """planner 曾把「浏览仓库代码」照字面执行，一轮出方案读掉了大半个仓库。"""

    def test_memory_is_the_primary_source(self):
        assert "是了解现状的主要依据" in PLANNER_PROMPT

    def test_browsing_the_whole_repo_is_banned(self):
        assert "禁止通读仓库" in PLANNER_PROMPT
        # 旧措辞无边界，模型据此全仓通读
        assert "浏览 `/ai_coding/project/<repo>` 的代码" not in PLANNER_PROMPT

    def test_extra_reads_must_be_located_by_grep_first(self):
        assert "只读命中的文件" in PLANNER_PROMPT

    def test_bootstrap_skill_runs_only_without_memory(self):
        assert "repo-bootstrap-analysis" in PLANNER_PROMPT
        assert "不要重跑该 skill" in PLANNER_PROMPT


class TestPlannerMinimalChange:
    """方案要落在「能实现且改动面最小」上，不顺手重构、不扩需求。"""

    def test_constraint_is_declared(self):
        assert "**最小改动**" in PLANNER_PROMPT

    def test_refactoring_along_the_way_is_banned(self):
        assert "不顺手重构" in PLANNER_PROMPT

    def test_widening_the_change_requires_a_reason(self):
        """允许扩大改动面，但必须在方案里交代理由与代价。"""
        assert "写明理由与代价" in PLANNER_PROMPT

    def test_steps_must_be_minimal_too(self):
        assert "最小改动方案" in PLANNER_PROMPT


class TestPlanGoalIsALabel:
    """``goal`` 直接显示在用户的进度面板上，不是给 coder 的说明书。

    实测一份 8 步的方案：goal 135–332 字，塞着类名、注解、依赖坐标与 git 命令，面板
    一行根本读不完。细节的归宿是方案全文（``content``）与 ``done_criteria`` —— coder 用
    ``get_unexecuted_plan`` 取方案时两者都拿得到，不靠 goal。
    """

    def test_prompt_caps_the_goal_length(self):
        assert "一行短句、约 15 字以内" in PLANNER_PROMPT

    def test_prompt_says_the_panel_is_the_reader(self):
        assert "原样显示在用户的进度面板上" in PLANNER_PROMPT
        assert "给用户看的一行标签" in PLANNER_PROMPT

    def test_detail_is_routed_to_content_and_done_criteria(self):
        assert "不要塞进 goal" in PLANNER_PROMPT

    def test_the_tool_description_says_the_same(self):
        """工具描述离模型更近，两处口径必须一致，否则模型只听得到一半。"""
        tools = build_plan_tools(None, PlanContext(session_db_id=1, owner="owner", repo="repo"))
        desc = next(t for t in tools if t.name == "storePlan").description
        assert "一行短句" in desc
        assert "不要写进 goal" in desc


class TestMemoryPathShape:
    """记忆路径只有一个口径：``backends.composite.memory_path``。

    读写两侧曾各拼一份（工具层下划线、server 层斜杠），口径一旦不一致，写入成功但
    没人读得到 —— 等于内容丢了。这里钉住「提示词写的路径 == 那个函数算出来的路径」，
    外加「工具层不许自己拼」。（server 层那份随 ``inspect`` 直接执行一起删掉了，读侧
    现在只有 ``read_file`` + 提示词里的这个路径。）
    """

    def test_prompt_path_matches_the_shared_helper(self):
        expected = f"`{memory_path('${owner}', '${repo_name}')}`"
        assert expected in BASE_PROMPT
        assert expected in PLANNER_PROMPT
        assert expected in CODER_PROMPT

    def test_old_underscore_form_is_gone(self):
        assert "/memories/${owner}_${repo_name}.md" not in BASE_PROMPT

    def test_the_tools_layer_uses_the_shared_helper(self):
        source = inspect.getsource(memory_tools)
        assert "memory_path(" in source
        assert "_MEMORY_PREFIX" not in source


class TestPlannerDoesNotTouchCode:
    """planner 曾在 /ai_coding/tmp 复制仓库、把需求完整实现一遍来自证可行。

    实测两轮 823s / 668s，一半以上开销花在搭一次性原型、起服务复现行为上。
    可行性验证是 coder 的活：planner 只把拿不准的地方写成「不确定项」。
    """

    def test_plan_phase_is_read_only(self):
        assert "出方案阶段零动手" in PLANNER_PROMPT
        assert "不写源码 / 测试 / 配置" in PLANNER_PROMPT

    def test_no_build_run_or_test_in_plan_phase(self):
        assert "不构建、不启动服务、不跑测试" in PLANNER_PROMPT

    def test_uncertainty_goes_to_the_plan_not_an_experiment(self):
        assert "不确定项交 coder" in PLANNER_PROMPT
        assert "风险与不确定项" in PLANNER_PROMPT

    def test_the_minimal_verification_escape_hatch_is_gone(self):
        # 旧措辞留了「最多做一次最小验证」的口子，模型据此把需求实现了一遍
        assert "最多做一次最小验证" not in PLANNER_PROMPT


class TestMainAgentStaysOutOfTheLab:
    """主智能体也搭过原型：复制仓库到 /ai_coding/tmp/vfy/r 并写探针控制器复现结论。"""

    def test_main_does_not_prototype(self):
        assert "不自己动手验证" in MAIN_AGENT_PROMPT
        assert "不复制仓库到临时目录做实验" in MAIN_AGENT_PROMPT

    def test_findings_go_to_the_user_or_the_coder(self):
        assert "不要自己下场复现一遍" in MAIN_AGENT_PROMPT
        assert "交由 `coder` 在实施时验证" in MAIN_AGENT_PROMPT


class TestMainPlansBeforeItDelegates:
    """实测一轮 coding：主智能体读记忆 → 查 PR → 直接派 coder，一次 ``write_todos``
    都没调，子智能体那份清单因此没有落点、整份显示不出来。这段是补的前置约束
    （与「任务清单规则」第 1 条刻意重复：那条单独放着的时候被无视过）。

    2026-10-19 起还有第二道：``DelegationGateMiddleware`` 在执行前硬拦。提示词这段仍要留，
    模型才知道被拒之后该补什么。
    """

    def test_main_plans_before_it_acts(self):
        assert "先看清这一轮要做什么" in MAIN_AGENT_PROMPT
        assert "绝不允许一次`write_todos`都没调用就调用`task`工具" in MAIN_AGENT_PROMPT

    def test_only_the_multi_agent_form_gets_it(self):
        # 单智能体形态（qa 等）不挂子智能体，这条对它们没有意义
        assert "绝不允许一次`write_todos`都没调用就调用`task`工具" not in QA_AGENT_PROMPT


class TestThePlanIsNotCopiedAround:
    """方案正文只该在库里那一份（``storePlan`` 的 ``content``）。

    实测一轮（2026-10-12，阿里云短信）：主控把方案全文抄进 coder 的任务描述，而 coder
    又自己 ``get_unexecuted_plan`` 取了一遍 —— 同一份方案在 coder 的上下文里躺着两份
    （6,270 字那份 + 库里 10,915 字那份），之后 79 次调用每次重发：**光这一项 ≈90 万
    input tokens**，还顺带埋了「两个版本谁为准」的隐患。
    """

    def test_the_planner_reports_a_summary_not_the_whole_plan(self):
        assert "回报 / 答复只写摘要，不抄方案全文" in PLANNER_PROMPT
        assert "不贴全文，也不贴步骤原文" in PLANNER_PROMPT

    def test_the_main_does_not_paste_the_plan_into_the_task(self):
        assert "不要抄方案全文" in MAIN_AGENT_PROMPT
        assert "不要搬运长文" in MAIN_AGENT_PROMPT
        assert "不贴方案全文" in intent_directive("coding")

    def test_the_coder_knows_the_plan_has_exactly_one_source(self):
        assert "方案正文只有这一个来源" in CODER_PROMPT

    def test_the_plan_form_also_answers_with_a_summary(self):
        # plan 形态是主控直接把方案写给用户：同样只给摘要，全文由「方案」链接承载
        assert "回报 / 答复只写摘要，不抄方案全文" in PLAN_AGENT_PROMPT
        assert "再把**摘要**呈给用户" in PLAN_AGENT_PROMPT


class TestReadAndBuildDiscipline:
    """2026-10-20 的 token 账：52 个文件整读 + 54 次 edit + 131 次调用 = 1519 万输入。

    读法（先定位、读片段、不重读）和构建节奏（批量验证）都写进基础/编码提示词，
    否则模型只会照着「逐条修」的流程一路重读、一路全量构建。
    """

    def test_locate_before_reading_slices(self):
        assert "先 `grep` 定位" in BASE_PROMPT
        assert "`offset` / `limit`" in BASE_PROMPT
        assert "不要整文件通读" in BASE_PROMPT

    def test_the_hard_limits_are_stated(self):
        assert "必须显式给 `limit` 且 ≤80 行" in BASE_PROMPT
        assert "`/ai_coding/project` ≤200 行" in BASE_PROMPT
        assert "`cat` / `less` / `more` / `nl` / `tac`" in BASE_PROMPT

    def test_coder_batches_verification(self):
        assert "批量验证" in CODER_PROMPT
        assert "不要每改一行就跑一次全量" in CODER_PROMPT

    def test_rework_delegation_does_not_paste_the_review(self):
        directive = intent_directive("rework")
        assert "不要整段粘贴审查原文" in directive
        assert "list_pr_comments" in directive
        assert "list_pr_comments" in CODER_PROMPT, "coder 要能自己拉审查原文"


class TestReviewIsDiffDriven:
    """reviewer 49 个文件 / 58 次 read 的教训：diff hunk 是主材料，read_file 只补上下文。"""

    def test_it_reads_the_diff_first(self):
        assert "先看 diff，不先读文件" in REVIEWER_PROMPT
        assert "改动 hunk" in REVIEWER_PROMPT
        assert "同一文件同一范围本轮不重读" in REVIEWER_PROMPT

    def test_it_does_not_rescan_a_file_with_offsets(self):
        assert "用不同 offset 把同一文件从头扫到尾也算重读" in REVIEWER_PROMPT

    def test_large_reviews_close_per_module(self):
        assert "大 PR 分模块收口" in REVIEWER_PROMPT
        assert "报告开头给一张覆盖清单" in REVIEWER_PROMPT
        assert "覆盖率不等于读完所有文件" in REVIEWER_PROMPT

    def test_the_review_form_shares_the_method(self):
        assert "先看 diff，不先读文件" in REVIEW_AGENT_PROMPT


class TestReworkGroupsByModule:
    """2026-10-20：9 条问题按 P0/P1 逐条修，206 次调用 / 1700 万输入。

    改成先确认问题、按模块分组、按模块改与测，避免按级别逐条穿插。
    """

    def test_issues_are_grouped_by_module_not_severity(self):
        assert "按模块分组，不按级别" in CODER_PROMPT
        assert "不按 P0/P1 排序" in CODER_PROMPT
        assert "一项对应一个模块" in CODER_PROMPT

    def test_each_module_is_tested_before_moving_on(self):
        assert "同一模块的几条问题一起改完" in CODER_PROMPT
        assert "全部模块改完再做一次全量" in CODER_PROMPT


class TestReasoningRules:
    """推理纪律在基础提示词里，所有形态共享（不只 coder）。"""

    def test_the_rule_is_in_the_shared_head(self):
        assert "## 推理规则" in BASE_PROMPT
        assert "问题 | 影响面 | 处置(答/改/记/上报)" in BASE_PROMPT
        assert "先过影响面闸门" in BASE_PROMPT
        assert "同一个点连续两次没有新证据" in BASE_PROMPT

    def test_unverifiable_issues_are_capped(self):
        assert "最多一次检索 + 一次查源码" in BASE_PROMPT
        assert "禁止为不可验证问题设计替代实现" in BASE_PROMPT
        assert "安全、数据丢失、对外 API / 数据契约" in BASE_PROMPT

    def test_the_impact_gate_decides_the_depth(self):
        assert "低后果 → 记一行残余风险" in BASE_PROMPT
        assert "高后果 → 停下上报" in BASE_PROMPT

    def test_the_residual_risk_template_is_ready_to_use(self):
        assert "残余风险：<问题>；影响：<失败关闭/可回滚/需人工确认>" in BASE_PROMPT

    def test_readonly_forms_carry_it_too(self):
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT, PLANNER_PROMPT):
            assert "## 推理规则" in prompt

    def test_reasoning_must_land_in_an_artifact(self):
        assert "写进答复 / 改代码 / 加测试 / 写风险记录 / 上报" in BASE_PROMPT

    def test_coder_has_two_workflows(self):
        assert "## 工作流程 A：按方案实施（有 plan_id）" in CODER_PROMPT
        assert "## 工作流程 B：修复 PR 审查问题（rework）" in CODER_PROMPT
        assert "核验，不设计修法" in CODER_PROMPT
        assert "核验完成前不改代码" in CODER_PROMPT
        assert "覆盖清单" in CODER_PROMPT


class TestThePlanBodyStaysShortAndClean:
    """正文限长 + 写干净：一次超长生成会写出半成品。

    实测 2026-10-12（plan 41）：一次调用要吐 9.5k 字正文（输入已 54.7k），模型在里面自我
    修补（同一节写两遍、带「更正稿」字样）、写到第 4 步（共 6 步）就断了。现在提示词给了
    字数预算，工具那边也按同一个数硬拒（`MAX_CONTENT_CHARS`）。
    """

    def test_the_prompt_sets_a_budget(self):
        assert "4000 字以内" in PLANNER_PROMPT
        assert "正文精简（强要求）" in PLANNER_PROMPT
        assert "不要在文档里追加「更正稿」" in PLANNER_PROMPT

    def test_the_hard_cap_in_the_prompt_is_the_tools_cap(self):
        """提示词里那个硬上限必须就是工具里的常量 —— 两处对不上，模型按提示词写也会被拒。"""
        from ai_coding_harness.tools.plan import MAX_CONTENT_CHARS

        assert str(MAX_CONTENT_CHARS) in PLANNER_PROMPT

    def test_what_belongs_in_the_body_is_spelled_out(self):
        assert "不该写" in PLANNER_PROMPT
        assert "coder 自己会读码" in PLANNER_PROMPT


class TestTodosComeAfterTheTaskIsUnderstood:
    """``write_todos`` 曾被当成开场动作：一上来先摆一份清单，摆出来的是猜的。

    这份清单同时是给用户看的进度（界面上就这一份），条目得是他话里的对象。
    2026-10-19 起 ``TODO_PROMPT`` 收敛成三条（先看清再落清单 / 推进状态 / 收尾），
    qa 与 analysis 两个只读形态都摘掉它，其余形态照旧拼上去。
    """

    def test_the_rule_orders_analysis_before_the_list(self):
        assert "先看清这一轮要做什么" in TODO_PROMPT
        assert "判断请求类型、读清需求之后才调 `write_todos`" in TODO_PROMPT

    def test_the_rule_says_the_list_is_for_the_user(self):
        assert "任务清单要贴合用户这一轮的意图，也贴合他的说法" in TODO_PROMPT
        assert "用户看到的清单以最后一次调用为准" in TODO_PROMPT

    def test_the_write_it_first_wording_is_gone(self):
        assert "每个任务都必须先使用 `write_todos` 工具生成" not in BASE_PROMPT

    def test_orchestrator_analyzes_then_writes_the_list(self):
        assert "判断请求类型、读清需求之后才调 `write_todos`" in MAIN_AGENT_PROMPT
        assert "绝不允许一次`write_todos`都没调用就调用`task`工具" in MAIN_AGENT_PROMPT
        assert "先自己用 `write_todos` 写出本轮清单再动手" not in MAIN_AGENT_PROMPT


class TestTheListCarriesThePlanSteps:
    """coder 执行方案时的清单口径：**一项对应方案的一步**，条目用他自己的话写。

    这条口径改过三版。第一版只有「不要照抄方案的步骤」，单放着塌成一条「执行计划中」的
    占位；第二版补上粒度、删了措辞那条，实测又滑到整段照抄；第三版（上一版）两条并存，
    却变成对同一份文本的相反要求 —— 「把方案的每一步各列一条」的示例「执行方案步骤 2：
    改密钥存储」本身就是一次复述，而「清单只写你自己的动作 / 不复述方案」是二值禁令，
    模型只能二选一（`scripts/check_prompts.py` 的 C1 扫的就是这对）。

    现在这条口径只留在 ``CODER_PROMPT``（``TODO_PROMPT`` 是八个形态共用的三条通用规则，
    不替 coder 管方案步骤）：粒度（一步一条、不许塌成占位）与措辞（用你自己的话、
    不搬方案原文）写在同一句里，示例对两条都成立。
    """

    def test_coder_maps_one_item_to_one_plan_step(self):
        assert "一项对应方案的一步" in CODER_PROMPT
        assert "如「步骤 2：改密钥存储」" in CODER_PROMPT

    def test_the_one_liner_placeholder_is_named_as_the_anti_pattern(self):
        assert "不要只留一条「执行计划中」这样的占位" in CODER_PROMPT

    def test_the_rule_still_forbids_copying_the_plan_text(self):
        """`不加这条就会整段照抄` —— 它 2026-09-21 被误删过一次，这次钉住。"""
        assert "条目用你自己的话写这一步在动什么" in CODER_PROMPT
        assert "方案原文与每一步的状态在方案面板上" in CODER_PROMPT
        assert "同一步的方案原文不必再搬一遍" in CODER_PROMPT

    def test_the_two_halves_are_not_two_opposite_orders(self):
        """相反要求不许回来：既说「一步一条」又说「只写你自己的动作 / 不复述方案」。"""
        assert "一项对应方案的一步" in CODER_PROMPT
        assert "清单只写你自己的动作" not in CODER_PROMPT
        assert "不复述方案" not in CODER_PROMPT

    def test_coder_says_the_same(self):
        """与通用规则刻意重复：单放在通用规则里的话被无视过。"""
        assert "一项对应方案的一步" in CODER_PROMPT
        assert "「执行计划中」" in CODER_PROMPT
        assert "清单与方案的分工" in CODER_PROMPT
        assert "用你自己的话写这一步在动什么" in CODER_PROMPT

    def test_coder_writes_the_list_right_after_fetching_the_plan(self):
        """流程位置是用户指定的：第 2 步取计划之后、动手之前就把清单落下来。"""
        assert "3. **落清单**：取到方案后立刻调 `write_todos`" in CODER_PROMPT


class TestQaHasNoTodoList:
    """qa 与 analysis 都不需要 `write_todos`：一轮问答没有多步进度可报；analysis 的进度
    由 `code-tracing-analysis` skill 的取证台账承载，再挂一份清单只是同一件事记两遍。

    两条一起留：提示词里那段摘掉（劝它别选），工具本身也从两个形态的边界里撤掉
    （`safety/policy.py`，见 `test_boundary.py`）—— 后者是真边界。
    """

    def test_qa_prompt_never_mentions_the_tool(self):
        assert "write_todos" not in QA_AGENT_PROMPT
        assert "任务清单规则" not in QA_AGENT_PROMPT

    def test_analysis_also_drops_the_list(self):
        """2026-10-17 口径：analysis 的进度靠取证台账，不再挂清单。"""
        assert "write_todos" not in ANALYSIS_AGENT_PROMPT
        assert "任务清单规则" not in ANALYSIS_AGENT_PROMPT

    def test_every_other_form_keeps_it(self):
        for prompt in (
            MAIN_AGENT_PROMPT,
            PLAN_AGENT_PROMPT,
            REVIEW_AGENT_PROMPT,
            PLANNER_PROMPT,
            CODER_PROMPT,
            REVIEWER_PROMPT,
        ):
            assert "任务清单规则" in prompt


class TestFindingsSurviveTheTrim:
    """读过的东西会被裁掉，所以结论必须落在**自己的话**里 —— 这是提示词那一半。

    2026-10-16 那轮 analysis 触顶（`recursion_limit=200`）的账：126 次工具调用只留得住最后
    几条原文，模型回头要用早先读过的文件时满眼是占位符，于是重读 —— 同一个文件读 8 次、
    同一批文件用 `execute` 各 dump 6~7 次，步数全烧在重读上。代码那一半是「占位符带身份、
    不再劝重读」（`context_trim.trimmed_placeholder`）与当时把保留窗口从 6 抬到 12
    （该配置 2026-10-17 已作废）；三件是一套，所以在这儿一起钉住。
    """

    def test_a_placeholder_means_it_already_read_that(self):
        """要明说「占位符＝你读过」，否则模型只当「这里没有信息」，转头再去读一遍。"""
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT):
            assert "读过就别重读" in prompt
            assert "不等于你没读过" in prompt

    def test_nothing_invites_a_reread_any_more(self):
        """「需要时重新读一次」那句就是重读的诱因，两份只读提示词里都不能再出现。"""
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT):
            assert "重新读一次" not in prompt

    def test_conclusions_go_into_its_own_words(self):
        """工具返回会被裁、AI 消息不会 —— 这条机制得讲给它听，它才知道结论该往哪儿放。"""
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT):
            assert "工具返回会被裁掉，你写出来的话不会" in prompt

    def test_analysis_concludes_as_it_goes(self):
        """分析工作法第 3 步：边取证边下结论，不许攒到最后一次性下。"""
        assert "边取证边下结论，不要攒到最后" in ANALYSIS_AGENT_PROMPT
        assert "串成整体" in ANALYSIS_AGENT_PROMPT, "收尾是串结论，不是重读一遍材料"

    def test_the_paged_reread_anti_pattern_is_named(self):
        """那轮的重读全是「同一文件、不同 offset/limit」—— 空转刹车认不出来，只能在这儿点名。"""
        assert "反复翻同一个文件" in ANALYSIS_AGENT_PROMPT
        assert "再 `cat` 一遍" in ANALYSIS_AGENT_PROMPT

    def test_the_discipline_stays_in_the_readonly_forms(self):
        """这两条挂在只读形态的检索纪律里，别顺手漂到写码那几份去。"""
        for prompt in (CODER_PROMPT, PLANNER_PROMPT, REVIEWER_PROMPT):
            assert "读过就别重读" not in prompt


class TestExistingFeatureIsAConflict:
    """需求对应的能力已存在时，planner 出过一份「换条路径」的变体方案。

    那正是「需求与现状冲突」该停下来问用户的场景，不能自行改换口径继续。
    """

    def test_existing_feature_is_a_conflict(self):
        assert "功能已存在" in PLANNER_PROMPT
        assert "等价能力" in PLANNER_PROMPT

    def test_switching_to_another_approach_is_banned(self):
        assert "不许换一种实现方式绕过去" in PLANNER_PROMPT


class TestUserDecidesWhatToFix:
    def test_main_stops_and_waits_for_user(self):
        assert "停下等用户指示" in MAIN_AGENT_PROMPT
        assert "不自动回环" in MAIN_AGENT_PROMPT

    def test_coder_no_longer_converges_on_severity_alone(self):
        assert "直到没有「严重」级别的问题为止" not in CODER_PROMPT
        assert "用户指定的问题全部解决" in CODER_PROMPT

    def test_coder_is_told_not_to_comment_on_pr(self):
        assert "你不要调用 `comment_pr`" in CODER_PROMPT


class TestReviewReply:
    def test_both_reply_shapes_are_documented(self):
        assert "用户判定**不影响**的问题" in MAIN_AGENT_PROMPT
        assert "「已解决 ……」" in MAIN_AGENT_PROMPT

    def test_reply_must_quote_source_not_memory(self):
        """回评要引 PR 上的原文：这句原来还在共用段里（八个智能体都收到「取回审查原文」），
        2026-09-25 挪进能回评的两个形态 —— 共用段只留通用话。"""
        assert "不得凭记忆复述" in MAIN_AGENT_PROMPT
        assert "不得凭记忆复述" in REVIEW_AGENT_PROMPT
        assert "不得凭记忆复述" not in BASE_PROMPT

    def test_reviewer_hands_back_full_finding_list(self):
        assert "完整问题清单" in REVIEWER_PROMPT


class TestMemoryWritebackGate:
    """记忆回写：谁写、写什么、什么时候写，都是行为契约。

    写入收敛到主智能体一个入口（``save_repo_memory``），时机是每轮 plan / coding 开始、
    出方案或派发子智能体之前 —— 这样方案读到的是本轮刚刷新的版本，planner / coder
    读到的也是。

    「自己查未合并 PR、有就停下等合并」**已从提示词挪进闸门**
    （``middleware/memory.py``，在 ``before_agent`` 就拦下本轮）：留在提示词里等于
    让模型把闸门白做一遍，还留着「把查不到讲成没有」的口子。指令现在只需说清
    「这件事平台做了」，并交代需求表哪几列不归模型管。

    记忆全文由同一个中间件注入系统提示（**每个智能体、每个形态**都有），所以提示词里
    「怎么拿到记忆」的口径必须跟着改：现在是「直接用「仓库记忆」块」，而不是
    「用 `read_file` 读一遍」。
    """

    def test_every_round_that_lands_facts_carries_the_writeback(self):
        """记忆正文只在**真的落地了事实**的轮次写：coding / rework（都改了代码）与
        review（出了审查结论），且都在**收尾时**（2026-09-25 定；rework 2026-09-26 加入）。

        analysis 2026-09-26 也加入：它的分析结论（仓库基本信息 + 当前缺口）就是记忆的正文来源，
        不写回等于这一轮白分析 —— 所以它是**唯一带记忆写回的只读形态**。
        """
        for intent in ("coding", "review", "rework", "analysis"):
            directive = intent_directive(intent)
            assert "save_repo_memory" in directive, intent
            assert "收尾时写回仓库记忆" in directive, intent

    def test_the_plan_round_does_not_write_memory(self):
        """出方案这一轮还没动代码：写进去的是「打算做的事」，不是事实。"""
        directive = intent_directive("plan")
        assert "save_repo_memory" not in directive
        assert "不写仓库记忆" in directive

    def test_the_writeback_is_the_last_thing(self):
        """从「开工前第 0 步」改成收尾：先干活、再沉淀 —— 顺序不能再倒过来。"""
        for intent in ("coding", "review", "rework", "analysis"):
            directive = intent_directive(intent)
            assert "本轮的最后一件事" in directive, intent
            assert "第 0 步" not in directive, intent

    def test_coding_writes_memory_after_dispatch(self):
        """coding 轮里那段收尾指令必须排在派发说明**之后**。"""
        directive = intent_directive("coding")
        assert directive.index("派 `coder`") < directive.index("收尾时写回仓库记忆")

    def test_the_model_is_told_the_platform_already_checked_prs(self):
        """不说清就会出现两种坏结果：又自己查一遍（白费一轮），
        或者张口断言「仓库没有未合并的 PR」（拿没查过的话当事实）。

        2026-09-26 起判据是先看仓库记忆里记着的待合并 PR：没有要核对的那一轮平台**连查都不查**，
        所以这里的措辞是「核过记忆里记着的那些」，不是「查过全部」。
        """
        for intent in ("coding", "review", "rework"):
            directive = intent_directive(intent)
            assert "开工前平台已经替你核过" in directive
            assert "不要自己再查一遍" in directive
            assert "不要对用户断言「仓库没有未合并的 PR」" in directive

    def test_the_header_is_declared_platform_owned(self):
        """平台维护文件头那一行**与 PR 行的状态格** —— 表没了之后，这段不该再提「四列由平台回填」。"""
        for intent in ("coding", "review", "rework"):
            directive = intent_directive(intent)
            assert "文件头" in directive and "盖章" in directive
            assert "**状态**" in directive, "状态那一格归平台改（2026-09-26 起）"
            assert "需求列表" not in directive

    def test_the_unfinished_features_are_asked_for(self):
        """「当前缺口」里按模块列未完成的功能 —— 这是模型写记忆时的硬要求。"""
        for intent in ("coding", "review", "rework", "analysis"):
            directive = intent_directive(intent)
            assert "未完成的功能" in directive
            assert "按模块" in directive
        assert "「当前缺口」更新成**按模块列出所有未完成的功能**" in intent_directive("coding")

    def test_the_changelog_entry_carries_the_pr_status(self):
        """变更记录每条要写 PR 与**状态**（写回时一律「待合并」，之后人工改）。

        2026-09-26 的用户口径：写码与提 PR 是同一条流程，一轮改动一定带着一个 PR ——
        所以提示词里既要有「状态先填待合并」，也要有「PR 没建成时不写这条变更记录」。
        """
        for intent in ("coding", "review", "rework"):
            directive = intent_directive(intent)
            assert "状态一律先填「待合并」" in directive, intent
            assert "PR 没建成" in directive, intent
        # 同一件事在基础提示词的「仓库记忆文件规则」里也要说（子智能体读的是那一份）
        assert "写回时一律填「待合并」" in BASE_PROMPT
        assert "没有 PR" not in BASE_PROMPT

    def test_the_pr_number_must_come_from_this_rounds_receipt(self):
        """变更记录里的编号/链接必须来自**本轮真实来源**，并做一次收尾自检。

        缘起（2026-09-27 实测）：PR #7 在 00:03 建好、00:11 收尾，那一轮主控一次
        `save_repo_memory` 都没调 —— 记忆里没有 #7，隔天「按评审意见修改」被记忆闸门判成
        「没有可修的对象」（闸门只看记忆里记着的 PR）。所以：编号/链接取 coder 回报里的
        `create_pr` 回执原文，缺了就补齐再写，且本轮 PR 必须落到变更记录里。
        """
        for intent in ("coding", "review", "rework"):
            directive = intent_directive(intent)
            assert "create_pr" in directive and "回执" in directive, intent
            assert "不许凭印象编号" in directive, intent
            assert "收尾自检：本轮的 PR 必须落到「变更记录」里" in directive, intent

    def test_refreshing_memory_rides_on_the_analysis_round(self):
        """用户点名刷新记忆：**不新开意图**，就在 analysis 里 —— 拉列表 → 补缺 → 写回 → 给账。

        2026-09-27 用户口径：analysis 本来就有 `get_pr_info` 与 `save_repo_memory`，单开一个
        意图只是多一份分类面与装配登记（先加过一版，同日撤回）。
        """
        directive = intent_directive("analysis")

        assert "刷新 / 更新仓库记忆" in directive
        assert "get_pr_info" in directive and 'state="all"' in directive
        assert "save_repo_memory" in directive
        assert "补一条" in directive, "记忆里没有的 PR 要补进去"
        # 只读：不派子智能体、也不碰 PR
        assert "不要派子智能体" in directive
        assert "comment_pr" in directive and "都不在你手上" in directive
        # 「不写变更记录」那条纪律要留个例外，否则刷新记忆轮会被自己劝住
        assert "**例外**" in directive

    def test_other_intents_have_no_memory_step(self):
        """plan 与 qa 是**唯二**不写记忆的形态：前者还没动代码，后者只回答。"""
        for intent in ("plan", "qa"):
            assert "save_repo_memory" not in intent_directive(intent)
        assert "不写「变更记录」" in intent_directive("analysis"), "分析轮没有 PR，不写变更记录"

    def test_only_the_main_agent_writes_memory(self):
        assert "写回只在 coding / review / analysis 三类轮的收尾时由主控完成" in BASE_PROMPT
        assert "不要写仓库记忆" in CODER_PROMPT

    def test_memory_is_written_at_most_once_per_round(self):
        """写回是整篇覆盖，一轮里反复微调等于反复全量写盘（实测多写了 3 次）。"""
        for intent in ("coding", "review", "rework", "analysis"):
            directive = intent_directive(intent)
            assert "本轮只写一次" in directive
            assert "不要为措辞、空格、排版微调反复写回" in directive

    def test_the_block_is_claimed_to_exist_for_every_agent(self):
        """这段曾经写反过：旧措辞谎称记忆「由运行时注入」，模型据此以为读不到就不读了。
        注入现在是真的，措辞也必须说全 —— 只说「某些形态有」，其余形态的模型就会白读一遍文件。"""
        assert "由运行时注入" not in BASE_PROMPT
        assert "**每个智能体、每个形态**每轮开始时" in BASE_PROMPT
        assert "不要再 `read_file` 读它" in BASE_PROMPT

    def test_subagents_are_told_not_to_reread_the_memory(self):
        assert "先看仓库记忆" in PLANNER_PROMPT
        assert "看仓库记忆" in CODER_PROMPT
        assert "不要再用 `read_file` 读一遍" in PLANNER_PROMPT
        assert "不要再用 `read_file` 读一遍" in CODER_PROMPT
        # planner 曾被告知「用 `read_file` 工具读取仓库记忆」——注入之后那是多余的一次往返
        assert "用 `read_file` 工具读取仓库记忆" not in PLANNER_PROMPT

    def test_subagents_see_memory_as_read_only(self):
        assert "（StoreBackend，**只读**）" in PLANNER_PROMPT
        assert "（StoreBackend，**只读**；写回由主智能体负责）" in CODER_PROMPT
        # planner 曾被告知「首次可生成」，而写回现在不归它
        assert "首次可生成" not in PLANNER_PROMPT


class TestMemoryDateComesFromRuntime:
    """记忆里的日期曾经是编的（写了 2026-02-12，仓库其实只有一个 2026-09-16 的提交）。

    现在文件头那一行由平台写；散文里的日期必须让模型自己跑 `date`，别凭记忆写。
    文件头那一行另归平台（``memory_reconcile`` 每次刷新按本轮重算），模型不必也不该填。
    """

    def test_date_must_come_from_the_runtime(self):
        assert "用 `execute` 跑 `date` 取" in BASE_PROMPT
        assert "不要凭记忆编写" in BASE_PROMPT

    def test_the_file_header_is_declared_platform_owned(self):
        assert "文件头" in BASE_PROMPT
        assert "{YYYY-MM-DD HH:mm}" not in BASE_PROMPT, "别再让模型往头行填时间"


class TestCapabilitiesArePerTurn:
    """历史里的工具可用性不能当成本轮依据。

    2026-10-19 真机：编码轮的文本里已经写着「task：有」，主控仍在思考里翻历史，
    说「工具清单里没有 task」。能力块于是被证明只是给单个工具打的补丁；现在改成
    基础提示词里的一条通用规则 —— 所有形态共享，覆盖 task / storePlan / 写工具。
    """

    def test_the_rule_is_in_the_shared_head(self):
        assert "每一轮的能力不同" in BASE_PROMPT
        assert "只以本轮实际提供的工具为准" in BASE_PROMPT
        assert "工具清单里没有…」只属于那一轮" in BASE_PROMPT
        assert "也不要用记忆去枚举工具清单" in BASE_PROMPT

    def test_readonly_forms_carry_it_too(self):
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT, PLANNER_PROMPT):
            assert "每一轮的能力不同" in prompt


class TestPrMergeIsHuman:
    """合并 PR 必须由人工审核处理，平台不再提供合并工具。"""

    def test_no_prompt_references_a_merge_tool(self):
        for prompt in (
            BASE_PROMPT,
            MAIN_AGENT_PROMPT,
            PLANNER_PROMPT,
            CODER_PROMPT,
            REVIEWER_PROMPT,
        ):
            assert "merge_pr" not in prompt

    def test_merge_is_stated_as_manual(self):
        # 平台中性：Gitee 与 GitHub 都是人工在页面点合并
        assert "PR 的合并由人工在代码平台页面上处理" in BASE_PROMPT
        assert "你没有合并工具" in MAIN_AGENT_PROMPT


class TestReadonlyFormsCanReadPrs:
    """qa / analysis 曾拿不到任何 PR 工具：「最近有哪些未关闭的 PR」这类问题只能靠提交历史猜。

    PR 的**读**工具给它们（两种形态都只读），写侧仍然一个不给。另：只讲「PR 一律不能碰」
    会走到另一个极端 —— 模型连 `get_pr_info` 都不敢用，故这段边界与 `_READONLY_GIT` 同底。
    """

    def test_both_prompts_name_the_pr_read_tools(self):
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT):
            assert "`get_pr_info`" in prompt
            assert "`list_pr_comments`" in prompt

    def test_state_filter_is_documented(self):
        """不提 `state` 的取值，问「未关闭的」时模型只能拿默认值，答不出筛选口径。"""
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT):
            assert "open / closed / merged / all" in prompt

    def test_the_write_side_is_still_named_as_forbidden(self):
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT):
            assert "不评论（`comment_pr`）" in prompt
            assert "不关闭（`close_pr`）" in prompt

    def test_pr_state_is_not_guessed_from_commit_history(self):
        """analysis 要的「当前代码是 PR 还是已合并」，只能以 PR 的实际状态为准。"""
        assert "不要只凭提交历史推测 PR 状态" in QA_AGENT_PROMPT
        assert "不要凭提交历史推测" in ANALYSIS_AGENT_PROMPT

    def test_readonly_boundary_no_longer_points_at_a_planner(self):
        """plan 已改成单智能体，那句「由 planner 产出分步方案」是过期的指路。"""
        assert "由 planner 产出分步方案" not in QA_AGENT_PROMPT
        assert "由 planner 产出分步方案" not in ANALYSIS_AGENT_PROMPT


class TestIntentDirectives:
    def test_review_and_coding_carry_the_user_gate(self):
        assert "等用户决定修哪些问题" in intent_directive("review")
        assert "等用户决定修哪些问题" in intent_directive("coding")

    def test_plan_round_produces_the_plan_itself(self):
        """单智能体的 plan 轮没有 `task`：说「派 planner」这轮就没人出方案了。"""
        directive = intent_directive("plan")
        assert "你自己" in directive
        assert "storePlan" in directive
        assert "不实施" in directive
        assert "用 `task` 派" not in directive

    def test_review_round_reviews_itself(self):
        directive = intent_directive("review")
        assert "你自己" in directive
        assert "comment_pr" in directive
        # 措辞从「全程只读」改成「只读代码」：这一轮要写一次仓库记忆（收尾），
        # 说「全程只读」是句假话
        assert "只读代码" in directive
        assert "全程只读" not in directive
        assert "用 `task` 派" not in directive

    def test_coding_has_both_branches(self):
        """明确要动手 → 主控先查方案、没有就 planner、**落库不等确认**；没明确 → 出方案等确认。"""
        directive = intent_directive("coding")
        assert "get_unexecuted_plan" in directive
        assert "落库后不要停下来等确认" in directive
        assert "确认实施" in directive
        assert "停下等用户明确「确认实施」" in directive

    def test_the_direct_branch_runs_all_the_way_to_review(self):
        """明确要动手那条路要有头有尾：查到方案就派 coder，没有就 planner→coder→reviewer。

        （2026-09-27 用户口径：planner 落库后不需要用户再确认，这一轮一路跑到审查结束。）
        """
        directive = intent_directive("coding")
        assert "直接派 `coder`" in directive
        assert "接着派 `coder`" in directive
        assert "再派 `reviewer`" in directive
        assert "中途不停下来问用户" in directive

    def test_coding_lists_the_direct_execution_phrases(self):
        directive = intent_directive("coding")
        for phrase in ("动手改", "直接做", "按这个方案做"):
            assert phrase in directive, phrase

    def test_coding_recovers_when_there_is_no_plan_to_execute(self):
        """直通分支的前提是上一轮确实留了份未执行方案；没有就得改派 planner。"""
        directive = intent_directive("coding")
        assert "取不到可执行的未执行方案" in directive
        assert "改派 `planner`" in directive

    def test_coder_stops_when_it_cannot_find_a_plan(self):
        assert "取不到方案就停下回报主智能体" in CODER_PROMPT

    def test_rework_fetches_the_review_before_touching_code(self):
        """按评审意见改的第一件事是**取回原文**：凭记忆复述时用户对着 PR 核对必然对不上。"""
        directive = intent_directive("rework")
        assert "先取回审查原文" in directive
        assert "list_pr_comments" in directive
        assert "不得凭记忆复述" in directive
        assert directive.index("list_pr_comments") < directive.index("派 `coder`")

    def test_rework_scopes_the_fix_to_what_the_user_named(self):
        """修复范围等于用户点名的那些问题 —— 顺手扩大范围就是改掉用户没要求改的东西。"""
        directive = intent_directive("rework")
        assert "修复范围严格等于用户点名要改的问题" in directive
        assert "不要顺手扩大范围" in directive

    def test_rework_skips_the_planner(self):
        """改什么已经由审查结论定下来：这一轮派 planner 只是多绕一圈。"""
        directive = intent_directive("rework")
        assert "不需要 `planner`" in directive
        assert "直接派 `coder`" in directive

    def test_rework_tells_the_model_the_gate_is_the_platform_s_business(self):
        """闸门是平台判的（有 open PR 才放行）—— 模型不该自己再查一遍、更不该停下来追问。"""
        directive = intent_directive("rework")
        assert "平台已经替你判过" in directive
        assert "会被直接拦下" in directive

    def test_other_intents_get_no_directive(self):
        assert intent_directive("qa") == ""
        assert intent_directive(None) == ""


class TestCapabilityExit:
    """形态是按意图裁出来的（``profiles.py``）：裁得不合适时模型手上就没有那件工具。

    光说「做不到就说做不到」不够 —— 2026-09-21 那轮 686 秒正是这么来的：plan 形态没有写
    工具，模型于是把方案复述一遍、再问用户「要不要实施」。所以出口要写成一条**真能走**的
    路（``handoffTask``），并且五个形态全挂上：表里新增一个形态却忘挂出口，这里当场红。
    """

    def test_all_five_main_prompts_carry_the_exit(self):
        assert len(_MAIN_PROMPTS) == 5, "形态表变了就得看一眼出口是否跟着挂"
        for form, prompt in _MAIN_PROMPTS.items():
            assert "做不了就交给别的能力形态" in prompt, form
            assert "`handoffTask`" in prompt, form

    def test_subagents_do_not_get_it(self):
        """交接是**主控**的出口：子智能体只回报结论，不自己找人接手。"""
        for name, prompt in (
            ("planner", PLANNER_PROMPT),
            ("coder", CODER_PROMPT),
            ("reviewer", REVIEWER_PROMPT),
        ):
            assert "handoffTask" not in prompt, name

    def test_it_is_scoped_to_a_real_capability_gap(self):
        """「缺能力」与「需求没定」要分清 —— 后者该问用户，不是把活推出去。"""
        for prompt in _MAIN_PROMPTS.values():
            assert "只在**确实缺能力**时用" in prompt
            assert "该问用户就问用户" in prompt
            assert "不要为了省事把活推出去" in prompt

    def test_handing_off_beats_faking_it(self):
        """最大的风险是模型硬撑：拿别的工具变相做，或者口头答应一个做不到的结果。"""
        for prompt in _MAIN_PROMPTS.values():
            assert "缺了就别绕道用别的工具变相做" in prompt
            assert "也别承诺做不到的结果" in prompt

    def test_it_says_what_must_be_written_down(self):
        """接手的那一轮看不到这一轮的上下文，只有 ``notes`` —— 所以两个参数都得讲清。"""
        for prompt in _MAIN_PROMPTS.values():
            assert "`reason` 写清为什么这轮做不了" in prompt
            assert "`notes` 写清**已掌握的情况**" in prompt
            assert "只有你写的 `notes`" in prompt

    def test_the_outer_model_still_speaks_to_the_user(self):
        """交接后由外层用自己的口气讲出去，且照旧不暴露「交给了谁」。"""
        for prompt in _MAIN_PROMPTS.values():
            assert "**由你**用面向用户的口气讲出去" in prompt
            assert "照旧不提交接给了谁" in prompt


class TestReadonlyBoundarySplitsTheTwoCases:
    """只读形态最容易被两件事搞混：用户在**要**改，还是在**问**要不要改。"""

    def test_an_explicit_write_request_is_handed_off(self):
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT):
            assert "明确提出要改代码" in prompt
            assert "别回一句「这轮只读」把活推回去" in prompt

    def test_merely_asking_is_not_a_capability_gap(self):
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT):
            assert "那不是缺能力，是还没定" in prompt
            assert "开始实施" in prompt

    def test_it_says_which_tools_are_missing(self):
        """「先确认真的缺能力」得能照着查：只读轮没有 `task`、也没有那四个写工具。

        「只有只读工具」这句不能再写 —— `analysis` 保留了 `/ai_coding/tmp/` 的写根，
        手里确实有 `write_file` / `edit_file`，说「只有只读工具」是句假话。
        """
        for prompt in (QA_AGENT_PROMPT, ANALYSIS_AGENT_PROMPT):
            assert "不与仓库发生写入" in prompt
            assert "没有 `task`" in prompt
            assert "只有只读工具" not in prompt


class TestPlanRoundHandsOffInsteadOfStalling:
    """plan 形态下最贵的一种错：用户明说「别给方案了，直接改」，模型却拿口令把人挡回去。"""

    def test_a_direct_write_request_goes_out(self):
        assert "调 `handoffTask` 把这一轮交出去" in PLAN_AGENT_PROMPT
        assert "不要拿「请说确认实施」把人挡回去" in PLAN_AGENT_PROMPT

    def test_the_normal_path_still_stops_for_confirmation(self):
        """正常路径一字不变：出完方案停下等「确认实施」，有了出口也不许自行开工。"""
        assert "停下等用户明确「确认实施」" in PLAN_AGENT_PROMPT
        assert "**不实施**" in PLAN_AGENT_PROMPT


class TestReviewRoundHandsOffInsteadOfFixing:
    """审查轮用户已经点名要修时：别自己动手（这轮没有写工具），也别写一串待办糊过去。"""

    def test_it_does_not_fix_by_itself(self):
        assert "不要自己动手" in REVIEW_AGENT_PROMPT
        assert "这轮没有写工具" in REVIEW_AGENT_PROMPT

    def test_it_hands_off_with_the_issue_numbers(self):
        assert "`notes` 里写清**要修的问题编号**" in REVIEW_AGENT_PROMPT

    def test_it_still_stops_for_the_user_otherwise(self):
        assert "停下等用户决定修哪些问题" in REVIEW_AGENT_PROMPT


class TestNoDanglingReferences:
    def test_nonexistent_tool_is_not_referenced(self):
        assert "list_review_findings" not in BASE_PROMPT

    def test_subagent_names_match_the_real_roles(self):
        """提示词点名的子 Agent 必须真存在：曾写 `code_reviewer`（实际叫 `reviewer`），
        照着派必然扑空。这三个名字归**多智能体主控**那份 —— 其余形态没有 `task`，
        在共用段里点它们的名等于让模型去派一个它不具备的能力（见下面那个测试类）。"""
        assert "code_reviewer" not in MAIN_AGENT_PROMPT
        for role in ("planner", "coder", "reviewer"):
            assert f"`{role}`" in MAIN_AGENT_PROMPT, role

    def test_paths_point_at_real_locations(self):
        assert "/reviews/" not in BASE_PROMPT
        assert "`/projects`" not in BASE_PROMPT


class TestChineseEverywhere:
    """中文约束必须覆盖「过程旁白」，不能只管「面向用户」的那部分。"""

    def test_rule_covers_all_natural_language_output(self):
        assert "所有自然语言输出一律使用中文" in BASE_PROMPT
        # 旧的「面向用户」限定词给子智能体的旁白留了后门（表现就是英文输出）
        assert "所有面向用户的自然语言输出" not in BASE_PROMPT

    def test_subagents_are_explicitly_in_scope(self):
        assert "子智能体同样适用" in BASE_PROMPT

    def test_english_process_narration_is_still_banned(self):
        assert "禁止输出英文过程描述" in BASE_PROMPT


class TestSkillRulesBelongToTheFormThatDoesTheWork:
    """技能规则曾整段按「多智能体主控」的口吻写，却挂在八个智能体共用的段里。

    后果是三步都不可能执行的指令：qa / analysis / plan / review 没有 `task`，却被要求
    「必须优先委派 `reviewer` 子 Agent」；plan / qa / analysis 没有 `comment_pr`，却被要求
    「在 PR 上回复对应问题」；`reviewer` 子智能体自己的提示词更是叫它**去委派 reviewer**。
    现在共用段只说「哪件事该用哪套 skill」，委派与回评归 ``_ROLE_ORCHESTRATE`` ——
    那是唯一挂子智能体、也唯一拿得到 `comment_pr` 的形态。
    """

    def test_only_the_orchestrator_is_told_to_delegate_review(self):
        assert "审查一律派 `reviewer`" in MAIN_AGENT_PROMPT
        for name, prompt in (
            ("qa", QA_AGENT_PROMPT),
            ("analysis", ANALYSIS_AGENT_PROMPT),
            ("plan", PLAN_AGENT_PROMPT),
            ("review", REVIEW_AGENT_PROMPT),
            ("planner", PLANNER_PROMPT),
            ("coder", CODER_PROMPT),
            ("reviewer", REVIEWER_PROMPT),
        ):
            assert "必须优先委派" not in prompt, name
            assert "委派 `reviewer` 子 Agent" not in prompt, name

    def test_pr_reply_is_only_given_to_the_orchestrator(self):
        assert "在 PR 上回复" in MAIN_AGENT_PROMPT
        for name, prompt in (
            ("qa", QA_AGENT_PROMPT),
            ("analysis", ANALYSIS_AGENT_PROMPT),
            ("plan", PLAN_AGENT_PROMPT),
        ):
            assert "在 PR 上回复" not in prompt, name

    def test_taking_back_the_review_text_is_only_the_orchestrators_job(self):
        """取回审查原文是 rework 指令的一段，只有主控形态会拼上它。"""
        assert "先取回审查原文" in intent_directive("rework")
        assert "先取回审查原文" not in ANALYSIS_AGENT_PROMPT

    def test_the_review_skill_is_named_where_the_review_happens(self):
        assert "`code-review` skill" in REVIEWER_PROMPT
        assert "`code-review` skill" in REVIEW_AGENT_PROMPT
        assert "`code-review` skill" in MAIN_AGENT_PROMPT


class TestGitWriteCommandsOnlyReachTheCoder:
    """``GIT_PROMPT`` 原来把 clone / 建分支 / commit / push 的写法放在写侧共用段里，
    于是 orchestrate 主控（不亲自写代码）、plan 主控与 planner（出方案零动手）、
    review 主控与 reviewer（只读代码）都收到了一份「怎么提交」的说明书 —— 与它们各自的
    角色口径正相反。只读命令留给所有形态，改仓库的命令收敛到 ``_GIT_WRITE_STEPS``。
    """

    def test_the_write_steps_are_in_the_coder_prompt(self):
        assert "建分支：`git checkout -b" in CODER_PROMPT
        assert "推送：`git push origin" in CODER_PROMPT

    def test_no_other_prompt_carries_them(self):
        for name, prompt in (
            ("orchestrate", MAIN_AGENT_PROMPT),
            ("plan", PLAN_AGENT_PROMPT),
            ("review", REVIEW_AGENT_PROMPT),
            ("qa", QA_AGENT_PROMPT),
            ("analysis", ANALYSIS_AGENT_PROMPT),
            ("planner", PLANNER_PROMPT),
            ("reviewer", REVIEWER_PROMPT),
        ):
            for step in ("git checkout -b", "git add -A", "git push origin"):
                assert step not in prompt, (name, step)

    def test_the_read_side_commands_stay_for_everyone(self):
        """摘掉写命令不能让模型以为「git 一律别碰」——只读命令仍写在共用段里。"""
        for prompt in (MAIN_AGENT_PROMPT, PLANNER_PROMPT, REVIEWER_PROMPT):
            assert "git log" in prompt

    def test_the_plan_step_says_who_pushes(self):
        """方案的末步是 coder 的动作：只写「git push 后用 create_pr」，plan 轮会当自己的活。"""
        assert "这一步**由 coder 执行**" in PLAN_AGENT_PROMPT
