"""主智能体与子智能体的中间件。

按出口分几个模块，各自的取舍都写在模块 docstring 里：

- ``context_inject``：每次模型调用前注入本轮意图与澄清块；
- ``redaction``：凭据脱敏的两条出口 —— 工具返回值、模型输出；
- ``tool_integrity``：工具调用配对自洽 —— 空 id 归一、工具异常兜底、历史整表修复；
- ``boundary``：按智能体的活动范围 —— 隐藏越界的工具、拦下越界的调用；
- ``guard``：本轮空转刹车 —— 同一工具同一参数反复调用时先劝、再停；
- ``delegation_gate``：主控的委派前置条件 —— 本轮没成功写过 ``write_todos`` 就不许
  调用 ``task``；
- ``read_guard``：读文件红线 —— policies ≤80 行、project ≤200 行；``cat`` 等整文件
  命令直接拦；
- ``memory``：仓库记忆 —— 每轮开始把它注入系统提示；PR 状态闸门在 server 层意图判定后
  一次完成，不再由中间件承担；
- ``plan_recovery``：coding 主控 / planner / coder 进入和正常退出时回收 executing 方案；
- ``fallback``：主导模型彻底失败时切兜底，并留一条 ``MODEL_FALLBACK`` 的痕。
- ``stream_usage``：流式响应也要 token 用量；网关不认 ``stream_options`` 时按地址自动降级。
- ``context_trim``：压缩线处的**持久化裁剪投影**（保留当前步骤 + 模型工作笔记
  ``KEEP:`` 点名的结果；事件只存工具调用 ID，原文仍在 checkpoint）；真正的挂载与判断在
  ``agent/summarization.py``。

这里只做重新导出，装配点（``graph.py`` / ``subagents.py``）从这里拿类并实例化。
公共的 ``TurnState`` / ``TurnStop`` 定义在 ``agent.turn_state``，这里只保留兼容导出。
中间件是 ``AgentMiddleware`` 子类，实例无状态，每次构建新实例即可 —— 例外是
``BoundaryMiddleware``、``FallbackMiddleware`` 与 ``MemoryMiddleware``：前者持有该智能体的
策略、中者持有该角色的角色名与兜底模型、后者按轮暂存要注入的记忆块，所以这三类
**每个角色要各 new 一个**。``HarnessSummarizationMiddleware`` 同理（它持有日志要用的角色标注）。
"""

from __future__ import annotations

from ai_coding_harness.agent.middleware.boundary import BoundaryMiddleware
from ai_coding_harness.agent.middleware.context_inject import (
    IntentMiddleware,
    now_stamp,
)
from ai_coding_harness.agent.middleware.context_trim import (
    CONTEXT_TRIM_EVENT_VERSION,
    PRETRIM_KEEP_RECENT_TOOL_RESULTS,
    PRETRIM_MIN_ARG_CHARS,
    PRETRIM_MIN_RESULT_CHARS,
    ContextTrimEvent,
    apply_context_trim_event,
    keep_items_from_messages,
    merge_context_trim_event,
    plan_context_trim_event,
    pretrim_tool_results,
    prune_context_trim_event,
)
from ai_coding_harness.agent.middleware.delegation_gate import DelegationGateMiddleware
from ai_coding_harness.agent.middleware.fallback import (
    FALLBACK_LOG_TAG,
    FallbackMiddleware,
)
from ai_coding_harness.agent.middleware.guard import (
    LoopGuardMiddleware,
)
from ai_coding_harness.agent.middleware.memory import (
    MEMORY_BLOCK_TITLE,
    MemoryMiddleware,
    build_memory_middleware,
)
from ai_coding_harness.agent.middleware.plan_recovery import PlanRecoveryMiddleware
from ai_coding_harness.agent.middleware.read_guard import ReadGuardMiddleware
from ai_coding_harness.agent.middleware.redaction import (
    ModelRedactionMiddleware,
    RedactionMiddleware,
)
from ai_coding_harness.agent.middleware.stream_usage import (
    STREAM_USAGE_LOG_TAG,
    StreamUsageMiddleware,
)
from ai_coding_harness.agent.middleware.tool_integrity import (
    HistoryRepairMiddleware,
    ToolCallIdMiddleware,
    ToolErrorGuardMiddleware,
)
from ai_coding_harness.agent.turn_state import (
    ABORT_REASON,
    LOOP_ABORT_MARKER,
    TurnState,
    TurnStop,
)

__all__ = [
    "ABORT_REASON",
    "CONTEXT_TRIM_EVENT_VERSION",
    "FALLBACK_LOG_TAG",
    "LOOP_ABORT_MARKER",
    "MEMORY_BLOCK_TITLE",
    "PRETRIM_KEEP_RECENT_TOOL_RESULTS",
    "PRETRIM_MIN_ARG_CHARS",
    "PRETRIM_MIN_RESULT_CHARS",
    "STREAM_USAGE_LOG_TAG",
    "BoundaryMiddleware",
    "ContextTrimEvent",
    "DelegationGateMiddleware",
    "FallbackMiddleware",
    "HistoryRepairMiddleware",
    "IntentMiddleware",
    "LoopGuardMiddleware",
    "MemoryMiddleware",
    "ModelRedactionMiddleware",
    "PlanRecoveryMiddleware",
    "ReadGuardMiddleware",
    "RedactionMiddleware",
    "StreamUsageMiddleware",
    "ToolCallIdMiddleware",
    "ToolErrorGuardMiddleware",
    "TurnState",
    "TurnStop",
    "apply_context_trim_event",
    "build_memory_middleware",
    "keep_items_from_messages",
    "merge_context_trim_event",
    "now_stamp",
    "plan_context_trim_event",
    "pretrim_tool_results",
    "prune_context_trim_event",
]
