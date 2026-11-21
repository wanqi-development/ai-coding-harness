"""Provider-protocol adapters for role-specific chat model construction.

The application has one model ingress: ``build_chat_model``. It resolves a fixed
``ModelConfig`` to a concrete ``BaseChatModel`` through a protocol adapter. Provider
thinking parameters stay inside the adapter; callers only declare ``enabled`` or
``disabled``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel, ModelProfile

from ai_coding_harness.agent.chat_model import ReasoningContentChatOpenAI
from ai_coding_harness.config import (
    ANTHROPIC_PROTOCOL,
    DEFAULT_PROVIDER_PROTOCOL,
    ModelConfig,
    temperature_for,
)

ThinkingMode = Literal["enabled", "disabled"]


@dataclass(frozen=True)
class ModelBuildOptions:
    thinking: ThinkingMode = "enabled"
    streaming: bool = True
    stream_usage: bool = True
    max_tokens: int | None = None
    temperature_override: float | None = None


class ChatModelAdapter(Protocol):
    protocol: str

    def build(self, config: ModelConfig, options: ModelBuildOptions) -> BaseChatModel: ...


def wants_reasoning_effort(model: str) -> bool:
    """Only OpenAI Chat Completions reasoning families accept ``reasoning_effort``."""
    name = (model or "").strip().lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


def wants_responses_api(model: str) -> bool:
    """GPT-6 exposes readable reasoning summaries through the Responses API."""
    name = (model or "").strip().lower()
    return name.startswith("gpt-6")


def accepts_temperature(model: str) -> bool:
    """GPT-6 rejects ``temperature`` even when tools are bound."""
    return not wants_responses_api(model)


def disabled_thinking_options(config: ModelConfig) -> dict[str, Any]:
    """Return the provider-specific request body for disabling reasoning.

    vLLM exposes Qwen's switch through ``chat_template_kwargs``. DeepSeek
    accepts the native ``thinking`` object. Unknown OpenAI-compatible
    gateways get no private extension rather than a parameter they may reject.
    """
    provider = (config.provider or "").strip().lower()
    if provider == "vllm":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if provider == "deepseek":
        return {"thinking": {"type": "disabled"}}
    return {}


class OpenAICompatibleAdapter:
    """OpenAI Chat Completions plus the third-party ``reasoning_content`` extension."""

    protocol = DEFAULT_PROVIDER_PROTOCOL

    def build(self, config: ModelConfig, options: ModelBuildOptions) -> BaseChatModel:
        model_options: dict[str, Any]
        if options.thinking == "enabled" and wants_responses_api(config.model):
            model_options = {
                "use_responses_api": True,
                "output_version": "responses/v1",
                "reasoning": {"effort": "high", "summary": "detailed"},
                "use_previous_response_id": False,
            }
        elif options.thinking == "enabled":
            model_options = {"extra_body": {"thinking": {"type": "enabled"}}}
            if wants_reasoning_effort(config.model):
                model_options["reasoning_effort"] = "high"
        else:
            disabled_body = disabled_thinking_options(config)
            model_options = {"extra_body": disabled_body} if disabled_body else {}

        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "stream_usage": options.stream_usage,
            "streaming": options.streaming,
            **model_options,
        }
        if accepts_temperature(config.model):
            kwargs["temperature"] = temperature_for(
                config.model,
                config.temperature
                if options.temperature_override is None
                else options.temperature_override,
            )
        if options.max_tokens is not None:
            kwargs["max_tokens"] = options.max_tokens
        return ReasoningContentChatOpenAI(**kwargs)


class AnthropicAdapter:
    """Native Anthropic Messages protocol through LangChain's provider factory."""

    protocol = ANTHROPIC_PROTOCOL

    def build(self, config: ModelConfig, options: ModelBuildOptions) -> BaseChatModel:
        temperature = (
            config.temperature
            if options.temperature_override is None
            else options.temperature_override
        )
        kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "base_url": config.base_url,
            "temperature": temperature,
            "streaming": options.streaming,
            "stream_usage": options.stream_usage,
        }
        if options.max_tokens is not None:
            kwargs["max_tokens"] = options.max_tokens
        if options.thinking == "enabled":
            # Anthropic requires temperature=1 when extended thinking is enabled.
            kwargs["temperature"] = 1.0
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4096}

        model = init_chat_model(
            config.model,
            model_provider="anthropic",
            **kwargs,
        )
        if not isinstance(model, BaseChatModel):
            raise TypeError("Anthropic Adapter did not create a fixed BaseChatModel")
        return model


_ADAPTERS: dict[str, ChatModelAdapter] = {
    adapter.protocol: adapter
    for adapter in (OpenAICompatibleAdapter(), AnthropicAdapter())
}


def build_chat_model(
    config: ModelConfig,
    options: ModelBuildOptions | None = None,
) -> BaseChatModel:
    """Build one model through the single protocol-dispatch ingress."""
    if not config.model:
        raise ValueError("模型名未配置")
    if not config.api_key or not config.base_url:
        raise ValueError("缺少 api_key 或 base_url")
    adapter = _ADAPTERS.get(config.protocol)
    if adapter is None:
        supported = " / ".join(sorted(_ADAPTERS))
        raise ValueError(f"不支持的模型协议 {config.protocol!r}；可选：{supported}")

    model = adapter.build(config, options or ModelBuildOptions())
    if config.context_window > 0:
        model.profile = ModelProfile(max_input_tokens=config.context_window)
    return model
