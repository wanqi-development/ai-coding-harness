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
- ``fallback``：主导模型彻底失败时切兜底，并留一条 ``MODEL_FALLBACK`` 的痕。
- ``stream_usage``：流式响应也要 token 用量；网关不认 ``stream_options`` 时按地址自动降级。
- ``context_trim``：压缩线处的**应急裁剪**辅助函数（保留当前步骤 + 模型工作笔记
  ``KEEP:`` 点名的结果，**只改这一次请求**）；真正的挂载与判断在
  ``agent/summarization.py``。

这里只做重新导出，装配点（``graph.py`` / ``subagents.py``）从这里拿类并实例化。
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
    PRETRIM_KEEP_RECENT_TOOL_RESULTS,
    keep_items_from_messages,
    pretrim_tool_results,
)
from ai_coding_harness.agent.middleware.delegation_gate import DelegationGateMiddleware
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
    MEMORY_BLOCK_TITLE,
    MemoryMiddleware,
    build_memory_middleware,
)
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

__all__ = [
    "ABORT_REASON",
    "FALLBACK_LOG_TAG",
    "LOOP_ABORT_MARKER",
    "MEMORY_BLOCK_TITLE",
    "PRETRIM_KEEP_RECENT_TOOL_RESULTS",
    "STREAM_USAGE_LOG_TAG",
    "BoundaryMiddleware",
    "DelegationGateMiddleware",
    "FallbackMiddleware",
    "HistoryRepairMiddleware",
    "IntentMiddleware",
    "LoopGuardMiddleware",
    "MemoryMiddleware",
    "ModelRedactionMiddleware",
    "ReadGuardMiddleware",
    "RedactionMiddleware",
    "StreamUsageMiddleware",
    "ToolCallIdMiddleware",
    "ToolErrorGuardMiddleware",
    "TurnState",
    "TurnStop",
    "build_memory_middleware",
    "keep_items_from_messages",
    "now_stamp",
    "pretrim_tool_results",
]
