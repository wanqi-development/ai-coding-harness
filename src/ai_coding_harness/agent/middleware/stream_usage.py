"""流式响应的 token 用量：让 OpenAI 兼容网关在流式模式下也回 ``usage``。

LangChain 的 ``ChatOpenAI`` 只在**默认 OpenAI 地址**时自动打开 ``stream_usage``；本项目
全走自定义 ``base_url``（DeepSeek / Moonshot / 自建网关），默认不发送
``stream_options.include_usage``，于是流式响应没有 ``usage_metadata`` —— 日志里就是
「LLM 调用完成：（无用量信息）」，压缩的「上一轮真值」触发与在线校准都拿不到样本。

这里配合 ``agent/llm.py`` 的 ``stream_usage=True`` 做两件事：

1. 网关认这个字段：流式最后一帧带用量，正常记账；
2. 网关不认（400/422 且报错文本点名 ``stream_options`` / ``include_usage``）：
   **按 base_url 记住**，本次改用不带该字段的模型副本重试一次，后续同一地址直接走
   降级路径。缓存只在进程内，重启后重试一次 —— 网关升级支持了就能自动恢复。

位置放在**兜底中间件内侧**：这次 400 是「网关不认字段」，不是「主导模型挂了」，
不能让 ``FallbackMiddleware`` 把它记成主导故障、切到兜底模型。重试也只重跑模型调用，
不会再跑压缩、脱敏这些外层中间件。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models.chat_models import BaseChatModel

from ai_coding_harness.observability.logging import MAIN_AGENT

logger = logging.getLogger(__name__)

# 查降级走这个关键字：grep STREAM_USAGE logs/agent.log
STREAM_USAGE_LOG_TAG = "STREAM_USAGE"

#: 已经确认不接受 ``stream_options`` 的网关地址（进程内共享，重启清空）。
_UNSUPPORTED: set[str] = set()

#: 报错文本里出现这些词才算「不认 stream_options」，别的 400 原样抛。
_REJECTION_WORDS = ("stream_options", "include_usage", "stream_usage")


def reset_stream_usage_cache() -> None:
    """清空降级缓存（单测用：模块级集合会跨用例串味）。"""
    _UNSUPPORTED.clear()


def _endpoint_key(model: BaseChatModel | None) -> str:
    """按 ``base_url`` 认网关；取不到地址就不降级、也不缓存。"""
    base = getattr(model, "openai_api_base", None)
    return str(base).rstrip("/") if base else ""


def _rejects_stream_options(exc: BaseException) -> bool:
    """是不是「网关不认 stream_options」这一种 400/422。"""
    if getattr(exc, "status_code", None) not in (400, 422):
        return False
    text = str(exc).lower()
    return any(word in text for word in _REJECTION_WORDS)


def _without_stream_usage(model: BaseChatModel) -> BaseChatModel:
    """同一套连接、只把流式用量关掉的副本（不动原实例，别的角色还在用）。"""
    if not hasattr(model, "stream_usage"):
        return model
    return model.model_copy(update={"stream_usage": False})


class StreamUsageMiddleware(AgentMiddleware):
    """流式用量的自动降级；``agent`` 只用于日志 ``%(agent)s`` 那一列。"""

    def __init__(self, *, agent: str = MAIN_AGENT) -> None:
        super().__init__()
        self._agent = agent

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        key = _endpoint_key(request.model)
        if key and key in _UNSUPPORTED:
            return await handler(
                request.override(model=_without_stream_usage(request.model))
            )
        try:
            return await handler(request)
        except Exception as exc:
            if not key or not _rejects_stream_options(exc):
                raise
            _UNSUPPORTED.add(key)
            logger.warning(
                "%s 网关不接受 stream_options.include_usage，后续该地址改用非流式用量：%s",
                STREAM_USAGE_LOG_TAG,
                key,
                extra={"agent": self._agent},
            )
            return await handler(
                request.override(model=_without_stream_usage(request.model))
            )
