"""熔断状态存哪：进程内 / Redis 两套实现，以及中间件按共享状态工作。

状态机（阈值、冷却、探针）在 ``middleware/fallback.py`` 里只有一份，
``test_model_fallback.py`` 已经把它的行为钉住了；这里只钉**存取的契约**：

- 进程内版给的是副本（改完必须 ``save`` 才生效），与 Redis 版同一个契约；
- Redis 版跨实例共享 —— 这是多 worker 下熔断还有意义的全部原因（各记一份等于没有熔断）；
- Redis 报错就退回进程内，且**不打断请求**。
"""

from __future__ import annotations

import logging

import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from ai_coding_harness.agent.circuit import (
    CircuitState,
    MemoryCircuitStore,
    RedisCircuitStore,
)
from ai_coding_harness.agent.middleware.fallback import CIRCUIT_LOG_TAG, FallbackMiddleware
from tests.fake_redis import FakeRedis
from tests.test_model_fallback import _model, _rate_limited

PREFIX = "test:"
PRIMARY = "glm-5.3-flash"


async def test_the_memory_store_hands_out_copies() -> None:
    """改副本不写回 = 不生效（与 Redis 版同一契约，免得单进程下悄悄例外）。"""
    store = MemoryCircuitStore()
    state = await store.load("m")
    state.failures = 5

    assert (await store.load("m")).failures == 0

    await store.save("m", state)
    assert (await store.load("m")).failures == 5


async def test_the_redis_store_is_shared_between_instances() -> None:
    """两个实例＝两个 worker：一个记下的账，另一个读得到。"""
    redis = FakeRedis()
    a, b = RedisCircuitStore(redis, prefix=PREFIX), RedisCircuitStore(redis, prefix=PREFIX)

    await a.save(PRIMARY, CircuitState(failures=2, opened_at=1234.5, probing=True))

    state = await b.load(PRIMARY)
    assert (state.failures, state.opened_at, state.probing) == (2, 1234.5, True)
    assert redis.keys() == [f"{PREFIX}circuit:{PRIMARY}"]


async def test_an_unknown_model_starts_from_zero() -> None:
    redis = FakeRedis()
    store = RedisCircuitStore(redis, prefix=PREFIX)

    assert await store.load("从没见过的模型") == CircuitState()


async def test_a_dead_redis_falls_back_to_process_state() -> None:
    """Redis 报错时熔断退回进程内 —— 它只是省等待的启发式，不该让请求失败。"""
    redis = FakeRedis()
    store = RedisCircuitStore(redis, prefix=PREFIX)
    redis.fail_next(1)

    assert await store.load(PRIMARY) == CircuitState()
    assert store.degraded is True

    await store.save(PRIMARY, CircuitState(failures=1))
    assert (await store.load(PRIMARY)).failures == 1


async def test_the_middleware_honours_a_circuit_opened_by_another_worker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """别处已经开闸：这一轮连主导都不撞，直接走兜底（并留一条 MODEL_CIRCUIT）。"""
    redis = FakeRedis()
    store = RedisCircuitStore(redis, prefix=PREFIX)
    # 另一个 worker 留下的账：连续失败到阈值、冷却还没到
    from ai_coding_harness.agent.middleware import fallback as fallback_module

    await store.save(
        PRIMARY, CircuitState(failures=3, opened_at=fallback_module._now())
    )
    primary, fallback = _model(PRIMARY), _model("deepseek-v4-pro")
    middleware = FallbackMiddleware(
        role="coder",
        agent="coder",
        fallback=fallback,
        circuit_threshold=3,
        circuit_cooldown=60,
        circuit=store,
    )
    tried: list[str] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        tried.append(str(getattr(request.model, "model_name", "")))
        if request.model is not primary:
            return ModelResponse(result=[AIMessage(content="兜底答的")])
        raise _rate_limited("主导挂了")

    with caplog.at_level(logging.WARNING):
        result = await middleware.awrap_model_call(
            ModelRequest(model=primary, messages=[]), handler
        )

    assert tried == ["deepseek-v4-pro"], "主导一次都不该被撞"
    assert result.result[0].content == "兜底答的"
    assert any(CIRCUIT_LOG_TAG in r.getMessage() for r in caplog.records)
