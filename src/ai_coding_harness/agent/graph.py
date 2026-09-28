"""Agent assembly — build the main deep agent (with subagents)."""

from __future__ import annotations

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import SubAgent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from ai_coding_harness.agent.middleware import (
    BoundaryMiddleware,
    FallbackMiddleware,
    HistoryRepairMiddleware,
    IntentMiddleware,
    LoopGuardMiddleware,
    MemoryMiddleware,
    ModelRedactionMiddleware,
    RedactionMiddleware,
    ToolCallIdMiddleware,
    ToolErrorGuardMiddleware,
)
from ai_coding_harness.safety.permissions import CommandSafety
from ai_coding_harness.safety.policy import AgentPolicy

# 关闭 deepagents 默认自动添加的 `general-purpose` 子智能体 —— 本项目固定「一主三从」。
# 以 provider 为 key 注册，覆盖所有 openai 兼容模型（DeepSeek / Kimi）。
#
# 顺带换掉上游的 `PatchToolCallsMiddleware`：它的判据（全历史 tool 响应 id 集合）会被
# 空 id 骗过，见 `middleware/tool_integrity.py` 的模块说明。本项目自带的
# `HistoryRepairMiddleware` 是它的超集 —— 判据更严，还认 None 与空串。
register_harness_profile(
    "openai",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_middleware=frozenset({PatchToolCallsMiddleware}),
    ),
)


def build_agent(
    *,
    model: BaseChatModel,
    backend: BackendProtocol,
    tools: list[BaseTool],
    store: BaseStore,
    checkpointer: BaseCheckpointSaver,
    system_prompt: str,
    policy: AgentPolicy,
    safety: CommandSafety | None = None,
    fallback: FallbackMiddleware | None = None,
    memory: MemoryMiddleware | None = None,
    subagents: list[SubAgent] | None = None,
    skills: list[str] | None = None,
) -> CompiledStateGraph:
    """装配主智能体（deep agent）。

    ``backend`` 是当前会话的混合后端（含该会话沙箱的 OpenSandboxBackend）；
    ``system_prompt`` 是主智能体提示词（BASE + 主角色）；``tools`` 是主智能体工具；
    ``subagents`` 是子智能体定义（planner / coder / reviewer，经 ``task`` 工具调用）；
    ``skills`` 是 skill 目录路径（backend 内路径，经 SkillsMiddleware 加载）。

    ``policy`` 是**这个形态**的活动范围（``safety/policy.py``），必填 —— 漏传等于主控
    没有边界，而这正是本次要堵的；``safety`` 是全局底线，与 ``OpenSandboxBackend``
    用的是同一份规则。

    ``fallback`` 是兜底中间件（``None`` ＝ 本形态没有兜底，见 ``llm.build_fallback``）。
    它挂在这批的**最内侧**：降级重试跑的是它自己的内层 handler，不该把我们的边界与脱敏
    前处理再跑一遍；反过来，外层的 ``ModelRedactionMiddleware`` 因此永远看到「最终赢的
    那个回答」，一次脱敏即覆盖所有模型来源。

    ``memory`` 是仓库记忆中间件（``None`` ＝ 这个智能体不看记忆，单测里会这么用）。
    所有智能体都该挂一份：它把记忆全文注入系统消息。主控形态那份还会在开工前按 Gitee
    实况刷新记忆（``REFRESH_FORMS``），其中 plan / coding 两个形态还有未合入的 PR 就拦下
    本轮（``BLOCKING_FORMS``）。
    """
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[
            # 放在我们这批的最外侧：越界的调用要在别的中间件动手之前就被拒掉
            BoundaryMiddleware(policy, safety),
            # 紧接着的这块答的是「本轮看不看得到仓库记忆」：闸门那条路在 before_agent
            # 就抛 TurnStop 结束本轮，它一响，下面这些中间件与模型都不会被碰到
            *([memory] if memory is not None else []),
            # 紧跟着数重复：被上面拒掉的调用不计（模型每圈拿到的是明确的拒绝理由，
            # 那本身就是新信息），数的是「真的执行了、结果还一样」的那种原地打转
            LoopGuardMiddleware(),
            ToolCallIdMiddleware(),
            IntentMiddleware(),
            RedactionMiddleware(),
            ModelRedactionMiddleware(),
            ToolErrorGuardMiddleware(),
            HistoryRepairMiddleware(),
            # 最内侧：兜底重试不再重跑上面这几层
            *([fallback] if fallback is not None else []),
        ],
        subagents=subagents,
        backend=backend,
        store=store,
        checkpointer=checkpointer,
        skills=skills,
        name="ai-coding-harness",
    )
