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
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from ai_coding_harness.agent.middleware import (
    BoundaryMiddleware,
    DelegationGateMiddleware,
    FallbackMiddleware,
    HistoryRepairMiddleware,
    IntentMiddleware,
    LoopGuardMiddleware,
    MemoryMiddleware,
    ModelRedactionMiddleware,
    RedactionMiddleware,
    StreamUsageMiddleware,
    ToolCallIdMiddleware,
    ToolErrorGuardMiddleware,
)
from ai_coding_harness.observability.logging import MAIN_AGENT, MAIN_AGENT_NAME
from ai_coding_harness.safety.permissions import CommandSafety
from ai_coding_harness.safety.policy import AgentPolicy

# 关闭 deepagents 默认自动添加的 `general-purpose` 子智能体 —— 本项目固定「一主三从」。
# 以 provider 为 key 注册，覆盖所有 openai 兼容模型（DeepSeek / Kimi）。
#
# 顺带换掉上游的 `PatchToolCallsMiddleware`：它的判据（全历史 tool 响应 id 集合）会被
# 空 id 骗过，见 `middleware/tool_integrity.py` 的模块说明。本项目自带的
# `HistoryRepairMiddleware` 是它的超集 —— 判据更严，还认 None 与空串。
#
# 第三件：排掉上游自带的压缩中间件，换成我们自己构造的那一份（`agent/summarization.py`）——
# 上游工厂的 trigger / keep / trim_tokens_to_summarize 三个默认值都不合用，而且都没暴露成
# 参数；位置也得由我们定（要挂在剪裁的**内侧**，它才数得到「真要发出去的那一份」）。
# 排除是**按类精确匹配**的，所以我们那个子类实例不会被一起排掉。上游若改了这份装配，
# `_verify_excluded_middleware_coverage` 会在建 agent 时直接 ValueError —— 当场红，
# 不会静默变成「两份压缩同时挂着」。
register_harness_profile(
    "openai",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_middleware=frozenset({PatchToolCallsMiddleware, SummarizationMiddleware}),
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
    summarization: AgentMiddleware | None = None,
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

    ``summarization`` 是压缩中间件（``None`` ＝ 关掉压缩，见 ``summarization.build_summarization``）。
    上游那份已在模块级被 ``excluded_middleware`` 排掉，所以这里**必须自己传一份**，
    漏传就是「这个智能体不压缩」而不是「退回上游默认」。它同时负责压缩线处的应急裁剪
    （``CONTEXT_PRETRIM``），所以**不要再单独挂常规剪裁中间件**。
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
            # 主控派子智能体之前必须先落一份清单。放在循环计数外侧：被它拦下的 `task`
            # 不该占重复额度，补完 `write_todos` 后第一次派发仍是第 1 次
            DelegationGateMiddleware(),
            # 紧跟着数重复：被上面拒掉的调用不计（模型每圈拿到的是明确的拒绝理由，
            # 那本身就是新信息），数的是「真的执行了、结果还一样」的那种原地打转
            LoopGuardMiddleware(agent=MAIN_AGENT),
            ToolCallIdMiddleware(),
            IntentMiddleware(),
            RedactionMiddleware(),
            ModelRedactionMiddleware(),
            ToolErrorGuardMiddleware(agent=MAIN_AGENT),
            HistoryRepairMiddleware(),
            # 压缩（摘要）挂在最内侧、兜底的外面：兜底重试不该把压缩再跑一遍。
            # 到压缩线时它先做一次应急工具裁剪（只保当前步骤），剪完低于线就不压。
            *([summarization] if summarization is not None else []),
            # 兜底放在倒数第二层：它重试时不再重跑上面这几层
            *([fallback] if fallback is not None else []),
            # 比兜底还内一层：网关不认 stream_options 的 400 在这里就地重试，
            # 不让兜底把它误判成「主导模型挂了」而切模型。
            StreamUsageMiddleware(agent=MAIN_AGENT),
        ],
        subagents=subagents,
        backend=backend,
        store=store,
        checkpointer=checkpointer,
        skills=skills,
        # 这个名字会成为主控那一路模型调用的 ``lc_agent_name``：运行日志靠它标执行主角、
        # token 账靠它认「这一笔是主控」。只在这里定义一处，别处引用常量
        # （见 ``observability.logging.MAIN_AGENT_NAME``）。
        name=MAIN_AGENT_NAME,
    )
