"""Tool registry — 按智能体（主 / 子）分配自定义工具。"""

from __future__ import annotations

from deepagents.backends.protocol import BackendProtocol
from langchain_core.tools import BaseTool

from ai_coding_harness.agent.profiles import (
    ORCHESTRATE_FORM,
    PLAN_FORM,
    QA_FORM,
)
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database
from ai_coding_harness.tools.git import RepoSpec, build_pr_tools
from ai_coding_harness.tools.handoff import HandoffContext, build_handoff_tools
from ai_coding_harness.tools.memory import MemoryContext, build_memory_tools
from ai_coding_harness.tools.plan import PlanContext, build_plan_tools

# 形态 → 主控除联网工具外还能拿到什么。形态名与 ``agent/profiles.py`` 一一对应：
# 那里定「挂哪些子智能体」、这里定「给哪些主工具」、``prompts._MAIN_PROMPTS`` 定「用哪套
# 角色段」—— 三处必须同时改，只改一处就会配出「有工具但提示词不让用」这类错配。
_FORM_TOOLS: dict[str, tuple[str, ...]] = {
    # 问答：读仓库、读记忆走 `read_file`（本就是只读工具，无需在此列出）。PR 的**读**工具
    # 要给 —— 「有哪些未关闭的 PR」「当前代码在 PR 里还是已合并」是它的常见问法；**写**工具
    # （create_pr / close_pr / comment_pr / save_repo_memory / storePlan）一概不给。
    # 记忆正文只在 coding / rework 收尾写，qa 已合并原 analysis，但不再有写记忆权限。
    # 看不见比提示词里劝阻有效。
    QA_FORM: ("get_pr_info", "list_pr_comments"),
    # 出方案：主控自己出方案并 storePlan 落库；**方案对象本身的增删改查全归它**
    # （getPlan / updatePlan / updatePlanSteps / deletePlan，2026-09-25 加）——
    # 别的形态没有写方案的工具（qa），要么只管实施进度
    # （coder 的 updatePlanStatus / updatePlanStep）。PR 只读：**开工前**要查未合并 PR
    # 决定放不放行（闸门在 middleware/memory.py，不是靠提示词）。
    # **没有 save_repo_memory**：出方案阶段还没动代码，记忆正文的写入只在
    # coding / rework 收尾时发生。
    PLAN_FORM: (
        "get_pr_info",
        "storePlan",
        "getPlan",
        # 只要判「它还在不在、还能不能实施」时用这个：不拉一万字的正文（2026-10-13 加）
        "planExists",
        "updatePlan",
        "updatePlanSteps",
        "deletePlan",
    ),
    # 编码：全流程。需要 `comment_pr`（审查结论由主控呈给用户、也由它按用户指示回评 PR）；
    # 没有 `merge_pr`（合并由人工在 Gitee 页面上处理）；记忆统一由它 `save_repo_memory` 写回。
    # **只读的 `get_unexecuted_plan`**（2026-09-27 加）：用户明确说「直接做」时，主控自己先查
    # 有没有可执行的方案 —— 有就直接派 coder，没有才先派 planner（省掉「派了 coder 才发现
    # 没方案、再改派 planner」那一圈）。**方案的增删改仍归 planner**（storePlan / updatePlan /
    # updatePlanSteps / deletePlan 不在下表里）。
    ORCHESTRATE_FORM: (
        "get_pr_info",
        "list_pr_comments",
        "comment_pr",
        "close_pr",
        "get_unexecuted_plan",
        # 已知 plan_id 时先判存在性再派 coder：一次只回布尔的查询，比拉全文便宜
        #（2026-10-13 用户口径）
        "planExists",
        "save_repo_memory",
    ),
}

#: 需要 ``build_plan_tools`` 那口池子的工具名：出方案轮要 ``storePlan``，编码轮要
#: ``get_unexecuted_plan`` 与 ``planExists``（两个都是只读查询）。池子整口建出来，再按
#: ``wanted`` 挑 —— 表里写什么才能拿到什么。
_PLAN_TOOL_NAMES: frozenset[str] = frozenset(
    {"storePlan", "get_unexecuted_plan", "planExists"}
)


