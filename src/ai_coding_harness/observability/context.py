"""请求级 trace_id（contextvar），把接口日志与智能体日志串起来。"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    """生成一个新的 trace_id。"""
    return uuid.uuid4().hex[:12]


def set_trace_id(trace_id: str) -> Token[str]:
    """设置当前上下文的 trace_id，返回用于恢复的 token。"""
    return _trace_id.set(trace_id)


def reset_trace_id(token: Token[str]) -> None:
    _trace_id.reset(token)


def get_trace_id() -> str:
    """取当前 trace_id（无上下文时为 '-'）。"""
    return _trace_id.get()
