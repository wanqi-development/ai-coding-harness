"""Protocol adapter contract: one ingress, provider-specific thinking only."""

from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel

from ai_coding_harness.agent.chat_model import ReasoningContentChatOpenAI
from ai_coding_harness.agent.model_adapters import (
    ModelBuildOptions,
    build_chat_model,
)
from ai_coding_harness.config import (
    ANTHROPIC_PROTOCOL,
    ModelConfig,
    load_provider_seed,
)


def test_anthropic_seed_declares_native_protocol_and_headers() -> None:
    anthropic = next(
        meta for meta in load_provider_seed() if meta.provider == "anthropic"
    )

    assert anthropic.protocol == ANTHROPIC_PROTOCOL
    assert anthropic.default_base_url == "https://api.anthropic.com"
    assert anthropic.auth_header == "x-api-key"
    assert anthropic.extra_headers["anthropic-version"] == "2023-06-01"


def test_openai_compatible_uses_the_reasoning_content_adapter() -> None:
    model = build_chat_model(
        ModelConfig(
            model="glm-5.3",
            api_key="k",
            base_url="https://example.test/v1",
            provider="glm",
            context_window=1_000_000,
        )
    )

    assert isinstance(model, ReasoningContentChatOpenAI)
    assert model.extra_body == {"thinking": {"type": "enabled"}}
    assert model.profile is not None
    assert model.profile["max_input_tokens"] == 1_000_000


def test_gpt6_never_sends_the_unsupported_temperature_parameter() -> None:
    model = build_chat_model(
        ModelConfig(
            model="gpt-6.1-sol",
            api_key="k",
            base_url="https://example.test/v1",
            provider="gpt",
        )
    )

    payload = model._get_request_payload("hello")

    assert model.use_responses_api is True
    assert "temperature" not in payload


def test_gpt6_classifier_also_omits_temperature() -> None:
    model = build_chat_model(
        ModelConfig(
            model="gpt-6-luna",
            api_key="k",
            base_url="https://example.test/v1",
            provider="gpt",
        ),
        ModelBuildOptions(thinking="disabled", streaming=False),
    )

    payload = model._get_request_payload("hello")

    assert not model.use_responses_api
    assert "temperature" not in payload


def test_vllm_classifier_uses_qwen_chat_template_switch() -> None:
    model = build_chat_model(
        ModelConfig(
            model="qwen3-8b",
            api_key="k",
            base_url="http://127.0.0.1:8000/v1",
            provider="vllm",
        ),
        ModelBuildOptions(thinking="disabled", streaming=False),
    )

    assert model.extra_body == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_deepseek_classifier_uses_native_thinking_switch() -> None:
    model = build_chat_model(
        ModelConfig(
            model="deepseek-flash",
            api_key="k",
            base_url="https://api.deepseek.com",
            provider="deepseek",
        ),
        ModelBuildOptions(thinking="disabled", streaming=False),
    )

    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_unknown_compatible_provider_gets_no_private_disabled_thinking_field() -> None:
    model = build_chat_model(
        ModelConfig(
            model="m",
            api_key="k",
            base_url="https://example.test/v1",
            provider="custom",
        ),
        ModelBuildOptions(thinking="disabled", streaming=False),
    )

    assert model.extra_body in (None, {})


def test_anthropic_uses_native_messages_and_enables_thinking() -> None:
    model = build_chat_model(
        ModelConfig(
            model="claude-sonnet-4-5",
            api_key="k",
            base_url="https://api.anthropic.com",
            provider="anthropic",
            protocol=ANTHROPIC_PROTOCOL,
            context_window=200_000,
        )
    )

    assert isinstance(model, ChatAnthropic)
    assert model.temperature == 1.0
    assert model.thinking == {"type": "enabled", "budget_tokens": 4096}
    assert model.profile is not None
    assert model.profile["max_input_tokens"] == 200_000


def test_anthropic_classifier_omits_thinking_and_limits_output() -> None:
    model = build_chat_model(
        ModelConfig(
            model="claude-sonnet-4-5",
            api_key="k",
            base_url="https://api.anthropic.com",
            provider="anthropic",
            protocol=ANTHROPIC_PROTOCOL,
        ),
        ModelBuildOptions(
            thinking="disabled",
            streaming=False,
            stream_usage=False,
            max_tokens=512,
            temperature_override=0,
        ),
    )

    assert isinstance(model, ChatAnthropic)
    assert model.thinking is None
    assert model.temperature == 0
    assert model.max_tokens == 512


def test_unknown_protocol_fails_before_constructing_a_model() -> None:
    with pytest.raises(ValueError, match="不支持的模型协议"):
        build_chat_model(
            ModelConfig(
                model="m",
                api_key="k",
                base_url="https://example.test",
                protocol="unknown",
            )
        )


def test_all_adapters_return_a_base_chat_model() -> None:
    compatible = build_chat_model(
        ModelConfig(model="m", api_key="k", base_url="https://example.test/v1")
    )
    anthropic = build_chat_model(
        ModelConfig(
            model="claude-sonnet-4-5",
            api_key="k",
            base_url="https://api.anthropic.com",
            protocol=ANTHROPIC_PROTOCOL,
        )
    )

    assert isinstance(compatible, BaseChatModel)
    assert isinstance(anthropic, BaseChatModel)
