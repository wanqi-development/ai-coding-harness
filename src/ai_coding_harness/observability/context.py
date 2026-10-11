"""请求级 trace_id 与会话级 session_id（contextvar），把日志串起来。

两个维度各管一段：

- ``trace_id``：**一次请求**。接口日志与智能体日志靠它串成一条；
- ``session_id``：**一个会话**（``public_id``）。右栏「日志」标签靠它把「这个会话自己的
  日志」筛出来 —— 在此之前只有 ``public_id=`` 那几种日志行带会话信息，占全量约 3%，
  工具调用 / LLM 调用这些真正想看的行**都不带**（2026-10-11 实测：877 / 28907 行）。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")
_session_id: ContextVar[str] = ContextVar("session_id", default="-")


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


def set_session_id(session_id: str) -> Token[str]:
    """设置当前上下文的会话 id（``public_id``），返回用于恢复的 token。

    在 ``server.service.chat()`` 里设一次就够 —— 它是一轮的入口，跑在 ``RunHub`` 的独立
    task 里；task 有独立的 context，所以这一个 set 覆盖本轮所有日志（中间件 / 工具 / 沙箱 /
    LLM 全都带上），也不会串到别的会话上。
    """
    return _session_id.set(session_id)


def reset_session_id(token: Token[str]) -> None:
    _session_id.reset(token)


def get_session_id() -> str:
    """取当前会话 id（无上下文时为 '-'）。"""
    return _session_id.get()
