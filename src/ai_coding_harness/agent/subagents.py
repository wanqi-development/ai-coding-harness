"""子智能体定义：planner / coder / reviewer。

装配哪些角色由 profile 决定（``agent/profiles.py``）—— qa / analysis 一个都不给，
主智能体于是连 `task` 工具都没有（deepagents 只在子智能体非空时才加它）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from deepagents.middleware.subagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ai_coding_harness.agent.middleware import (
    BoundaryMiddleware,
    FallbackMiddleware,
    HistoryRepairMiddleware,
    LoopGuardMiddleware,
    MemoryMiddleware,
    ModelRedactionMiddleware,
    RedactionMiddleware,
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
        "description": "按 planner 的方案实施代码、测试、更新记忆并创建 PR；以及按用户指定要修改的审查问题继续修复。",
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
    memory_factory: Callable[[], MemoryMiddleware] | None = None,
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
    """
    fallbacks = fallbacks or {}
    return [
        {
            "name": role,
            "description": spec["description"],
            "system_prompt": spec["prompt"],
            "tools": list(tools_by_role[role]),
            "model": model_for(role),
            "middleware": [
                # 与主控同序：边界在最外侧，记忆紧随其后
                BoundaryMiddleware(policies[role], safety),
                # 各角色一份新实例：它们可能在同一回合里并发跑（见上面的 ``memory_factory``）
                *([memory_factory()] if memory_factory is not None else []),
                LoopGuardMiddleware(),
                ToolCallIdMiddleware(),
                RedactionMiddleware(),
                ModelRedactionMiddleware(),
                ToolErrorGuardMiddleware(),
                HistoryRepairMiddleware(),
                # 最内侧：兜底重试不再重跑上面这几层
                *([fallbacks[role]] if role in fallbacks else []),
            ],
        }
        for role, spec in _ROLES.items()
        if role in tools_by_role
    ]
