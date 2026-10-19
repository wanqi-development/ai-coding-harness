"""运行时上下文辅助函数（供工具/节点内部使用，非 LangChain 工具）。

约定：会话的 ``thread_id`` 存放在 LangGraph 配置的 ``configurable`` 中
（见 api 层 chat 路由注入），本模块负责统一读取。
（``repo_url`` 不走 config —— 工具通过会话级 ``RepoSpec`` 获取。）
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_config as _langgraph_get_config
from langgraph.prebuilt import ToolRuntime


def get_config() -> dict[str, Any]:
    """获取当前 LangGraph 配置（RunnableConfig），无上下文时返回空 dict。"""
    try:
        return _langgraph_get_config()
    except RuntimeError:
        return {}


def get_configurable(key: str, default: Any = None) -> Any:
    """从 ``configurable`` 读取一个键值，无上下文时返回 default。"""
    try:
        cfg = _langgraph_get_config()
    except RuntimeError:
        return default
    configurable = cfg.get("configurable") if isinstance(cfg, dict) else {}
    return configurable.get(key, default)


def get_thread_id() -> str | None:
    """获取当前 thread_id（configurable.thread_id）。"""
    return get_configurable("thread_id")


def get_intent() -> str | None:
    """获取本次请求的意图（configurable.intent，由 server 层注入）。"""
    return get_configurable("intent")


def get_clarification() -> dict[str, Any] | None:
    """低置信度澄清信息（configurable.clarification）；不是澄清轮就是 ``None``。"""
    value = get_configurable("clarification")
    return value if isinstance(value, dict) else None


def get_redact_terms() -> tuple[str, ...]:
    """获取本次请求的脱敏词表（configurable.redact_terms，由 server 层注入）。

    取不到就返回空表：模式匹配照常生效，只是少掉「按值精确替换」那条腿。
    """
    return tuple(get_configurable("redact_terms") or ())


def get_state(runtime: ToolRuntime | None = None) -> Any:
    """获取当前 graph 状态。

    工具内优先传入注入的 ``runtime``（其 ``state`` 属性）；节点内 state 一般
    直接作为函数参数，无需此函数。
    """
    if runtime is not None:
        return runtime.state
    return None
