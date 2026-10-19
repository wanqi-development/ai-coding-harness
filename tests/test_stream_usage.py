"""流式 token 用量：模型默认打开，网关不认时按地址自动降级。

这一层只负责「拿得到就拿、拿不到别把请求打挂」：真正的估算与压缩判据不依赖 usage，
它只是「上一轮真值」触发与在线校准的样本来源。

不联网：模型只构造，``handler`` 是假的。
"""

from __future__ import annotations

import httpx
import openai
import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from ai_coding_harness.agent.llm import build_model
from ai_coding_harness.agent.middleware.stream_usage import (
    STREAM_USAGE_LOG_TAG,
    StreamUsageMiddleware,
    reset_stream_usage_cache,
)
from ai_coding_harness.config import ModelConfig, Settings
from ai_coding_harness.observability.logging import MAIN_AGENT

_BASE = "http://127.0.0.1:1/v1"


def _model() -> BaseChatModel:
    return init_chat_model(
        model="m",
        model_provider="openai",
        api_key="dummy",
        base_url=_BASE,
        stream_usage=True,
    )


def _bad_request(message: str) -> Exception:
    return openai.BadRequestError(
        message,
        response=httpx.Response(
            400, request=httpx.Request("POST", f"{_BASE}/chat/completions")
        ),
        body=None,
    )


def _request() -> ModelRequest:
    return ModelRequest(model=_model(), messages=[])


def _ok(text: str = "ok") -> ModelResponse:
    return ModelResponse(result=[AIMessage(content=text)])


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    reset_stream_usage_cache()


def test_model_factory_turns_stream_usage_on() -> None:
    """主模型与兜底都走 ``_model_from``：这一处打开，两条路都有用量。"""
    settings = Settings(
        _env_file=None,
        agent_models={
            "qa": ModelConfig(model="m-qa", api_key="k", base_url=_BASE),
        },
    )
    assert build_model(settings, role="qa").stream_usage is True


class TestStreamUsageMiddleware:
    async def test_rejection_retries_once_without_the_field_and_remembers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        middleware = StreamUsageMiddleware(agent=MAIN_AGENT)
        seen: list[BaseChatModel] = []
        reject = True

        async def handler(request: ModelRequest) -> ModelResponse:
            nonlocal reject
            seen.append(request.model)  # type: ignore[arg-type]
            if reject:
                reject = False
                raise _bad_request("Unrecognized request argument: stream_options")
            return _ok()

        result = await middleware.awrap_model_call(_request(), handler)
        assert result.result[0].content == "ok"
        assert len(seen) == 2
        assert seen[0].stream_usage is True
        assert seen[1].stream_usage is False

        # 同一地址的下一次调用直接降级，不再先撞一次 400
        seen.clear()
        await middleware.awrap_model_call(_request(), handler)
        assert len(seen) == 1 and seen[0].stream_usage is False
        assert any(
            STREAM_USAGE_LOG_TAG in record.getMessage() for record in caplog.records
        )

    async def test_unrelated_400_is_not_swallowed(self) -> None:
        middleware = StreamUsageMiddleware(agent=MAIN_AGENT)
        seen: list[BaseChatModel] = []

        async def handler(request: ModelRequest) -> ModelResponse:
            seen.append(request.model)  # type: ignore[arg-type]
            raise _bad_request("model is not found")

        with pytest.raises(openai.BadRequestError):
            await middleware.awrap_model_call(_request(), handler)
        assert len(seen) == 1

    async def test_422_naming_include_usage_is_also_a_rejection(self) -> None:
        middleware = StreamUsageMiddleware(agent=MAIN_AGENT)
        seen: list[BaseChatModel] = []

        async def handler(request: ModelRequest) -> ModelResponse:
            seen.append(request.model)  # type: ignore[arg-type]
            if len(seen) == 1:
                raise openai.UnprocessableEntityError(
                    "unknown field include_usage",
                    response=httpx.Response(
                        422,
                        request=httpx.Request("POST", f"{_BASE}/chat/completions"),
                    ),
                    body=None,
                )
            return _ok()

        await middleware.awrap_model_call(_request(), handler)
        assert len(seen) == 2 and seen[1].stream_usage is False

    async def test_retry_failure_propagates(self) -> None:
        """降级后仍失败就把第二个异常抛出 —— 不无限重试。"""
        middleware = StreamUsageMiddleware(agent=MAIN_AGENT)
        calls = 0

        async def handler(request: ModelRequest) -> ModelResponse:
            nonlocal calls
            calls += 1
            raise _bad_request("stream_options rejected")

        with pytest.raises(openai.BadRequestError):
            await middleware.awrap_model_call(_request(), handler)
        assert calls == 2

    async def test_no_base_url_never_retries(self) -> None:
        """取不到地址就没有「按地址记住」的依据，别把错误吞成无限重试。"""
        middleware = StreamUsageMiddleware(agent=MAIN_AGENT)
        model = _model().model_copy(update={"openai_api_base": None})
        calls = 0

        async def handler(request: ModelRequest) -> ModelResponse:
            nonlocal calls
            calls += 1
            raise _bad_request("stream_options rejected")

        with pytest.raises(openai.BadRequestError):
            await middleware.awrap_model_call(
                ModelRequest(model=model, messages=[]), handler
            )
        assert calls == 1

