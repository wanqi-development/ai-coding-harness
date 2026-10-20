"""在线校准：真值 / 估算 → 按模型维护的系数。

钉住的几条都是实测口径：只认「我们刚发出去的那一份」的样本、跳过兜底模型答的响应、
小请求不记（固定开销占主导）、没有样本时系数必须是 1.0（行为与校准前完全一致）。

不联网：模型与响应都是假的。
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ai_coding_harness.agent.context_calibration import (
    CALIBRATION,
    CalibratedTokenCounter,
    CalibrationStore,
    model_key,
    reset_calibration,
)
from ai_coding_harness.agent.middleware.context_trim import context_token_counter
from ai_coding_harness.agent.middleware.summarization import build_summarization
from ai_coding_harness.config import Settings


@pytest.fixture(autouse=True)
def _clean():
    reset_calibration()
    yield
    reset_calibration()


def _model(name: str = "m-cal", base: str = "http://127.0.0.1:1/v1") -> Any:
    return SimpleNamespace(model_name=name, openai_api_base=base)


def _response(
    *,
    input_tokens: int,
    answered_by: str = "m-cal",
) -> Any:
    message = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": 10,
            "total_tokens": input_tokens + 10,
        },
        response_metadata={"model_name": answered_by},
    )
    return SimpleNamespace(result=[message])


class TestStore:
    def test_the_first_sample_is_the_ratio(self) -> None:
        store = CalibrationStore()
        found = store.record("k", estimated=100_000, actual=130_000)

        assert found is not None
        assert found.ratio == pytest.approx(1.3)
        assert found.samples == 1
        assert store.ratio("k") == pytest.approx(1.3)

    def test_later_samples_move_by_the_ema_step(self) -> None:
        store = CalibrationStore()
        store.record("k", estimated=100_000, actual=100_000)  # 1.0
        found = store.record("k", estimated=100_000, actual=200_000)  # sample 2.0

        assert found is not None
        assert found.ratio == pytest.approx(1.0 * 0.7 + 2.0 * 0.3)
        assert found.samples == 2

    def test_outliers_are_clamped(self) -> None:
        store = CalibrationStore()
        assert store.record("k", estimated=10_000, actual=1_000_000) is not None
        assert store.ratio("k") == pytest.approx(3.0)

    def test_small_requests_are_not_samples(self) -> None:
        """固定开销主导的小请求会把比例带偏；不记。"""
        store = CalibrationStore()
        assert store.record("k", estimated=500, actual=800) is None
        assert store.ratio("k") == 1.0

    def test_missing_and_zero_usage_is_ignored(self) -> None:
        store = CalibrationStore()
        assert store.record("k", estimated=10_000, actual=0) is None
        assert store.record("k", estimated=10_000, actual=-1) is None

    def test_models_do_not_share_a_ratio(self) -> None:
        store = CalibrationStore()
        store.record("a", estimated=10_000, actual=13_000)
        assert store.ratio("a") != 1.0
        assert store.ratio("b") == 1.0


class TestCounter:
    def test_no_sample_keeps_the_raw_estimate(self) -> None:
        counter = CalibratedTokenCounter(_model())
        messages = [HumanMessage(content="甲" * 3000)]

        assert counter(messages) == context_token_counter(messages)

    def test_the_ratio_is_applied(self) -> None:
        store = CalibrationStore()
        store.record("http://127.0.0.1:1/v1|m-cal", estimated=100_000, actual=130_000)
        counter = CalibratedTokenCounter(_model(), store=store)
        messages = [HumanMessage(content="甲" * 3000)]
        raw = context_token_counter(messages)

        assert counter(messages) == math.ceil(raw * 1.3)

    def test_record_converts_the_calibrated_estimate_back_to_raw(self) -> None:
        store = CalibrationStore()
        key = "http://127.0.0.1:1/v1|m-cal"
        store.record(key, estimated=100_000, actual=130_000)  # ratio 1.3
        counter = CalibratedTokenCounter(_model(), store=store)

        # 校准后估算 130k、真实 130k → 原始估算应回到 100k，比例仍是 1.3
        found = counter.record(_response(input_tokens=130_000), 130_000)

        assert found is not None
        assert found.ratio == pytest.approx(1.3)
        assert found.samples == 2

    def test_a_fallback_answer_is_not_recorded_on_the_primary(self) -> None:
        store = CalibrationStore()
        counter = CalibratedTokenCounter(_model(), store=store)
        assert counter.record(_response(input_tokens=50_000, answered_by="other"), 40_000) is None
        assert store.ratio(counter.key) == 1.0

    def test_missing_usage_is_ignored(self) -> None:
        counter = CalibratedTokenCounter(_model())
        assert counter.record(SimpleNamespace(result=[AIMessage(content="ok")]), 40_000) is None

    def test_key_is_base_url_plus_model(self) -> None:
        assert model_key(_model()) == "http://127.0.0.1:1/v1|m-cal"
        # 末尾斜杠归一：同一家网关不该记成两本账
        assert model_key(_model(base="http://x/v1/")) == "http://x/v1|m-cal"


class TestThroughTheMiddleware:
    async def test_a_normal_call_records_the_sample(self) -> None:
        """真实链路：估算 → 调模型 → 拿 usage → 记样本。"""
        from langchain.agents.middleware import ModelRequest, ModelResponse
        from langchain_core.language_models.fake_chat_models import (
            GenericFakeChatModel,
        )

        class Fake(GenericFakeChatModel):
            model_name: str = "m-cal"
            openai_api_base: str | None = "http://127.0.0.1:1/v1"

            def _get_ls_params(self, **kwargs: object) -> dict[str, str]:
                return {"ls_provider": "openai"}

        model = Fake(messages=iter([AIMessage(content="ok")]))
        model.profile = {"max_input_tokens": 1_000_000}
        settings = Settings(
            _env_file=None,
            context_summary_trigger_tokens=120_000,
            context_summary_keep_tokens=1_000,
        )
        middleware = build_summarization(model, SimpleNamespace(), settings, agent="coder")
        assert middleware is not None
        messages = [HumanMessage(content="甲" * 3000)]  # ≈1875 tokens，过 1000 的样本门槛

        async def handler(request: ModelRequest) -> ModelResponse:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="ok",
                        usage_metadata={
                            "input_tokens": 2_000,
                            "output_tokens": 5,
                            "total_tokens": 2_005,
                        },
                        response_metadata={"model_name": "m-cal"},
                    )
                ]
            )

        await middleware.awrap_model_call(
            ModelRequest(model=model, messages=messages, state={"messages": messages}),
            handler,
        )

        assert CALIBRATION.ratio("http://127.0.0.1:1/v1|m-cal") > 1.0
