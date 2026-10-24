"""子智能体定义：planner / coder / reviewer。

装配哪些角色由 profile 决定（``agent/profiles.py``）—— qa / analysis 一个都不给，
主智能体于是连 `task` 工具都没有（deepagents 只在子智能体非空时才加它）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from deepagents.middleware.subagents import SubAgent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ai_coding_harness.agent.middleware import (
    BoundaryMiddleware,
    FallbackMiddleware,
    HistoryRepairMiddleware,
    LoopGuardMiddleware,
    MemoryMiddleware,
    ModelRedactionMiddleware,
    ReadGuardMiddleware,
    RedactionMiddleware,
    StreamUsageMiddleware,
    ToolCallIdMiddleware,
    ToolErrorGuardMiddleware,
)
from ai_coding_harness.agent.prompts import CODER_PROMPT, PLANNER_PROMPT, REVIEWER_PROMPT
from ai_coding_harness.safety.permissions import CommandSafety
from ai_coding_harness.safety.policy import AgentPolicy

# 固定顺序（deepagents 会照此把清单写进 `task` 工具描述，顺序稳定便于对照）
_ROLES: dict[str, dict[str, str]] = {
    "planner": {
        "description": "制定分步骤实施方案（只出方案，不写代码、不提 PR）。需要规划实现方案时派给它。",
        "prompt": PLANNER_PROMPT,
    },
    "coder": {
        "description": "按 planner 的方案实施代码、测试并创建 PR；按用户指定要修改的审查问题继续修复；纯文档任务可不经方案直接完成并创建 PR。",
        "prompt": CODER_PROMPT,
    },
    "reviewer": {
        "description": "对 PR / diff 做只读代码审查，并把结论评论到 PR。",
        "prompt": REVIEWER_PROMPT,
    },
}

ROLE_NAMES: tuple[str, ...] = tuple(_ROLES)


def build_subagents(
    *,
    tools_by_role: Mapping[str, list[BaseTool]],
    model_for: Callable[[str], BaseChatModel],
    policies: Mapping[str, AgentPolicy],
    safety: CommandSafety | None = None,
    fallbacks: Mapping[str, FallbackMiddleware] | None = None,
    memory_factory: Callable[[str], MemoryMiddleware] | None = None,
    plan_recovery_factory: Callable[[str], AgentMiddleware | None] | None = None,
    summarization_for: Callable[[str, BaseChatModel], AgentMiddleware | None] | None = None,
) -> list[SubAgent]:
    """按角色装配子智能体：只产出 ``tools_by_role`` 里给了条目（含空表）的角色。

    每个角色带**自己的** ``model``（``model_for(角色名)``，路由表在 ``llm.py``）——
    不给就继承主智能体，而主智能体在 coding 形态下是 glm：coding 那三个角色各有各的
    模型（planner 出方案用 glm、coder 写码用 deepseek、reviewer 审查用 kimi），继承
    等于把三家的活派给同一家。文件工具（ls/read/write/edit/glob/grep）与 execute 由
    deepagents 中间件注入，无需在此列出。

    ``middleware`` 必须逐个列出：主智能体的自定义中间件**不会**下传给子智能体
    （deepagents 只给它们自带默认栈 + 各自 spec 里的 middleware）。两条脱敏出口
    （工具返回值、模型输出）都不能漏 —— 读写沙箱、跑命令的正是这三个。
    工具调用自洽的三个钩子同理：子智能体自己也跑工具，也会撞上同一个空 id 历史。
    循环守卫（``guard.py``）也一样 —— 原地打转的多半正是拿着 ``execute`` 的那个角色。
    ``policies`` 同理必须按角色各给一份：边界是**每个角色自己的**，而主控那份下不来。

    ``policies[role]`` 缺失就抛 ``KeyError``（不做默认）：默认成「无边界」等于悄悄
    放开一个角色，而这类漏挂在测试里必须立刻看得见。

    ``fallbacks`` 是**按角色**的兜底中间件（``None`` ＝ 一个都没有）；缺键＝该角色没有
    兜底，与 ``policies`` 不同这里不报错 —— 「没有兜底」是合法状态（角色本来就用兜底
    那一家时就是这样，见 ``llm.build_fallback``），而「没有边界」不是。

    ``memory_factory`` 是仓库记忆中间件的**工厂**（``None`` ＝ 不给记忆，单测里会这么用）：
    这里要的是「每次调用现给一个实例」，不是「传一个共用的进来」。同一回合主控可能一次
    发几个 ``task`` 出去，那几个子智能体是并发跑的，而记忆中间件按轮把要注入的那块存在
    ``self`` 上 —— 共用一个实例就会互相覆盖，注入到谁的系统提示里都说不准。给的是工厂
    而不是「已经建好的实例列表」，也是为了让装配点（``session/manager.py``）不必知道
    有几个角色。子智能体这份不带闸门：它们本来就不写记忆，为一次注入多查一趟 Gitee 不划算，
    而且 coder 刚建完 PR、reviewer 再查一遍就会把自己挡在门外。

    ``summarization_for`` 是压缩中间件（2026-10-15 起我们自己构造那一份，上游那份已在
    ``agent/graph.py`` 里按类排掉 —— **所以这里不传就是「这个角色不压缩」**，不会退回上游默认）。
    它收 ``(角色, 该角色的模型实例)``：压缩要按**那个模型自己的窗口**算触发线，而窗口是挂在
    模型实例的 ``profile`` 上的；顺带也避免为它再建一个同配置的模型实例。到压缩线时的
    应急裁剪也在这份中间件里，**不再单独挂常规剪裁**。
    """
    fallbacks = fallbacks or {}
    specs: list[SubAgent] = []
    for role, spec in _ROLES.items():
        if role not in tools_by_role:
            continue
        # 模型每角色只建一次：压缩中间件要用**同一个实例**（窗口挂在它的 profile 上）
        model = model_for(role)
        summarization = None if summarization_for is None else summarization_for(role, model)
        plan_recovery = (
            plan_recovery_factory(role) if plan_recovery_factory is not None else None
        )
        specs.append(
            {
                "name": role,
                "description": spec["description"],
                "system_prompt": spec["prompt"],
                "tools": list(tools_by_role[role]),
                "model": model,
                "middleware": [
                    # 与主控同序：边界在最外侧，记忆紧随其后
                    BoundaryMiddleware(policies[role], safety),
                    # planner / coder 也要在进入与正常退出时回收 executing 方案。
                    *([plan_recovery] if plan_recovery is not None else []),
                    # 各角色一份新实例：它们可能在同一回合里并发跑（见上面的 ``memory_factory``）
                    *([memory_factory(role)] if memory_factory is not None else []),
                    # 读文件红线：policies / project 不许整文件读（与主控同一份规则）
                    ReadGuardMiddleware(),
                    # 循环守卫按角色分账（``agent=role``）：``TurnState`` 是每轮一份、经
                    # configurable 注入的，而上游会把它原样传进子智能体 —— 不署名的话三个
                    # 角色与主控共用一本账，一个角色用满额度，下一个角色第一次调用就被中止
                    LoopGuardMiddleware(agent=role),
                    ToolCallIdMiddleware(),
                    RedactionMiddleware(),
                    ModelRedactionMiddleware(),
                    ToolErrorGuardMiddleware(agent=role),
                    HistoryRepairMiddleware(),
                    # 压缩（含压缩线处的应急裁剪）仍在兜底外面
                    *([summarization] if summarization is not None else []),
                    # 兜底放在倒数第二层：它重试时不再重跑上面这几层
                    *([fallbacks[role]] if role in fallbacks else []),
                    # 与主控同位置：流式用量开关的 400 重试在兜底内侧
                    StreamUsageMiddleware(agent=role),
                ],
            }
        )
    return specs
