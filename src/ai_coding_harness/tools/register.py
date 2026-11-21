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
from ai_coding_harness.tools.memory import (
    QA_KINDS,
    SAVE_KINDS,
    MemoryContext,
    build_memory_tools,
)
from ai_coding_harness.tools.plan import PlanContext, build_plan_tools
from ai_coding_harness.tools.repo_sync import RepoSyncContext, build_repo_sync_tools
from ai_coding_harness.tools.review import ReviewContext, build_review_tools
from ai_coding_harness.tools.work_task import WorkTaskContext, build_work_task_tools

# 形态 → 主控除联网工具外还能拿到什么。形态名与 ``agent/profiles.py`` 一一对应：
# 那里定「挂哪些子智能体」、这里定「给哪些主工具」、``prompts._MAIN_PROMPTS`` 定「用哪套
# 角色段」—— 三处必须同时改，只改一处就会配出「有工具但提示词不让用」这类错配。
_FORM_TOOLS: dict[str, tuple[str, ...]] = {
    # 问答：可写经过代码验证的仓库分析文档，但不能写 PR、方案或 coding 任务状态。
    # QA 的 save_repo_memory 还会按 allowed_kinds 限制：可写有证据的 gaps / risks，
    # 但不能写 decision。
    QA_FORM: (
        "get_pr_info",
        "list_pr_comments",
        "read_repo_memory",
        "save_repo_memory",
        "sync_repo_code",
    ),
    # 出方案：主控自己出方案并 storePlan 落库；**方案对象本身的增删改查全归它**
    # （getPlan / updatePlan / updatePlanSteps / deletePlan，2026-09-25 加）——
    # 别的形态没有写方案的工具（qa），要么只管实施进度
    # （coder 的 updatePlanStatus / updatePlanStep）。PR 只读：**开工前**要查未合并 PR
    # 决定放不放行（闸门在 middleware/memory.py，不是靠提示词）。
    # **没有 save_repo_memory**：plan 严格只读，不迁移、不更新、不写正文。
    PLAN_FORM: (
        "get_pr_info",
        "storePlan",
        "getPlan",
        # 只要判「它还在不在、还能不能实施」时用这个：不拉一万字的正文（2026-10-13 加）
        "planExists",
        "updatePlan",
        "updatePlanSteps",
        "deletePlan",
        "read_repo_memory",
    ),
    # 编码：全流程。需要 `comment_pr`；没有 `merge_pr`（合并由人工处理）；
    # 记忆统一由文档级 `save_repo_memory` 写回。
    # **只读的 `get_unexecuted_plan`**（2026-09-27 加）：用户明确说「直接做」时，主控自己先查
    # 有没有可执行的方案 —— 有就直接派 coder，没有才先派 planner（省掉「派了 coder 才发现
    # 没方案、再改派 planner」那一圈）。**方案的增删改仍归 planner**（storePlan / updatePlan /
    # updatePlanSteps / deletePlan 不在下表里）。
    ORCHESTRATE_FORM: (
        "get_pr_info",
        "list_pr_comments",
        "create_pr",
        "comment_pr",
        "close_pr",
        "get_unexecuted_plan",
        # 已知 plan_id 时先判存在性再派 coder：一次只回布尔的查询，比拉全文便宜
        #（2026-10-13 用户口径）
        "planExists",
        "save_repo_memory",
        "read_repo_memory",
        "getActiveWorkTask",
        "openWorkTask",
        "recordTaskSubmission",
        "updateWorkTask",
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
    review_ctx = (
        ReviewContext(
            database=database,
            session_id=plan_ctx.session_db_id,
            platform=repo.platform.value,
            owner=repo.owner,
            repo=repo.repo,
            repo_dir=f"{settings.sandbox_project_dir.rstrip('/')}/{repo.repo}",
            backend=backend,
            plan_id=None,
        )
        if database is not None and plan_ctx is not None
        else None
    )
    work_task_ctx = (
        WorkTaskContext(
            database=database,
            session_id=plan_ctx.session_db_id,
            platform=repo.platform.value,
            owner=repo.owner,
            repo=repo.repo,
        )
        if database is not None and plan_ctx is not None
        else None
    )

    pool: dict[str, BaseTool] = {
        t.name: t
        for t in build_pr_tools(
            settings,
            repo,
            database=database,
            session_id=plan_ctx.session_db_id if plan_ctx is not None else None,
            work_task_ctx=work_task_ctx,
        )
    }
    memory_kinds = (
        QA_KINDS
        if form == QA_FORM
        else SAVE_KINDS
        if form == ORCHESTRATE_FORM
        else frozenset()
    )
    memory_tools = build_memory_tools(
        MemoryContext(
            platform=repo.platform.value,
            owner=repo.owner,
            repo=repo.repo,
            backend=backend,
            prefix=settings.memories_prefix,
            allowed_kinds=memory_kinds,
            persist_migration=form != PLAN_FORM,
        )
    )
    pool.update({tool.name: tool for tool in memory_tools})
    if _PLAN_TOOL_NAMES & set(wanted):
        pool.update(
            {t.name: t for t in build_plan_tools(database, plan_ctx)}
        )
    if review_ctx is not None:
        pool.update({t.name: t for t in build_review_tools(review_ctx)})
    if work_task_ctx is not None:
        pool.update({t.name: t for t in build_work_task_tools(work_task_ctx)})
    if form == QA_FORM:
        pool.update(
            {
                tool.name: tool
                for tool in build_repo_sync_tools(
                    RepoSyncContext(
                        backend=backend,
                        repo_url=f"{repo.web_url}.git",
                        repo_dir=f"{settings.sandbox_project_dir.rstrip('/')}/{repo.repo}",
                    )
                )
            }
        )
    handoff_tools = build_handoff_tools(handoff) if handoff is not None else []
    return online + [pool[name] for name in wanted if name in pool] + handoff_tools


def build_subagent_tools(
    role: str,
    settings: Settings,
    web_search_tool: BaseTool,
    fetch_url_tool: BaseTool,
    repo: RepoSpec,
    database: Database | None = None,
    plan_ctx: PlanContext | None = None,
    backend: BackendProtocol | None = None,
) -> list[BaseTool]:
    """子智能体工具：

    - planner: web_search_tool + fetch_url_tool + storePlan
    - coder: get_unexecuted_plan + updatePlanStatus + updatePlanStep + list_pr_comments
      （**只读**评论；写代码、commit、push 归 coder，建 PR 归主控）
    - reviewer: comment_pr + list_pr_comments + get_pr_info
    """
    pr_tools = build_pr_tools(
        settings,
        repo,
        database=database,
        session_id=plan_ctx.session_db_id if plan_ctx is not None else None,
        work_task_ctx=(
            WorkTaskContext(
                database=database,
                session_id=plan_ctx.session_db_id,
                platform=repo.platform.value,
                owner=repo.owner,
                repo=repo.repo,
            )
            if database is not None and plan_ctx is not None
            else None
        ),
    )
    plan_tools = build_plan_tools(database, plan_ctx) if (database and plan_ctx) else []
    review_tools = (
        build_review_tools(
            ReviewContext(
                database=database,
                session_id=plan_ctx.session_db_id,
                platform=repo.platform.value,
                owner=repo.owner,
                repo=repo.repo,
                repo_dir=f"{settings.sandbox_project_dir.rstrip('/')}/{repo.repo}",
                backend=backend,
                plan_id=None,
            )
        )
        if database is not None and plan_ctx is not None and backend is not None
        else []
    )
    memory_tools = (
        build_memory_tools(
            MemoryContext(
                platform=repo.platform.value,
                owner=repo.owner,
                repo=repo.repo,
                backend=backend,
                prefix=settings.memories_prefix,
                allowed_kinds=frozenset(),
                persist_migration=False,
            )
        )
        if backend is not None
        else []
    )
    memory_reads = _by_name(memory_tools, "read_repo_memory")

    if role == "planner":
        return (
            [web_search_tool, fetch_url_tool]
            + _by_name(plan_tools, "storePlan")
            + memory_reads
        )
    if role == "coder":
        return (
            _by_name(plan_tools, "get_unexecuted_plan")
            + _by_name(plan_tools, "updatePlanStatus")
            + _by_name(plan_tools, "updatePlanStep")
            + _by_name(pr_tools, "list_pr_comments")
            + memory_reads
        )
    if role == "reviewer":
        return (
            _by_name(pr_tools, "comment_pr")
            + _by_name(pr_tools, "list_pr_comments")
            + _by_name(pr_tools, "get_pr_info")
            + review_tools
            + memory_reads
        )
    return []
