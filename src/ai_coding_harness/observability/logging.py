"""应用日志配置：控制台 + 接口日志 + 智能体日志（按 logger 名路由）。

- 控制台：输出全部日志；
- `logs/api.log`：接口日志（HTTP 层 + uvicorn.access）；
- `logs/agent.log`：智能体运行日志（运行时模块 + 沙箱/工具/LLM 相关第三方库）；
- 每条日志带 `trace_id`（由接口中间件按请求生成），用于把两条流串起来。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from ai_coding_harness.config import Settings
from ai_coding_harness.observability.context import get_session_id, get_trace_id

#: 三格上下文：trace_id（一次请求）、session_id（一个会话）、agent（执行主角）。
#: session_id 是 2026-10-11 加的 —— 右栏「日志」标签按它筛「这个会话自己的日志」；
#: 没有它的时候只有 ``public_id=`` 那几种行带会话信息（占全量约 3%）。
_FORMAT = (
    "%(asctime)s [%(levelname)s] [%(trace_id)s] [%(session_id)s] [%(agent)s] "
    "%(name)s: %(message)s"
)
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 接口日志：HTTP 层
_API_PREFIXES = ("ai_coding_harness.api", "uvicorn.access")
# 智能体日志：运行时（含沙箱 / 工具 / LLM 相关第三方库）
_AGENT_PREFIXES = (
    "ai_coding_harness.agent",
    "ai_coding_harness.server",
    "ai_coding_harness.session",
    "ai_coding_harness.sandbox",
    "ai_coding_harness.backends",
    "ai_coding_harness.tools",
    "ai_coding_harness.memory",
    "ai_coding_harness.observability",
    "ai_coding_harness.safety",
    # 旁路状态（运行缓冲 / 熔断状态）的降级告警。它不在上面任何前缀下，漏掉这条的代价
    # 实测过：Redis 连不上时那句「退回进程内实现」只进控制台，日志文件里一个字都没有，
    # 窗口一关就再也查不到为什么「明明连得上却降级了」。
    "ai_coding_harness.infra.redis_cache",
    "opensandbox",
    "httpx",
    "langchain",
    "langgraph",
    "deepagents",
)

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# 运行日志里的「执行主角」标注：主智能体用 MAIN_AGENT，子智能体用其自身名字
# （planner / coder / reviewer）。没标注的一律记为 _AGENT_UNSET。
MAIN_AGENT = "main"

# 主智能体在事件流里的**真名**：``agent/graph.py`` 建主智能体时传的 ``name=`` 就是它，
# 于是主控那一路模型调用的 ``lc_agent_name`` 是这个字符串（不是上面的占位名）。
# 判「这一笔是不是主控」时必须认它 —— 只认 MAIN_AGENT 的话，主控的账会被归到一个英文名
# 下，而且「主控峰值输入」恒为 0，上下文压力那一整块静默失效（2026-10-12 实测踩到）。
# 定义在这里是为了**只有一处**：graph 用它命名，run_log 用它归类。
MAIN_AGENT_NAME = "ai-coding-harness"

_AGENT_UNSET = "-"


class _TraceIdFilter(logging.Filter):
    """把当前上下文的 trace_id / session_id 注入日志记录，并给执行主角留一个占位。

    ``agent`` 由日志调用方用 ``extra={"agent": "planner"}`` 主动给出（见
    ``server.service`` 的运行日志）；没给的一律记为 ``-``，这样格式串里的
    ``%(agent)s`` 永远不会缺键。``session_id`` 同理从 contextvar 取，没有上下文时是 ``-``。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        record.session_id = get_session_id()
        if not hasattr(record, "agent"):
            record.agent = _AGENT_UNSET
        return True


class _NamePrefixFilter(logging.Filter):
    """只放行 logger 名匹配给定前缀的日志。"""

    def __init__(self, prefixes: tuple[str, ...]) -> None:
        super().__init__()
        self._prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self._prefixes)


def _rotating_file(
    path: Path,
    settings: Settings,
) -> tuple[logging.Handler | None, Path]:
    """Create a file handler without making an unavailable log sink fatal.

    A second backend process, antivirus scanner, or malformed ACL can temporarily deny the
    fixed log path. Observability must degrade to a process-specific file instead of
    preventing the service from starting.
    """

    def build(candidate: Path) -> logging.Handler:
        handler = logging.handlers.RotatingFileHandler(
            candidate,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        handler.addFilter(_TraceIdFilter())
        return handler

    try:
        return build(path), path
    except OSError as primary_error:
        fallback = path.with_name(f"{path.stem}.{os.getpid()}{path.suffix}")
        try:
            handler = build(fallback)
        except OSError as fallback_error:
            print(
                f"[WARN] Cannot open log file {path}: {primary_error}; "
                f"fallback {fallback} also failed: {fallback_error}. "
                "Continuing with console logging only.",
                file=sys.stderr,
            )
            return None, path
        print(
            f"[WARN] Cannot open log file {path}: {primary_error}. "
            f"Logging to {fallback} instead.",
            file=sys.stderr,
        )
        return handler, fallback


def setup_logging(settings: Settings) -> Path:
    """配置日志（控制台 + api.log + agent.log），返回 api 日志路径。"""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    console.addFilter(_TraceIdFilter())

    api_file, api_path = _rotating_file(log_dir / settings.log_api_file, settings)
    if api_file is not None:
        api_file.addFilter(_NamePrefixFilter(_API_PREFIXES))

    agent_file, _ = _rotating_file(log_dir / settings.log_agent_file, settings)
    if agent_file is not None:
        agent_file.addFilter(_NamePrefixFilter(_AGENT_PREFIXES))

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(console)
    if api_file is not None:
        root.addHandler(api_file)
    if agent_file is not None:
        root.addHandler(agent_file)

    # uvicorn 自带 handler：清掉、改为冒泡到根（从而进入控制台与 api.log）
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.propagate = True
        logger.setLevel(level)

    # 第三方库降噪：httpx 会把完整 URL（含 query）按 INFO、httpcore 会把请求头按 DEBUG
    # 打进日志 —— 拼在 URL 里的令牌、或走请求头的令牌都会落盘明文，故一律压到 WARNING。
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    return api_path
