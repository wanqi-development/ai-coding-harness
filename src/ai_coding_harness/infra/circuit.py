"""模型熔断状态存哪：进程内 / Redis 两套实现。

状态是「某个主导模型连续失败了几次、什么时候开的闸、是不是已经放了一个探针」。
判定与记账的**状态机不在这里**（在 ``middleware/fallback.py``）—— 这里只负责存取，
于是两套实现的行为差别只剩「存哪」，不会各写一套规则。

两套实现的语义差别（都是有意的）：

- 进程内版返回的是**副本**，改完必须 ``save`` 才生效 —— 与 Redis 版同一个契约，
  免得「忘了写回」在单进程下悄悄不生效、上了多 worker 才暴露；
- Redis 版是**近似共享**：两个 worker 同时读改写会丢一次计数（熔断本来就是个启发式阈值，
  早一次晚一次开闸不改变正确性），探针也可能被两个 worker 同时放出去（多探一次而已）。
  要做到严格，得引 ``HINCRBY`` + Lua 脚本，按当前的用途不值这个复杂度。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ai_coding_harness.infra.redis_cache import RedisBacked


@dataclass
class CircuitState:
    """一个主导模型的连续失败与熔断窗。"""

    failures: int = 0
    opened_at: float | None = None
    probing: bool = False


class CircuitStore(Protocol):
    """熔断状态的存取。"""

    async def load(self, primary: str) -> CircuitState:
        """读当前状态；从没记过就是这个模型的初始态。"""

    async def save(self, primary: str, state: CircuitState) -> None:
        """写回（``load`` 拿到的是副本，改完不写回就不生效）。"""


class MemoryCircuitStore:
    """进程内状态 —— 单 worker 下就是原来那个模块级字典。"""

    def __init__(self) -> None:
        self._states: dict[str, CircuitState] = {}

    async def load(self, primary: str) -> CircuitState:
        known = self._states.get(primary)
        if known is None:
            return CircuitState()
        return CircuitState(
            failures=known.failures, opened_at=known.opened_at, probing=known.probing
        )

    async def save(self, primary: str, state: CircuitState) -> None:
        self._states[primary] = CircuitState(
            failures=state.failures, opened_at=state.opened_at, probing=state.probing
        )

    def clear(self) -> None:
        """清空全部状态（单测用：模块级单例会跨用例串味）。"""
        self._states.clear()


class RedisCircuitStore(RedisBacked):
    """Redis 上的熔断状态：一个模型一个 HASH，带 TTL（坏模型不留痕太久）。"""

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "harness:",
        ttl_seconds: int = 3600,
        cooldown_seconds: float = 60.0,
        guard_seconds: float = 15.0,
    ) -> None:
        super().__init__(
            client,
            what="熔断状态",
            cooldown_seconds=cooldown_seconds,
            guard_seconds=guard_seconds,
        )
        self._prefix = prefix
        self._ttl = ttl_seconds
        self._fallback = MemoryCircuitStore()

    def _key(self, primary: str) -> str:
        return f"{self._prefix}circuit:{primary}"

    async def load(self, primary: str) -> CircuitState:
        return await self.run(
            lambda: self._load_redis(primary),
            lambda: self._fallback.load(primary),
        )

    async def save(self, primary: str, state: CircuitState) -> None:
        await self._fallback.save(primary, state)
        await self.run(lambda: self._save_redis(primary, state), _noop)

    # -- Redis 那半 ---------------------------------------------------------
    async def _load_redis(self, primary: str) -> CircuitState:
        raw = await self._redis.hgetall(self._key(primary))
        if not raw:
            return CircuitState()
        opened = raw.get("opened_at")
        return CircuitState(
            failures=int(raw.get("failures") or 0),
            opened_at=None if opened in (None, "") else float(opened),
            probing=raw.get("probing") == "1",
        )

    async def _save_redis(self, primary: str, state: CircuitState) -> None:
        key = self._key(primary)
        await self._redis.hset(
            key,
            mapping={
                "failures": str(state.failures),
                "opened_at": "" if state.opened_at is None else str(state.opened_at),
                "probing": "1" if state.probing else "0",
            },
        )
        await self._redis.expire(key, self._ttl)


async def _noop() -> None:
    return None
