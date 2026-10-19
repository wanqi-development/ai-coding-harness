"""按意图装配智能体的守卫测试（``agent/profiles.py``）。

意图逐轮判定、装配逐 profile 缓存 —— 本文件钉住三件事：① 意图 → 形态的映射表；
② 形态落到工具 / 子智能体 / 提示词上的差异；③ 缓存键是形态。形态是**唯一的轴**：
它同时决定挂哪些子智能体、给哪些主工具、用哪套角色段。「裁掉比在提示词里劝阻有效」是
这次改动的依据：全量装配时问一句「这仓库是做什么的」也挂着 ``create_pr`` 与三个子智能体，
实测模型会拿 qa 类问题去派 planner 落方案。
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import ClassVar

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from ai_coding_harness.agent import profiles as profiles_module
from ai_coding_harness.agent.graph import build_agent
from ai_coding_harness.agent.middleware import (
    BLOCKING_FORMS,
    REFRESH_FORMS,
    DelegationGateMiddleware,
)
from ai_coding_harness.agent.profiles import FORM_ALIASES
from ai_coding_harness.agent.prompts import (
    ANALYSIS_FORM,
    CODER_PROMPT,
    ORCHESTRATE_FORM,
    PLAN_FORM,
    PLANNER_PROMPT,
    QA_FORM,
    REVIEW_FORM,
    REVIEWER_PROMPT,
    build_main_prompt,
)
from ai_coding_harness.agent.subagents import build_subagents
from ai_coding_harness.config import Settings
from ai_coding_harness.safety.policy import policy_for
from ai_coding_harness.server.intent import DIRECT_INTENTS, TASK_TYPES
from ai_coding_harness.session import manager as manager_module
from ai_coding_harness.session.manager import Session, SessionManager, SessionRuntime
from ai_coding_harness.tools.git import GiteeRepo
from ai_coding_harness.tools.plan import PlanContext
from ai_coding_harness.tools.register import build_main_tools

REPO = GiteeRepo(owner="owner", repo="repo", token="test-token")

# 输出形态规则段的小标题（``adhd/AGENT.md`` 首行）。用它做标记而不是正文里的某句话：
# 正文措辞会随迭代改，小标题是这段「在不在」最稳的锚点。
OUTPUT_STYLE_MARKER = "## 面向用户的输出形态"

# 写侧工具：给了它们就等于告诉模型「这轮能改代码 / 能改记忆 / 能改方案」
_WRITE_TOOLS = (
    "create_pr",
    "close_pr",
    "comment_pr",
    "save_repo_memory",
    "storePlan",
    "updatePlan",
    "updatePlanSteps",
    "deletePlan",
)


def _names(tools) -> list[str]:
    return [t.name for t in tools]


def _main_tools(form: str) -> list[str]:
    return _names(
        build_main_tools(
            Settings(),
            SimpleNamespace(name="web_search_tool"),  # type: ignore[arg-type]
            SimpleNamespace(name="fetch_url_tool"),  # type: ignore[arg-type]
            REPO,
            SimpleNamespace(awrite=None),  # type: ignore[arg-type]
            form=form,
            database=object(),  # type: ignore[arg-type]  # 只为通过 build_plan_tools 的真值判断
            plan_ctx=PlanContext(session_db_id=1, owner="owner", repo="repo"),
        )
    )


class TestIntentToProfile:
    def test_mapping_table(self):
        """意图名 → 形态名。形态名同时是缓存键与工具 / 提示词的索引。"""
        assert profiles_module.profile_for("qa").form == QA_FORM
        assert profiles_module.profile_for("analysis").form == ANALYSIS_FORM
        assert profiles_module.profile_for("plan").form == PLAN_FORM
        assert profiles_module.profile_for("review").form == REVIEW_FORM
        assert profiles_module.profile_for("coding").form == ORCHESTRATE_FORM
        assert profiles_module.profile_for("rework").form == ORCHESTRATE_FORM

    def test_only_the_registered_alias_shares_a_form(self):
        """除了**显式登记**的别名，意图之间不许共用形态：共用一个键就等于共用提示词与工具子集。

        编码这一形态叫 ``orchestrate``（它承载的是「派子智能体编排一套流程」），
        与意图名 ``coding`` 不同名 —— 其余四个恰好同名，别把这点当成规则。

        ``rework``（按评审意见修改）是**唯一**有意复用的一种：要改的代码、要走的环路与 coding
        完全相同（差别只在记忆闸门），所以复用同一个 ``AgentProfile`` 对象；表里再抄一份同形
        记录就会往后各漂各的。真要给它别的工具或角色段，就得摘出别名、单开形态。
        """
        forms = {i: profiles_module.profile_for(i).form for i in TASK_TYPES}
        profiled = {i: f for i, f in forms.items() if i in profiles_module.PROFILED_INTENTS}
        # 别名之外，每个意图各占一种形态
        unique = {i: f for i, f in profiled.items() if i not in FORM_ALIASES}
        assert len(set(unique.values())) == len(unique)
        # 别名只与被复用的那个同形
        for alias, target in FORM_ALIASES.items():
            assert profiled[alias] == profiled[target], alias
        assert profiled["coding"] == ORCHESTRATE_FORM

    def test_the_alias_shares_the_same_assembly_object(self):
        """别名必须是**同一个对象**：形态一漂，工具子集、角色段与缓存键跟着一起漂。"""
        assert FORM_ALIASES == {"rework": "coding"}
        for alias, target in FORM_ALIASES.items():
            assert profiles_module.profile_for(alias) is profiles_module.profile_for(target), alias
            assert profiles_module.profile_for(alias).subagents == (
                "planner",
                "coder",
                "reviewer",
            ), alias

    def test_the_analysis_form_can_refresh_memory_and_stays_out_of_the_gate(self):
        """「刷新仓库记忆」走的就是 ``analysis``：它有写记忆与读 PR 的工具，且**不在闸门里**。

        闸门拦的是 plan / coding（``BLOCKING_FORMS``）：仓库还有未合入 PR 时那两轮会被拦下。
        而「记忆里没记着这些 PR」恰恰是刷新记忆要解决的事 —— 这一轮必须放行，否则它永远
        跑不起来（2026-09-27 实测：PR #7 没记进记忆，隔天 rework 被闸门判成「没有可修的对象」）。
        """
        assert ANALYSIS_FORM in REFRESH_FORMS
        assert ANALYSIS_FORM not in BLOCKING_FORMS

    def test_qa_and_analysis_are_separate_profiles(self):
        """两者装配相同（都只读、都不挂子智能体），但角色提示词不同：
        产出落点与工作法都不一样，共用一个键会让后一种形态拿到前一种的口径。"""
        qa = profiles_module.profile_for("qa")
        analysis = profiles_module.profile_for("analysis")
        assert qa is not analysis
        assert qa.form != analysis.form

    def test_only_coding_mounts_subagents(self):
        """plan / review 由主控自己做：挂了子智能体反而会把「自己出方案」推回派发。"""
        for intent in ("qa", "analysis", "plan", "review"):
            profile = profiles_module.profile_for(intent)
            assert profile.subagents == (), intent

    def test_coding_mounts_all_three_in_a_stable_order(self):
        assert profiles_module.profile_for("coding").subagents == (
            "planner",
            "coder",
            "reviewer",
        )

    def test_unknown_intent_falls_back_to_full(self):
        """不知道用户要干什么时，少给能力的代价（这轮做不了）比多给更难挽回。"""
        full = profiles_module.profile_for("coding")
        assert profiles_module.profile_for("不存在") is full
        assert profiles_module.profile_for(None) is full
        assert profiles_module.profile_for("") is full

    def test_every_dispatchable_intent_is_registered(self):
        """新增意图必须在此登记：漏了不会报错，只会静默拿到全量装配。"""
        missing = set(TASK_TYPES) - DIRECT_INTENTS - profiles_module.PROFILED_INTENTS
        assert missing == set(), f"这些意图没有专属形态，会落到全量装配：{missing}"

    def test_direct_intents_are_not_profiled(self):
        assert DIRECT_INTENTS.isdisjoint(profiles_module.PROFILED_INTENTS)


class TestMainToolSubsets:
    """主工具按形态给 —— 与 ``_MAIN_PROMPTS`` 的角色段、``subagents`` 是配套的三处。"""

    def test_qa_gets_the_pr_read_tools_only(self):
        """读仓库、读记忆走 `read_file`（本就是只读工具），无需另给；PR 的**读**工具要给 ——
        「最近有哪些未关闭的 PR」「当前代码在 PR 里还是已合并」正是问答的常见问法。"""
        assert _main_tools(QA_FORM) == [
            "web_search_tool",
            "fetch_url_tool",
            "get_pr_info",
            "list_pr_comments",
        ]

    def test_analysis_gets_the_memory_writeback(self):
        """分析轮的结论就是仓库记忆的正文来源（「仓库基本信息」+「当前缺口」）——
        2026-09-26 起它多一件写工具 `save_repo_memory`；仓库与 PR 照旧只读。"""
        assert _main_tools(ANALYSIS_FORM) == [
            "web_search_tool",
            "fetch_url_tool",
            "get_pr_info",
            "list_pr_comments",
            "save_repo_memory",
        ]

    def test_qa_gets_no_write_tool_at_all(self):
        """问答只给读工具：一件写工具都不给（看不见比提示词里劝阻有效）。"""
        for name in _WRITE_TOOLS:
            assert name not in _main_tools(QA_FORM), name

    def test_plan_form_owns_the_whole_plan_lifecycle(self):
        """方案的增删改查全归 plan 形态的主控（2026-09-25）：出方案不止「写一份」，
        还要能看、能改、能删 —— 少一件，用户说「把那份方案删了」时就没路可走。
        **没有 save_repo_memory**：这一轮还没动代码，记忆正文只在 coding / review 收尾时写。"""
        assert _main_tools(PLAN_FORM) == [
            "web_search_tool",
            "fetch_url_tool",
            "get_pr_info",
            "storePlan",
            "getPlan",
            "planExists",
            "updatePlan",
            "updatePlanSteps",
            "deletePlan",
        ]

    def test_no_other_form_gets_the_plan_crud_tools(self):
        """方案对象的增删改查只有 plan 形态有：别的形态要么不碰方案，要么只管进度。"""
        for form in (ORCHESTRATE_FORM, QA_FORM, ANALYSIS_FORM, REVIEW_FORM):
            for name in ("getPlan", "updatePlan", "updatePlanSteps", "deletePlan"):
                assert name not in _main_tools(form), (form, name)

    def test_plan_form_cannot_comment_or_merge(self):
        """出方案不下场评论 PR，也不建 / 关 PR。"""
        names = _main_tools(PLAN_FORM)
        assert "comment_pr" not in names
        assert "close_pr" not in names
        assert "create_pr" not in names

    def test_review_form_can_read_and_post_pr_comments(self):
        assert _main_tools(REVIEW_FORM) == [
            "web_search_tool",
            "fetch_url_tool",
            "get_pr_info",
            "list_pr_comments",
            "comment_pr",
            "save_repo_memory",
        ]

    def test_review_form_writes_the_memory_at_the_end(self):
        """审查结论是稳定事实：这一轮收尾时由主控写回记忆（2026-09-25 起）。"""
        assert "save_repo_memory" in _main_tools(REVIEW_FORM)

    def test_plan_form_does_not_write_repo_memory(self):
        """出方案轮不写记忆：写进去的是「打算做的事」，不是既成事实。"""
        assert "save_repo_memory" not in _main_tools(PLAN_FORM)

    def test_orchestrate_form_keeps_the_full_set(self):
        """``orchestrate`` 是全流程形态：PR 读写 + 记忆 + **只读的** ``get_unexecuted_plan``。

        那件只读工具是 2026-09-27 加的：用户明确说「直接做」时，主控自己先查有没有可执行的
        方案（有就直接派 coder，没有才先派 planner），省掉「派了 coder 才发现没方案」那一圈。
        方案的增删改仍不给它（归 planner），见 ``test_tool_registry``。
        """
        assert _main_tools(ORCHESTRATE_FORM) == [
            "web_search_tool",
            "fetch_url_tool",
            "get_pr_info",
            "list_pr_comments",
            "comment_pr",
            "close_pr",
            "get_unexecuted_plan",
            "planExists",
            "save_repo_memory",
        ]

    def test_readonly_builds_no_subagent_at_all(self):
        """一个子智能体都不给时 deepagents 连 `task` 工具都不加 —— 单智能体轮想派也派不了。"""
        assert build_subagents(
            tools_by_role={}, model_for=_fake_model_for, policies={}
        ) == []


class _RoleModel(GenericFakeChatModel):
    """假模型：是**真的** ``BaseChatModel``，同时带着 ``role`` 让断言认得出「为谁取的」。

    不能再用 ``SimpleNamespace``：子智能体装配会**真造**一份压缩中间件
    （``agent/summarization.py``，2026-10-15 起我们自己构造那一份），而上游 ``__init__``
    第一件事就是 ``model.with_retry()`` —— 假得太彻底就撑不到断言那一步。
    """

    role: str = ""


def _fake_model_for(role: str) -> _RoleModel:
    """假模型工厂：把「为谁取的」记在实例上（本文件不关心模型怎么来的）。"""
    return _RoleModel(messages=iter([AIMessage(content="ok")]), role=role)


class TestSubagentSubset:
    """``build_subagents`` 的「角色 → spec」映射与顺序（只有 coding 形态会用到）。"""

    def _specs(self, *roles: str):
        return build_subagents(
            tools_by_role={role: [] for role in roles},
            model_for=_fake_model_for,
            policies={role: policy_for(role) for role in roles},
        )

    def test_each_role_gets_its_own_model(self):
        """子智能体**各自**取模型，不再继承主智能体 —— 主控在 coding 形态下是 glm，
        继承就等于四家活挤给一家（planner 出方案要 glm、coder 写码要 deepseek、
        reviewer 审查要 kimi）。"""
        specs = self._specs("planner", "coder", "reviewer")

        assert [spec["model"].role for spec in specs] == [
            "planner",
            "coder",
            "reviewer",
        ]

    def test_a_role_alone_is_supported(self):
        """单挂一个角色本身是支持的（此前 plan / review 就这么装配）；现在只有 coding 用，
        但装配层的能力不该跟着形态变。"""
        assert [s["name"] for s in self._specs("reviewer")] == ["reviewer"]
        assert [s["name"] for s in self._specs("planner")] == ["planner"]

    def test_subagents_do_not_mount_the_delegation_gate(self):
        """闸门只服务主控：子智能体没有 ``task`` 可调，挂上去只是多一层空包装。"""
        specs = self._specs("planner", "coder", "reviewer")
        assert not any(
            isinstance(middleware, DelegationGateMiddleware)
            for spec in specs
            for middleware in spec["middleware"]
        )

    def test_coding_mounts_three_in_table_order(self):
        """顺序跟 ``_ROLES`` 走（deepagents 照此写 `task` 的工具描述）。"""
        specs = self._specs("reviewer", "planner", "coder")
        assert [s["name"] for s in specs] == ["planner", "coder", "reviewer"]

    def test_unknown_role_is_dropped(self):
        assert self._specs("nonexistent") == []


class TestMainPromptForms:
    """主智能体五种形态的提示词差异：``orchestrate`` / ``qa`` / ``analysis`` / ``plan`` / ``review``。

    只读两态共用「能力边界 / git 边界 / 检索纪律」三段，差别在产出落点与工作法 ——
    分开写是为了让各自那一段说透，不要退回成一份「只读通用段」。
    plan / review 与各自的子智能体共用工作法段（``_PLAN_METHOD`` / ``_REVIEW_METHOD``），
    差别只在结论交给谁：子智能体交给主控，主控形态直接交给用户。
    """

    def _qa(self) -> str:
        return build_main_prompt("https://gitee.com/o/r.git", form=QA_FORM)

    def _analysis(self) -> str:
        return build_main_prompt("https://gitee.com/o/r.git", form=ANALYSIS_FORM)

    def _plan(self) -> str:
        return build_main_prompt("https://gitee.com/o/r.git", form=PLAN_FORM)

    def _review(self) -> str:
        return build_main_prompt("https://gitee.com/o/r.git", form=REVIEW_FORM)

    def _orchestrating(self) -> str:
        return build_main_prompt("https://gitee.com/o/r.git")

    def _single_agent_forms(self) -> list[str]:
        return [self._qa(), self._analysis(), self._plan(), self._review()]

    def test_capability_boundary_is_written_down(self):
        for prompt in (self._qa(), self._analysis()):
            assert "能力边界（强要求）" in prompt
            assert "没有 `task`（派不了子智能体）" in prompt

    def test_it_points_the_user_at_the_right_switch(self):
        """没有写工具时模型会口口声声「我这就改」—— 必须给出去哪儿切的出口。"""
        for prompt in (self._qa(), self._analysis()):
            assert "开始实施" in prompt
            assert "出方案" in prompt

    def test_readonly_forms_keep_read_only_git(self):
        """摘掉的是「怎么推分支、怎么提 PR」；看 git 的能力必须补回来，
        否则模型会把「只读」读成「git 一律别碰」，连 `git log` 都不敢用。"""
        for prompt in (self._qa(), self._analysis()):
            assert "只读的 git 命令照常可用" in prompt
            assert "不 `git add` / `git commit` / `git push`" in prompt
            assert "## git 的工作方式" not in prompt
            assert "建分支：`git checkout -b" not in prompt

    def test_readonly_forms_carry_the_retrieval_discipline(self):
        for prompt in (self._qa(), self._analysis()):
            assert "## 检索纪律" in prompt
            assert "只读命中的文件" in prompt
            assert "不要通读仓库" in prompt
            assert "不搭原型、不复制仓库做实验" in prompt

    def test_qa_closes_down_the_output_directory(self):
        """问答的结论就是回复本身：先落文件再回一句「已写入文件」等于把答案藏起来。

        2026-09-22 那轮 qa 是从**例外**钻出去的：那行后半句曾写着「只有用户明确要文件时才写入」，
        用户说「给我一份仓库记忆文件」，它就把记忆整篇复制进 `/ai_coding/output/`，再回一句
        「已写入 + 路径」—— 与被禁的那句话一字不差。所以钉的不只是那半句，还有「不留例外」，
        以及「要文件时该怎么办」得有落点，否则缺口还在、模型还会自己找路。
        """
        prompt = self._qa()
        assert "`/ai_coding/output/`：**本轮不用" in prompt
        assert "只有用户明确要文件时才写入" not in prompt
        assert "把全文贴进回复" in prompt
        assert "不要先写进 `/ai_coding/output/` 再回一句「已写入文件」" in prompt

    def test_analysis_closes_down_the_output_directory_too(self):
        """分析的结论也是回复本身：`/ai_coding/output/` 没有回传通路，写进去等于藏起来。

        analysis 曾有个「成篇产物落成文件」的口径，`repo-bootstrap-analysis` 技能里也写着
        往 output 写报告 —— 但那个目录 API 与前端**一处都不读**、卷也只挂 `project`，写进去
        的文件随沙箱销毁、用户取不到。所以与 qa 同形撤掉 output，只留 tmp：取证的中间产物
        有落脚点，结论必须进回复。
        """
        prompt = self._analysis()
        assert "`/ai_coding/output/`：**本轮不写**" in prompt
        assert "`/ai_coding/tmp/`：中间过程文件（**可写**）" in prompt
        assert "不要先落成文件、再回一句「已写入文件」把答案藏起来" in prompt
        assert "分析产物（**可写**）" not in prompt
        assert "成篇的产物可以落成文件" not in prompt

    def test_analysis_form_is_the_one_readonly_form_that_writes_the_memory(self):
        """分析轮的结论要沉淀：它的提示词里有「唯一的一处写入：仓库记忆」这一段
        （仓库基本信息 + 当前缺口），而 qa 没有 —— 两个只读形态的差别就在这一处。"""
        prompt = self._analysis()
        assert "这一轮唯一的一处写入：仓库记忆" in prompt
        assert "仓库基本信息" in prompt and "当前缺口" in prompt
        assert "这一轮唯一的一处写入" not in self._qa()
        assert "已证实" in prompt
        assert "待验证" in prompt

    def test_qa_does_not_carry_the_analysis_method(self):
        assert "分析工作法" not in self._qa()

    def test_analysis_is_not_a_qa_prompt(self):
        assert "## 回答口径" not in self._analysis()

    def test_readonly_is_not_an_excuse(self):
        assert "不要拿「我这轮只读」当挡箭牌" in self._qa()

    # -- plan / review：主控自己干，不派发 ---------------------------------

    def test_plan_form_owns_the_plan_and_stores_it(self):
        """单智能体的 plan 形态下方案由主控自己出并落库：说「派 planner」就等于这轮没人出方案。"""
        prompt = self._plan()
        assert "你自己出方案" in prompt
        assert "`storePlan`" in prompt
        assert "不实施" in prompt

    def test_plan_form_only_asks_about_user_side_gaps(self):
        """技术选型与实现取舍是方案的份内事：反问用户等于把该干的活推回去。"""
        prompt = self._plan()
        assert "疑问只来自用户侧" in prompt
        assert "技术选型、实现方式、命名与写法取舍**由你自己定**" in prompt
        assert "停下等用户明确「确认实施」" in prompt

    def test_plan_form_shares_the_method_with_the_planner(self):
        """同一套方案工作法：两条路出的方案必须口径一致，否则用户看到的详略会随意图变。"""
        prompt = self._plan()
        assert "是了解现状的主要依据" in prompt
        assert "禁止通读仓库" in prompt
        assert "方案的最后一步必须是「提交 PR」" in prompt

    def test_plan_form_writes_only_the_plan(self):
        """出方案阶段：沙箱里一个文件都不落（写工具已撤），**记忆也不写** —— 唯一的写是方案落库。

        这段口径改过三次：先是「一个文件都不落」（与注入的「第 0 步先写记忆」打架），
        改成白名单「记忆写回 + 方案落库」；2026-09-25 起出方案轮**不写记忆**（那时还没动
        代码，写进去的是「打算做的事」），白名单只剩方案那四个工具。
        """
        prompt = self._plan()
        assert "**本轮不落沙箱文件**" in prompt
        assert "写文件的工具已撤掉" in prompt
        assert "**只有方案那一处**" in prompt
        assert "save_repo_memory" not in prompt
        assert "（分析产物）可写" not in prompt
        assert "除了这一个目录，出方案阶段不落任何文件" not in prompt

    def test_plan_form_memory_is_read_only_again(self):
        """出方案轮不再写记忆（2026-09-25）：`_PLAN_WORKDIR` 那行与 planner 同口径，
        都写「**只读**」—— 曾经因为 plan 主控持有 `save_repo_memory` 而含糊过。"""
        prompt = self._plan()
        assert "（StoreBackend，**只读**）" in prompt
        assert "记忆正文只在 coding / review / analysis 收尾时写" in prompt

    def test_review_form_knows_which_directories_it_may_write(self):
        prompt = self._review()
        assert "`/ai_coding/review/`：审查报告（**可写**）" in prompt
        assert "本角色的读写权限见「你的角色」段" in prompt
        assert "收尾时写回仓库记忆" in prompt, "review 轮的写回职责要写在角色段里"

    def test_the_reviewer_subagent_keeps_the_memory_read_only(self):
        """写回是主控的事：reviewer 子智能体那份仍是只读（它没有 save_repo_memory）。"""
        assert "仓库记忆只读" in REVIEWER_PROMPT

    def test_the_shared_directory_table_points_at_the_per_form_list(self):
        """共用段不再逐目录声称「可写」：那是按形态给的，写错模型就去写一个会被拒的目录。"""
        for prompt in (self._qa(), self._analysis(), self._plan(), self._review()):
            assert "以你这份提示词的「工作目录」段为准" in prompt

    def test_no_form_gets_an_unconditional_commit_and_push(self):
        """「成果要及时落地：代码 commit + push」对只读形态是一句做不到的指令。"""
        for prompt in (self._qa(), self._analysis(), self._plan(), self._review()):
            assert "代码 commit + push" not in prompt

    def test_review_form_reviews_without_changing_code(self):
        prompt = self._review()
        assert "你自己审查" in prompt
        # 「全程只读」自 2026-09-25 起不成立：这一轮收尾要写一次仓库记忆，措辞改成「不改代码」
        assert "不改代码" in prompt
        assert "全程只读" not in prompt
        assert "`comment_pr`" in prompt

    def test_review_form_stops_after_the_report(self):
        """审完就停下等用户点单：自动进修复会改掉用户没要求改的东西。"""
        prompt = self._review()
        assert "完整问题清单" in prompt
        assert "停下等用户决定修哪些问题" in prompt

    def test_review_form_defers_to_the_pr_comments(self):
        """凭记忆复述会把上一轮的措辞当成这一轮的事实 —— 用户对着 PR 核对时对不上。"""
        assert "不得凭记忆复述" in self._review()

    def test_review_form_shares_the_method_with_the_reviewer(self):
        prompt = self._review()
        assert "git diff --stat" in prompt
        assert "问题分级（严重 / 一般 / 建议）" in prompt

    # -- 全体共用的部分 ----------------------------------------------------

    def test_only_orchestrate_dispatches(self):
        """单智能体形态下没有 `task`：提示词里就不该出现「派子智能体」。"""
        for prompt in self._single_agent_forms():
            assert "通过 `task` 工具分派" not in prompt
            assert "不自动回环" not in prompt
            assert "d. 用 `task` 派 `reviewer`" not in prompt
            assert "派 `planner`" not in prompt

    def test_every_form_shares_the_user_facing_output_rules(self):
        for prompt in [*self._single_agent_forms(), self._orchestrating()]:
            assert "## 面向用户的输出" in prompt
            assert "不要罗列调用了哪些子智能体" in prompt

    def test_no_form_is_told_the_output_directory_is_writable(self):
        """output 全形态收口：qa / analysis 先收，plan 与 orchestrate 跟着撤。

        理由是同一条 —— 那个目录没有回传通路（API / 前端一处都不读它，卷也只挂 `project`），
        写进去的文件随沙箱销毁、用户取不到。收完之后**没有任何形态**能写它，所以共用段里
        「结论写入对应输出目录」那句也得改掉，否则提示词还在承诺一个不存在的落点。
        """
        for prompt in [*self._single_agent_forms(), self._orchestrating()]:
            assert "**任何形态都不写**" in prompt
            assert "（分析产物）可写" not in prompt
            assert "结论写入对应输出目录" not in prompt

    def test_every_form_carries_the_target_repo(self):
        for prompt in [*self._single_agent_forms(), self._orchestrating()]:
            assert "https://gitee.com/o/r.git" in prompt
            assert "## 目标仓库" in prompt

    def test_every_form_carries_the_output_style_rules(self):
        """输出形态规则（``adhd/AGENT.md``）挂主智能体 —— 五个形态都是主智能体。"""
        for prompt in [*self._single_agent_forms(), self._orchestrating()]:
            assert OUTPUT_STYLE_MARKER in prompt

    def test_subagents_stay_out_of_the_output_style_rules(self):
        """它管的是「怎么对用户说话」；子智能体不直接对用户，挂了只会白占上下文。"""
        for prompt in (PLANNER_PROMPT, CODER_PROMPT, REVIEWER_PROMPT):
            assert OUTPUT_STYLE_MARKER not in prompt

    def test_missing_output_style_file_raises(self):
        """规则文件缺失当场炸：静默继续等于跑着一份没人知道的提示词。"""
        with pytest.raises(RuntimeError, match="输出形态规则文件缺失"):
            build_main_prompt(
                "https://gitee.com/o/r.git",
                settings=Settings(agent_output_style_file="adhd/does-not-exist.md"),
            )

    def test_the_orchestrating_form_still_dispatches(self):
        assert "通过 `task` 工具分派" in self._orchestrating()

    def test_unknown_form_raises(self):
        """形态不是用户输入：漏登记就该当场炸，不该静默给错提示词。"""
        with pytest.raises(KeyError):
            build_main_prompt("https://gitee.com/o/r.git", form="不存在")


def _runtime() -> SessionRuntime:
    return SessionRuntime(sandbox=SimpleNamespace(), backend=SimpleNamespace())


def _session() -> Session:
    return Session(
        public_id="11111111-1111-1111-1111-111111111111",
        db_id=7,
        repo=GiteeRepo(owner="owner", repo="repo", token="tok", git_username="oauth2"),
        repo_url="https://gitee.com/owner/repo.git",
    )


def _manager(monkeypatch) -> tuple[SessionManager, list[dict]]:
    """假 manager：``build_agent`` 换成本地记录器，其余（工具 / 子智能体装配）走真代码。"""
    calls: list[dict] = []

    def fake_build_agent(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(manager_module, "build_agent", fake_build_agent)
    manager = SessionManager(
        settings=Settings(),
        model_for=_fake_model_for,
        fallback_for=lambda role: None,
        web_search_tool=SimpleNamespace(name="web_search_tool"),  # type: ignore[arg-type]
        store=SimpleNamespace(),  # type: ignore[arg-type]
        checkpointer=SimpleNamespace(),  # type: ignore[arg-type]
        database=SimpleNamespace(),  # type: ignore[arg-type]
    )
    return manager, calls


class TestMainAgentModelFollowsForm:
    """主智能体的模型也挂在形态那根轴上：形态名就是角色名，两个轴不许分家。"""

    @pytest.mark.parametrize("intent", ["qa", "analysis", "plan", "review", "coding", "rework"])
    def test_main_agent_asks_for_a_model_by_form(self, monkeypatch, intent):
        manager, calls = _manager(monkeypatch)
        profile = profiles_module.profile_for(intent)

        manager.get_agent(_runtime(), _session(), profile)

        assert calls[0]["model"].role == profile.form


class TestAgentsAreBuiltPerCall:
    """**不缓存主智能体**（2026-10-14 用户口径：「每次都创建一个新的 agent」）。

    模型与上下文窗口都是**建实例时读**的，缓存住之后在设置页改完要重启才生效；每轮现建
    就是「改完立刻生效」的另一半。形态仍然决定装配（工具 / 子智能体 / 提示词），只是不再留着复用。
    """

    def test_every_call_builds_a_new_agent(self, monkeypatch):
        manager, calls = _manager(monkeypatch)
        runtime, session = _runtime(), _session()
        first = manager.get_agent(runtime, session, profiles_module.profile_for("plan"))
        second = manager.get_agent(runtime, session, profiles_module.profile_for("plan"))

        assert first is not second, "同一形态也现建：缓存住就换不动模型与窗口"
        assert len(calls) == 2

    def test_the_form_still_decides_the_assembly(self, monkeypatch):
        """形态这根轴没变：六个意图里 rework 与 coding 共用一个形态，其余各一个。"""
        manager, calls = _manager(monkeypatch)
        runtime, session = _runtime(), _session()
        for intent in ("qa", "analysis", "plan", "review", "coding", "rework"):
            manager.get_agent(runtime, session, profiles_module.profile_for(intent))

        assert [call["model"].role for call in calls] == [
            QA_FORM,
            ANALYSIS_FORM,
            PLAN_FORM,
            REVIEW_FORM,
            ORCHESTRATE_FORM,
            ORCHESTRATE_FORM,
        ]
        assert [call["system_prompt"] for call in calls] == [
            build_main_prompt(session.repo_url, form=form)
            for form in (QA_FORM, ANALYSIS_FORM, PLAN_FORM, REVIEW_FORM, ORCHESTRATE_FORM, ORCHESTRATE_FORM)
        ]

    def test_rework_and_coding_use_the_same_assembly(self, monkeypatch):
        """rework 就是 coding 形态：两次装配的形态一致（只是各建一份，不再共用一份实例）。"""
        manager, calls = _manager(monkeypatch)
        runtime, session = _runtime(), _session()
        coding = manager.get_agent(runtime, session, profiles_module.profile_for("coding"))
        rework = manager.get_agent(runtime, session, profiles_module.profile_for("rework"))

        assert coding is not rework
        assert [call["model"].role for call in calls] == [ORCHESTRATE_FORM, ORCHESTRATE_FORM]

    def test_qa_and_analysis_each_get_their_own_agent(self, monkeypatch):
        """装配相同但提示词不同：共用一个键会让后一轮接到前一轮的角色段。"""
        manager, calls = _manager(monkeypatch)
        runtime, session = _runtime(), _session()
        qa = manager.get_agent(runtime, session, profiles_module.profile_for("qa"))
        analysis = manager.get_agent(runtime, session, profiles_module.profile_for("analysis"))
        assert qa is not analysis
        assert [c["system_prompt"] for c in calls] == [
            build_main_prompt(session.repo_url, form=QA_FORM),
            build_main_prompt(session.repo_url, form=ANALYSIS_FORM),
        ]

    def test_switching_intent_builds_a_second_agent(self, monkeypatch):
        manager, calls = _manager(monkeypatch)
        runtime, session = _runtime(), _session()
        readonly = manager.get_agent(runtime, session, profiles_module.profile_for("qa"))
        coding = manager.get_agent(runtime, session, profiles_module.profile_for("coding"))
        assert readonly is not coding
        assert [call["model"].role for call in calls] == [QA_FORM, ORCHESTRATE_FORM]
        assert len(calls) == 2

    def test_readonly_agent_gets_pr_read_tools_but_no_write_side(self, monkeypatch):
        manager, calls = _manager(monkeypatch)
        manager.get_agent(_runtime(), _session(), profiles_module.profile_for("qa"))
        (kwargs,) = calls
        # fetch_url 是 manager 里真实构造的工具，名字就叫 `fetch_url`
        names = _names(kwargs["tools"])
        assert names == [
            "web_search_tool",
            "fetch_url",
            "get_pr_info",
            "list_pr_comments",
            # 只读形态也有出口：手上没有那把工具时，交给有它的形态来做
            "handoffTask",
        ]
        assert not set(names) & set(_WRITE_TOOLS)
        assert kwargs["subagents"] == []
        assert "能力边界（强要求）" in kwargs["system_prompt"]

    def test_full_agent_gets_the_whole_crew(self, monkeypatch):
        manager, calls = _manager(monkeypatch)
        manager.get_agent(_runtime(), _session(), profiles_module.profile_for("coding"))
        (kwargs,) = calls
        assert [s["name"] for s in kwargs["subagents"]] == [
            "planner",
            "coder",
            "reviewer",
        ]
        assert "save_repo_memory" in _names(kwargs["tools"])

    def test_plan_and_review_agents_have_no_subagent_at_all(self, monkeypatch):
        """两者都由主控自己干：挂了子智能体反而会把「自己出方案 / 自己审」推回派发。"""
        manager, calls = _manager(monkeypatch)
        runtime, session = _runtime(), _session()
        manager.get_agent(runtime, session, profiles_module.profile_for("plan"))
        manager.get_agent(runtime, session, profiles_module.profile_for("review"))
        assert [kwargs["subagents"] for kwargs in calls] == [[], []]

    def test_plan_agent_gets_the_plan_tool_but_not_the_write_side(self, monkeypatch):
        manager, calls = _manager(monkeypatch)
        manager.get_agent(_runtime(), _session(), profiles_module.profile_for("plan"))
        (kwargs,) = calls
        names = _names(kwargs["tools"])
        # 自己出方案就得自己落库（含增删改查）；PR 只读（开工前查未合并 PR 决定放不放行）
        assert "storePlan" in names
        assert "deletePlan" in names
        assert "get_pr_info" in names
        # 出方案轮不写记忆：正文只在 coding / review / analysis 收尾时写
        assert "save_repo_memory" not in names
        assert "create_pr" not in names
        assert "comment_pr" not in names

    def test_analysis_agent_writes_the_memory_but_nothing_else(self, monkeypatch):
        """分析轮：拿到记忆写回，但仓库与 PR 都还是只读（写根为空，只有 tmp 落中间文件）。"""
        manager, calls = _manager(monkeypatch)
        manager.get_agent(_runtime(), _session(), profiles_module.profile_for("analysis"))
        (kwargs,) = calls
        names = _names(kwargs["tools"])
        assert "save_repo_memory" in names, "分析结论要沉淀进记忆"
        for name in ("create_pr", "close_pr", "comment_pr", "storePlan", "task"):
            assert name not in names, name
        assert "这一轮唯一的一处写入" in kwargs["system_prompt"]

    def test_review_agent_comments_and_writes_the_memory_only(self, monkeypatch):
        manager, calls = _manager(monkeypatch)
        manager.get_agent(_runtime(), _session(), profiles_module.profile_for("review"))
        (kwargs,) = calls
        names = _names(kwargs["tools"])
        assert "comment_pr" in names
        assert "save_repo_memory" in names, "审查结论收尾时写回"
        assert "storePlan" not in names
        assert "create_pr" not in names


class _RecordingFake(GenericFakeChatModel):
    """假模型：记下 ``bind_tools`` 收到的那份清单 —— 那正是模型能看见的工具。

    provider 报 ``openai``：主程序就是按这个 provider 名关掉 deepagents 默认自动添加的
    ``general-purpose`` 子智能体（``agent/graph.py``），不走到同一条分支上就测不到真行为
    ——「一个子智能体都不给」时它反而会塞回一个通用的。
    """

    seen: ClassVar[list[str]] = []
    task_description: ClassVar[str] = ""

    def _get_ls_params(self, **kwargs: object) -> dict[str, str]:
        return {"ls_provider": "openai"}

    def bind_tools(self, tools: object, **kwargs: object) -> _RecordingFake:
        for tool in tools:  # type: ignore[union-attr]
            name = getattr(tool, "name", None)
            type(self).seen.append(str(name))
            if name == "task":
                type(self).task_description = str(getattr(tool, "description", ""))
        return self

    def bind(self, **kwargs: object) -> _RecordingFake:
        return self


_OFFERED = re.compile(r"^- (\S+?): ", re.MULTILINE)


def _offered_agents(task_description: str) -> list[str]:
    """`task` 描述里的「可派子智能体」清单（按 `- 名字: ` 的行首形态取）。"""
    return _OFFERED.findall(task_description)


def _graph_safe_model_for(role: str) -> GenericFakeChatModel:
    """**真建图**时用的模型工厂。

    deepagents 会从子智能体的模型实例上取标识（``model_name`` / provider）去查
    harness profile，所以这里必须给真的 chat model —— 装配层的用例塞
    ``SimpleNamespace`` 就够，一到建图那步就会崩在 ``spec.count`` 上。
    """
    return GenericFakeChatModel(messages=iter([AIMessage(content="ok")]))


async def _bound_tools(intent: str) -> tuple[list[str], str]:
    """装配该意图的主智能体，跑一轮，取回「模型看见的工具名」与 `task` 的描述。"""
    _RecordingFake.seen, _RecordingFake.task_description = [], ""
    profile = profiles_module.profile_for(intent)
    agent = build_agent(
        model=_RecordingFake(messages=iter([AIMessage(content="ok")])),
        backend=None,  # type: ignore[arg-type]
        tools=[],
        store=None,  # type: ignore[arg-type]
        checkpointer=None,  # type: ignore[arg-type]
        system_prompt="SYS",
        policy=policy_for(profile.form),
        subagents=build_subagents(
            tools_by_role={role: [] for role in profile.subagents},
            model_for=_graph_safe_model_for,
            policies={role: policy_for(role) for role in profile.subagents},
        ),
        skills=None,
    )
    await agent.ainvoke({"messages": [{"role": "user", "content": "hi"}]})
    return list(_RecordingFake.seen), _RecordingFake.task_description


class TestAssembledToolList:
    """装配到模型上的真实工具清单 —— 提示词说「你没有 task」就得真的没有。

    只按 ``- <名字>: `` 这个行首形态取「可派清单」：`task` 描述里还带一大段示例
    （``"content-reviewer"`` / ``"greeting-responder"`` 之类），按子串断言会误命中。
    """

    async def test_no_single_agent_round_has_the_task_tool(self):
        """plan / review 这轮主控自己干：`task` 在清单里就等于留了个误派的口子。"""
        for intent in ("qa", "analysis", "plan", "review"):
            names, description = await _bound_tools(intent)
            assert "task" not in names, intent
            assert description == "", intent

    async def test_coding_round_offers_exactly_the_three_roles(self):
        """「恰好」是重点：deepagents 会自动补一个 ``general-purpose``（挂全量主工具），
        漏关它这里就会多出一项。"""
        names, description = await _bound_tools("coding")
        assert "task" in names
        assert _offered_agents(description) == ["planner", "coder", "reviewer"]

    async def test_rework_round_offers_the_same_crew_as_coding(self):
        """rework 复用 coding 的装配：同样有 `task`、同样三个角色。

        这一轮**不派** planner 是提示词里的事（改什么已由审查结论定下来），不是把工具撤掉
        —— 撤掉就得给它单开一种形态。
        """
        names, description = await _bound_tools("rework")
        assert "task" in names
        assert _offered_agents(description) == ["planner", "coder", "reviewer"]
