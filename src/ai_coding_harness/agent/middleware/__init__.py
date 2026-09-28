"""主智能体与子智能体的中间件。

按出口分几个模块，各自的取舍都写在模块 docstring 里：

- ``context_inject``：每次模型调用前注入当前时间与本轮意图；
- ``redaction``：凭据脱敏的两条出口 —— 工具返回值、模型输出；
- ``tool_integrity``：工具调用配对自洽 —— 空 id 归一、工具异常兜底、历史整表修复；
- ``boundary``：按智能体的活动范围 —— 隐藏越界的工具、拦下越界的调用；
- ``guard``：本轮空转刹车 —— 同一工具同一参数反复调用时先劝、再停；
- ``memory``：仓库记忆 —— 每轮开始把它注入系统提示；plan / orchestrate 主控那份还按 Gitee
  的真实 PR 状态先对账，再按**本轮意图**判闸门（plan / coding 有未合入的 PR 就拦，rework
  反过来：没有待合并的 PR 才拦）；
- ``fallback``：主导模型彻底失败时切兜底，并留一条 ``MODEL_FALLBACK`` 的痕。

这里只做重新导出，装配点（``graph.py`` / ``subagents.py``）从这里拿类并实例化。
中间件是 ``AgentMiddleware`` 子类，实例无状态，每次构建新实例即可 —— 例外是
``BoundaryMiddleware``、``FallbackMiddleware`` 与 ``MemoryMiddleware``：前者持有该智能体的
策略、中者持有该角色的角色名与兜底模型、后者按轮暂存要注入的记忆块，所以这三类
**每个角色要各 new 一个**。
"""

from __future__ import annotations

from ai_coding_harness.agent.middleware.boundary import BoundaryMiddleware
from ai_coding_harness.agent.middleware.context_inject import (
    IntentMiddleware,
    current_time_block,
    now_stamp,
)
from ai_coding_harness.agent.middleware.fallback import (
    FALLBACK_LOG_TAG,
    FallbackMiddleware,
)
from ai_coding_harness.agent.middleware.guard import (
    ABORT_REASON,
    LOOP_ABORT_MARKER,
    LoopGuardMiddleware,
    TurnState,
    TurnStop,
)
from ai_coding_harness.agent.middleware.memory import (
    BLIND_MARKER,
    BLOCKING_FORMS,
    GATE_ABORT_REASON,
    GATE_MARKER,
    MEMORY_BLOCK_TITLE,
    NO_TARGET_ABORT_REASON,
    NOTHING_TO_FIX_MARKER,
    REFRESH_FORMS,
    MemoryMiddleware,
    Refreshed,
    build_memory_middleware,
    refresh_memory,
)
from ai_coding_harness.agent.middleware.redaction import (
    ModelRedactionMiddleware,
    RedactionMiddleware,
)
from ai_coding_harness.agent.middleware.tool_integrity import (
    HistoryRepairMiddleware,
    ToolCallIdMiddleware,
    ToolErrorGuardMiddleware,
)

__all__ = [
    "ABORT_REASON",
    "BLIND_MARKER",
    "BLOCKING_FORMS",
    "FALLBACK_LOG_TAG",
    "GATE_ABORT_REASON",
    "GATE_MARKER",
    "LOOP_ABORT_MARKER",
    "MEMORY_BLOCK_TITLE",
    "NOTHING_TO_FIX_MARKER",
    "NO_TARGET_ABORT_REASON",
    "REFRESH_FORMS",
    "BoundaryMiddleware",
    "FallbackMiddleware",
    "HistoryRepairMiddleware",
    "IntentMiddleware",
    "LoopGuardMiddleware",
    "MemoryMiddleware",
    "ModelRedactionMiddleware",
    "RedactionMiddleware",
    "Refreshed",
    "ToolCallIdMiddleware",
    "ToolErrorGuardMiddleware",
    "TurnState",
    "TurnStop",
    "build_memory_middleware",
    "current_time_block",
    "now_stamp",
    "refresh_memory",
]
