"""``redis.asyncio`` 的最小替身：只实现我们用到的那几个命令 + 可控时钟。

为什么不用 ``fakeredis``：这东西只是给「多 worker 共用一份 Redis」那几条用例当底座，
引一个第三方测试依赖不值当；而真正会咬人的几件事（TTL 到期、命令报错降级、两个实例
共用一份存储）用这 150 行反而更可控 —— 时钟是手推的，不用真等 120 秒。

值与真客户端一致按**字符串**存（线上建客户端时带 ``decode_responses=True``）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

#: 需要「每条命令让出一次」的包装名单（与下面实现的方法一一对应）
_COMMANDS = (
    "get",
    "set",
    "delete",
    "exists",
    "expire",
    "incr",
    "rpush",
    "ltrim",
    "llen",
    "lrange",
    "hgetall",
    "hset",
    "ping",
)


def _per_round_trip(command: Any) -> Any:
    """把一条命令包成「先让出一次事件循环，再干活」。

    真 Redis 每条命令都是一次网络往返，别的东西可以插在两条命令之间；这里的实现全是
    同步的，天然没有交错点 —— 于是「取号」与「入列」分成两条命令时那道缝**在测试里
    复现不出来**。加上这个让出点，替身才和真实例一个脾气（见 ``round_trip``）。
    """

    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0)
        return await command(*args, **kwargs)

    return wrapped


class FakeRedis:
    """内存里的 Redis 替身。``advance(seconds)`` 手动推进时钟，用于 TTL 相关断言。

    ``round_trip=True`` 时每条命令都会先让出一次事件循环（见 ``_per_round_trip``）——
    验「边写边读」这类交错问题时必须开：不开的话两条命令之间根本插不进别的协程。
    """

    def __init__(self, *, round_trip: bool = False) -> None:
        self._values: dict[str, Any] = {}
        self._lists: dict[str, list[str]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._expires: dict[str, float] = {}
        self._now = time.monotonic()
        self._failures = 0
        self.calls: list[str] = []
        self._round_trip = round_trip
        if round_trip:
            for name in _COMMANDS:
                setattr(self, name, _per_round_trip(getattr(self, name)))

    # -- 事务 ---------------------------------------------------------------
    def pipeline(self, transaction: bool = True) -> _Pipeline:
        """``MULTI/EXEC`` 的替身（见 ``_Pipeline``）。``transaction`` 只为对齐 redis-py 的签名。"""
        return _Pipeline(self)

    # -- 测试用的小工具 -----------------------------------------------------
    def advance(self, seconds: float) -> None:
        """把时钟往前推 —— 到期的键在下一次访问时消失（惰性过期，与 Redis 同）。"""
        self._now += seconds
        self._sweep()

    def fail_next(self, count: int = 1) -> None:
        """让接下来 ``count`` 条命令抛错（验「Redis 挂了就降级」）。"""
        self._failures = count

    def keys(self) -> list[str]:
        return sorted(set(self._values) | set(self._lists) | set(self._hashes))

    async def aclose(self) -> None:
        self._record("aclose")

    # -- 命令 ---------------------------------------------------------------
    async def get(self, key: str) -> str | None:
        self._record("get")
        return self._values.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool | None:
        self._record("set")
        exists = key in self._values
        if nx and exists:
            return None
        if xx and not exists:
            return None
        self._values[key] = str(value)
        if ex is not None:
            self._expires[key] = self._now + ex
        else:
            self._expires.pop(key, None)
        return True

    async def delete(self, *keys: str) -> int:
        self._record("delete")
        removed = 0
        for key in keys:
            for store in (self._values, self._lists, self._hashes):
                if key in store:
                    del store[key]
                    removed += 1
            self._expires.pop(key, None)
        return removed

    async def exists(self, *keys: str) -> int:
        self._record("exists")
        return sum(1 for key in keys if self._present(key))

    async def expire(self, key: str, seconds: int) -> bool:
        self._record("expire")
        if not self._present(key):
            return False
        self._expires[key] = self._now + seconds
        return True

    async def incr(self, key: str, amount: int = 1) -> int:
        self._record("incr")
        current = int(self._values.get(key) or 0)
        self._values[key] = str(current + amount)
        return current + amount

    async def rpush(self, key: str, *values: str) -> int:
        self._record("rpush")
        items = self._lists.setdefault(key, [])
        items.extend(str(v) for v in values)
        return len(items)

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        self._record("ltrim")
        items = self._lists.get(key)
        if items is None:
            return False
        size = len(items)
        lo = start + size if start < 0 else start
        hi = end + size if end < 0 else end
        self._lists[key] = items[max(lo, 0) : hi + 1]
        return True

    async def llen(self, key: str) -> int:
        self._record("llen")
        return len(self._lists.get(key, []))

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        self._record("lrange")
        values = self._lists.get(key, [])[start : (end + 1 if end >= 0 else None)]
        return list(values)

    async def hgetall(self, key: str) -> dict[str, str]:
        self._record("hgetall")
        return dict(self._hashes.get(key, {}))

    async def hset(self, key: str, mapping: dict[str, str] | None = None, **kw: str) -> int:
        self._record("hset")
        fields = {**(mapping or {}), **kw}
        store = self._hashes.setdefault(key, {})
        added = sum(1 for field in fields if field not in store)
        store.update({field: str(value) for field, value in fields.items()})
        return added

    async def ping(self) -> bool:
        self._record("ping")
        return True

    # -- 内部 ---------------------------------------------------------------
    def _record(self, command: str) -> None:
        self.calls.append(command)
        if self._failures > 0:
            self._failures -= 1
            raise ConnectionError("fake redis down")
        self._sweep()

    def _present(self, key: str) -> bool:
        return key in self._values or key in self._lists or key in self._hashes

    def _sweep(self) -> None:
        """惰性过期：只删「访问这一刻已经到期」的键。"""
        for key, deadline in list(self._expires.items()):
            if deadline <= self._now:
                for store in (self._values, self._lists, self._hashes):
                    store.pop(key, None)
                self._expires.pop(key, None)


class _Pipeline:
    """``MULTI/EXEC`` 的替身：命令排队，``execute()`` 时一口气跑完。

    **事务里的整块只算一次往返**，所以这里直接调 ``FakeRedis`` 的基类实现，绕开实例上
    那层「每条命令让出一次」的包装 —— 这正是真 Redis 的行为：事务执行期间别的客户端
    的命令插不进来。
    """

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _queue(self, name: str, *args: Any, **kwargs: Any) -> _Pipeline:
        self._queued.append((name, args, kwargs))
        return self

    def get(self, key: str) -> _Pipeline:
        return self._queue("get", key)

    def set(self, key: str, value: str, **kw: Any) -> _Pipeline:
        return self._queue("set", key, value, **kw)

    def delete(self, *keys: str) -> _Pipeline:
        return self._queue("delete", *keys)

    def exists(self, *keys: str) -> _Pipeline:
        return self._queue("exists", *keys)

    def expire(self, key: str, seconds: int) -> _Pipeline:
        return self._queue("expire", key, seconds)

    def incr(self, key: str, amount: int = 1) -> _Pipeline:
        return self._queue("incr", key, amount)

    def rpush(self, key: str, *values: str) -> _Pipeline:
        return self._queue("rpush", key, *values)

    def ltrim(self, key: str, start: int, end: int) -> _Pipeline:
        return self._queue("ltrim", key, start, end)

    def llen(self, key: str) -> _Pipeline:
        return self._queue("llen", key)

    def lrange(self, key: str, start: int, end: int) -> _Pipeline:
        return self._queue("lrange", key, start, end)

    async def execute(self) -> list[Any]:
        """按排队顺序执行并返回每条命令的结果（与 redis-py 同形）。"""
        results = [
            await getattr(FakeRedis, name)(self._redis, *args, **kwargs)
            for name, args, kwargs in self._queued
        ]
        self._queued.clear()
        return results
