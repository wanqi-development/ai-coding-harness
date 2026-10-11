"""跨 worker 的运行缓冲与运行权（配了 Redis 时走的那条路）。

单 worker 的行为在 ``test_run_hub.py`` 里钉着（内存实现，语义没变）；这里钉的是**多
worker 才有的那几件事**：刷新后落到另一个 worker 也能接回这一轮、同一个会话不许被两个
worker 同时跑、持有者挂了锁会过期、跨 worker 的终止请求真的停得住、以及 Redis 挂了要
退回进程内而不是把这一轮弄丢。

两个 ``RunHub`` 实例共用一份 ``FakeRedis`` 就等价于两个 worker —— 这正是这里能在不装
Redis 的机器上验跨进程语义的原因（替身见 ``tests/fake_redis.py``）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_coding_harness.server.run_store import (
    RedisRunBuffer,
    RedisRunLock,
)
from ai_coding_harness.server.runs import RunHub, RunInProgress
from tests.fake_redis import FakeRedis
from tests.run_fakes import GatedService, ScriptedService, StubSession, settle

PREFIX = "test:"


def _worker(
    redis: FakeRedis,
    service: Any,
    *,
    max_frames: int = 2000,
    lock_ttl: int = 120,
    buffer_ttl: int = 3600,
) -> RunHub:
    """一个「worker」：自己的 RunHub 实例，共用同一份 Redis。"""
    return RunHub(
        service,
        buffer=RedisRunBuffer(
            redis, prefix=PREFIX, ttl_seconds=buffer_ttl, max_frames=max_frames
        ),
        lock=RedisRunLock(redis, prefix=PREFIX, ttl_seconds=lock_ttl),
        poll_interval=0.01,
        renew_interval=3600,  # 测试里不需要续期，别让它插进来
    )


async def _drain(
    hub: RunHub, session_id: str, after: int = 0
) -> list[tuple[int, dict]]:
    return [(seq, frame) async for seq, frame in hub.subscribe(session_id, after)]


async def test_a_round_started_in_one_worker_streams_to_another() -> None:
    """本轮改动的核心：A 上起的那一轮，浏览器刷新落到 B 上照样接得回、跟得下。"""
    redis = FakeRedis()
    svc = GatedService([{"type": "ai", "content": c} for c in "abc"])
    a, b = _worker(redis, svc), _worker(redis, svc)
    await a.start("s", StubSession(), "hi")
    await svc.advance(1)

    got: list[tuple[int, dict]] = []

    async def collect() -> None:
        async for seq, frame in b.subscribe("s", 0):
            got.append((seq, frame))

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)
    assert [seq for seq, _ in got] == [1], "先补已有的帧"

    await svc.advance(2)
    await asyncio.wait_for(task, timeout=5)
    assert [seq for seq, _ in got] == [1, 2, 3]


async def test_a_cursor_can_be_resumed_on_another_worker() -> None:
    """前端带的 ``after`` 游标在另一个 worker 上同样有效（帧号来自 Redis）。"""
    redis = FakeRedis()
    svc = ScriptedService([{"type": "ai", "content": str(i)} for i in range(4)])
    a, b = _worker(redis, svc), _worker(redis, svc)
    await a.start("s", StubSession(), "hi")
    await settle()

    assert [seq for seq, _ in await _drain(b, "s", 2)] == [3, 4]


async def test_a_second_worker_cannot_start_the_same_session() -> None:
    """同一会话被两个 worker 同时跑，就是两个容器写同一个工作区 —— 必须拦住。"""
    redis = FakeRedis()
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    a, b = _worker(redis, svc), _worker(redis, svc)
    await a.start("s", StubSession(), "hi")
    await svc.advance(1)

    with pytest.raises(RunInProgress):
        await b.start("s", StubSession(), "again")

    await svc.advance(1)


async def test_the_lock_expires_so_another_worker_can_take_over() -> None:
    """持有者进程被 kill（锁到期）之后，别的 worker 能接手 —— 不然那个会话就永久卡住。"""
    redis = FakeRedis()
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    a, b = _worker(redis, svc, lock_ttl=120), _worker(redis, svc, lock_ttl=120)
    await a.start("s", StubSession(), "hi")

    redis.advance(121)

    await b.start("s", StubSession(), "hi")
    await a.shutdown()
    await b.shutdown()


async def test_a_run_is_visible_to_the_other_worker() -> None:
    redis = FakeRedis()
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    a, b = _worker(redis, svc), _worker(redis, svc)
    await a.start("s", StubSession(), "hi")
    await svc.advance(1)

    assert await b.is_running("s") is True

    await svc.advance(1)
    await settle()
    assert await b.is_running("s") is False


async def test_cancel_from_another_worker_stops_the_run() -> None:
    """用户在 A 上点了终止，而这一轮挂在 B 上：留标记 → B 自己取消。"""
    redis = FakeRedis()
    svc = GatedService([{"type": "ai", "content": c} for c in "abc"])
    a, b = _worker(redis, svc), _worker(redis, svc)
    await a.start("s", StubSession(), "hi")
    await svc.advance(1)

    assert await b.cancel("s") is True
    await asyncio.sleep(0.1)  # B 的 watcher 一个轮询周期内该动手了
    assert await a.is_running("s") is False
    assert await _drain(b, "s") == [], "取消＝丢掉那一轮的缓冲（订阅者据此收尾）"

    await svc.advance(1)  # 放行下一帧：这一轮已经取消，不该再落进缓冲
    assert await _drain(b, "s") == []


async def test_cancel_on_a_session_nobody_runs_is_not_an_error() -> None:
    redis = FakeRedis()
    hub = _worker(redis, ScriptedService([]))

    assert await hub.cancel("s") is False


async def test_frame_numbers_keep_counting_after_old_frames_are_trimmed() -> None:
    """丢老帧之后帧号仍要连续 —— 拿下标当帧号，前端续传会重复或跳帧。"""
    redis = FakeRedis()
    buf = RedisRunBuffer(redis, prefix=PREFIX, ttl_seconds=3600, max_frames=3)
    await buf.open("s")

    for i in range(5):
        assert await buf.append("s", {"type": "ai", "content": str(i)}) == i + 1

    got = await buf.read("s", 0)
    assert [seq for seq, _ in got] == [3, 4, 5]
    assert [frame["content"] for _, frame in got] == ["2", "3", "4"]


async def test_an_expired_buffer_reads_as_an_idle_session() -> None:
    """TTL 到期（一轮早就结束、没人再接）之后，订阅者该立刻收尾，而不是空等。"""
    redis = FakeRedis()
    svc = ScriptedService([{"type": "done"}])
    a, b = _worker(redis, svc, buffer_ttl=60), _worker(redis, svc, buffer_ttl=60)
    await a.start("s", StubSession(), "hi")
    await settle()
    assert [seq for seq, _ in await _drain(b, "s")] == [1]

    redis.advance(61)

    assert await b.is_running("s") is False
    assert await _drain(b, "s") == []


async def test_a_run_whose_owner_died_is_not_reported_as_running() -> None:
    """持有者死了（进程被 kill / 重启）之后，那一轮不该再报「在跑」。

    回归：``RunHub`` 原先只看缓冲 —— 缓冲里剩一把 ``seq``（``open`` 时写的、TTL 一小时）
    却没有 ``done``，于是 ``is_running`` 永远返回 True、``subscribe`` 永远空转。实测撞到过：
    界面上一直显示「正在接续本轮」，要等 ``seq`` 的 TTL 到期才自愈。运行权带 TTL 且由持有者
    定期续期，所以「锁还在不在」才是存活信号。
    """
    redis = FakeRedis()
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    a, b = _worker(redis, svc), _worker(redis, svc)
    await a.start("s", StubSession(), "hi")

    # 持有者进程被杀：本地 task 还在 park（模拟「没人再推进它」），但锁到期了
    redis.advance(121)

    assert await b.is_running("s") is False, "锁过期 ＝ 持有者已死，不该再报在跑"
    assert await _drain(b, "s") == [], "订阅要立刻收尾，不能空转等 TTL"

    await a.shutdown()


async def test_a_dead_redis_degrades_to_the_process_buffer() -> None:
    """Redis 报错就退回进程内那半：这一轮照跑，帧照样能重放（只是不再跨 worker）。"""
    redis = FakeRedis()
    buf = RedisRunBuffer(redis, prefix=PREFIX)
    redis.fail_next(1)  # 下一条命令（append 的取号）就开始报错

    assert await buf.append("s", {"type": "ai", "content": "a"}) == 1
    assert buf.degraded is True
    assert await buf.append("s", {"type": "ai", "content": "b"}) == 2
    calls = len(redis.calls)
    assert await buf.append("s", {"type": "ai", "content": "c"}) == 3
    assert len(redis.calls) == calls, "降级之后不再每条命令都白撞一次"

    assert [(seq, f["content"]) for seq, f in await buf.read("s", 0)] == [
        (1, "a"),
        (2, "b"),
        (3, "c"),
    ]
    assert await buf.is_done("s") is False
    await buf.finish("s")
    assert await buf.is_done("s") is True


async def test_frame_numbers_survive_reading_while_a_frame_is_being_written() -> None:
    """**边写边读**时帧号必须与内容一一对应，一帧都不许丢。

    真 Redis 里「取号」与「入列」如果不是一次事务，读侧就能看到「号涨了、帧还没进列表」
    的中间态；读侧靠 ``首帧号 = 号 - 长度 + 1`` 反推，于是把所有帧**整体多算一号**，
    游标跟着跳过去、尾巴上的那一帧被跳过（实测现象：订阅者拿到 (2, 首帧)、(3, 次帧)）。

    这里用 ``round_trip=True`` 的替身：每条命令都是一次往返，读写两条协程真的会交错。
    """
    redis = FakeRedis(round_trip=True)
    buf = RedisRunBuffer(redis, prefix=PREFIX)
    await buf.open("s")

    seen: list[tuple[int, str]] = []
    cursor = 0

    async def writer() -> None:
        for i in range(6):
            await buf.append("s", {"type": "ai", "content": str(i)})

    async def reader() -> None:
        nonlocal cursor
        for _ in range(60):
            for seq, frame in await buf.read("s", cursor):
                cursor = seq
                seen.append((seq, frame["content"]))
            await asyncio.sleep(0)

    await asyncio.gather(writer(), reader())
    # 收尾再读一次（读者可能在最后一帧落地前就退出了循环）
    for seq, frame in await buf.read("s", cursor):
        seen.append((seq, frame["content"]))

    assert [content for _, content in seen] == [str(i) for i in range(6)], seen
    assert [seq for seq, _ in seen] == [1, 2, 3, 4, 5, 6], seen