def _by_name(tools: list[BaseTool], name: str) -> list[BaseTool]:
    return [t for t in tools if t.name == name]


def build_main_tools(
    settings: Settings,
    web_search_tool: BaseTool,
    fetch_url_tool: BaseTool,
    repo: RepoSpec,
    backend: BackendProtocol,
    *,
    form: str = ORCHESTRATE_FORM,
    database: Database | None = None,
    plan_ctx: PlanContext | None = None,
    handoff: HandoffContext | None = None,
) -> list[BaseTool]:
    """主智能体工具：联网 + 该形态该有的那几个（表见 ``_FORM_TOOLS``）。

    子智能体只回报、不写仓库记忆，所以 ``save_repo_memory`` 只在主控手里 ——
    在 coding 与 plan 两个形态下都是。``storePlan`` 同理：多智能体形态下方案由
    ``planner`` 落库，单智能体的 plan 形态下由主控自己落库（否则方案面板与
    ``get_unexecuted_plan`` 都取不到东西）。

    ``handoff`` 给了就追加 ``handoffTask``（见 ``tools/handoff.py``）。它**不属于**
    ``_FORM_TOOLS``：那张表裁的是「这个形态有什么能力」，而交接恰恰是**所有形态都需要**
    的出口 —— 形态裁掉的能力模型看不见，没有出口就只能对用户说「我做不了」。
    不给 ``handoff`` 就不挂（脚本与单测直接建工具时不必凑一个会话上下文）。
    """
    wanted = _FORM_TOOLS[form]
    online = [web_search_tool, fetch_url_tool]

    pool: dict[str, BaseTool] = {t.name: t for t in build_pr_tools(settings, repo)}
    if "save_repo_memory" in wanted:
        # PR 查询与状态对账在轮首 / coding/rework 轮末完成；此工具只合并并落盘正文。
        pool.update(
            {
                t.name: t
                for t in build_memory_tools(
                    MemoryContext(
                        owner=repo.owner,
                        repo=repo.repo,
                        backend=backend,
                        prefix=settings.memories_prefix,
                    )
                )
            }
        )
    if _PLAN_TOOL_NAMES & set(wanted):
        pool.update(
            {t.name: t for t in build_plan_tools(database, plan_ctx)}
        )
    handoff_tools = build_handoff_tools(handoff) if handoff is not None else []
    return online + [pool[name] for name in wanted] + handoff_tools


def build_subagent_tools(
    role: str,
    settings: Settings,
    web_search_tool: BaseTool,
    fetch_url_tool: BaseTool,
    repo: RepoSpec,
    database: Database | None = None,
    plan_ctx: PlanContext | None = None,
) -> list[BaseTool]:
    """子智能体工具：

    - planner: web_search_tool + fetch_url_tool + storePlan
    - coder: get_unexecuted_plan + updatePlanStatus + updatePlanStep + create_pr +
      list_pr_comments（**只读**评论，用来自取审查原文；``comment_pr`` 仍然不给，
      PR 上的回复统一由主智能体负责）
    - reviewer: comment_pr + list_pr_comments + get_pr_info
    """
    pr_tools = build_pr_tools(settings, repo)
    plan_tools = build_plan_tools(database, plan_ctx) if (database and plan_ctx) else []

    if role == "planner":
        return [web_search_tool, fetch_url_tool] + _by_name(plan_tools, "storePlan")
    if role == "coder":
        return (
            _by_name(plan_tools, "get_unexecuted_plan")
            + _by_name(plan_tools, "updatePlanStatus")
            + _by_name(plan_tools, "updatePlanStep")
            + _by_name(pr_tools, "create_pr")
            + _by_name(pr_tools, "list_pr_comments")
        )
    if role == "reviewer":
        return (
            _by_name(pr_tools, "comment_pr")
            + _by_name(pr_tools, "list_pr_comments")
            + _by_name(pr_tools, "get_pr_info")
        )
    return []
