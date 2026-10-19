"""``RunHub``：把一轮对话从 HTTP 请求的生命周期里剥出来。

守的就是这个 bug：``EventSourceResponse`` 收到 ``http.disconnect`` 时会取消整个 task
group，而运行原来直接挂在那个请求上 —— **浏览器一刷新，这一轮就被杀掉**，``_run_agent``
走不到收尾的落库，成果与 token 一起丢。这里钉住「订阅者弃流不影响运行」，以及重放、
游标、并发拒绝这几条。假件见 ``tests/run_fakes.py``。

末尾两节是「终止」：用户点按钮 → ``POST /sessions/{id}/cancel`` → ``RunHub.cancel``。
取消不是故障，所以流要干净收尾（不产 error 帧）、重按要幂等。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ai_coding_harness.api.routes import sessions as sessions_route
from ai_coding_harness.server.runs import RUN_ERROR_MESSAGE, RunHub, RunInProgress
from tests.run_fakes import BoomService, GatedService, ScriptedService, StubSession, settle


async def _drain(hub: RunHub, session_id: str, after: int = 0) -> list[tuple[int, dict]]:
    return [(seq, frame) async for seq, frame in hub.subscribe(session_id, after)]


class _FlakyLock:
    """续期失败、但**能不能重新拿回**由构造参数决定的运行权。

    模拟 2026-10-12 真机上那次：SSH 隧道抖了一下 → 续期失败 → 钥匙 TTL 到点消失 → 之后
    每次 `renew` 都失败（`renew` 只查+续，不会重建），而 `acquire`（SET NX）还能拿回来。
    """

    cross_process = True

    def __init__(self, *, reacquire: bool, held: bool = True) -> None:
        self._reacquire = reacquire
        self._held = held
        self.acquires = 0
        self.renews = 0

    async def acquire(self, session_id: str) -> bool:
        self.acquires += 1
        if self.acquires == 1:
            return True  # start() 的那一次：这一轮本来就持有它
        return self._reacquire

    async def renew(self, session_id: str) -> bool:
        self.renews += 1
        return False

    async def release(self, session_id: str) -> None:
        return None

    async def held(self, session_id: str) -> bool:
        return self._held


async def test_a_lapsed_lock_is_taken_back_and_the_run_keeps_going(caplog) -> None:
    """钥匙过期（Redis 抖动）时先**重新拿回**，这一轮继续跑。

    实测那次：从 20:43:50 起 `renew` 每 30 秒失败一次、一直失败到轮次结束 —— 那 95 分钟里
    这一轮是「无主」跑的，UI 的订阅还先收尾了（界面显示成跑完了）。
    """
    lock = _FlakyLock(reacquire=True)
    # 一帧都不放行：这一轮停在「跑到一半」，好让续期那一步真的发生
    hub = RunHub(
        GatedService([{"type": "ai", "content": "a"}]),
        lock=lock,
        renew_interval=0.01,
        poll_interval=0.01,
    )
    caplog.set_level("INFO", logger="ai_coding_harness.server.runs")
    await hub.start("s", StubSession(), "hi")

    await settle()
    await asyncio.sleep(0.05)

    assert lock.renews >= 1 and lock.acquires >= 1, "续期失败后必须尝试重新拿回"
    assert await hub.is_running("s") is True, "拿回来了就该继续跑"
    assert any("重新取回" in r.getMessage() for r in caplog.records)
    await hub.shutdown()


async def test_a_lock_stolen_by_another_worker_ends_this_run(caplog) -> None:
    """真被别人拿走时**干净收尾**：别在没有所有权的情况下继续写同一个会话。"""
    lock = _FlakyLock(reacquire=False)
    service = GatedService([{"type": "done"}])
    hub = RunHub(service, lock=lock, renew_interval=0.01, poll_interval=0.01)
    caplog.set_level("WARNING", logger="ai_coding_harness.server.runs")
    await hub.start("s", StubSession(), "hi")

    await asyncio.sleep(0.05)

    assert await hub.is_running("s") is False, "被抢走就不该再报在跑"
    assert any("已被别的 worker 持有" in r.getMessage() for r in caplog.records)
    await hub.shutdown()


async def test_a_lock_blip_does_not_close_a_live_local_subscription() -> None:
    """本进程正在跑的轮次：锁短暂消失不该让**订阅**收尾。

    收尾了界面就会显示成「跑完了」，而这一轮其实还在跑（真机上就是这么发生的）。
    """
    service = GatedService([{"type": "ai", "content": "a"}, {"type": "done"}])
    hub = RunHub(
        service,
        lock=_FlakyLock(reacquire=True, held=False),
        renew_interval=30.0,
        poll_interval=0.01,
    )
    await hub.start("s", StubSession(), "hi")

    got: list[tuple[int, dict]] = []

    async def drain() -> None:
        got.extend(await _drain(hub, "s"))

    subscriber = asyncio.create_task(drain())
    await settle()

    assert subscriber.done() is False, "本地这一轮还在跑，订阅不该收尾"

    await service.advance(2)
    await subscriber

    assert _texts(got) == ["a", None]
    await hub.shutdown()


def _texts(got: list[tuple[int, dict]]) -> list[Any]:
    return [frame.get("content") for _, frame in got]


async def test_subscriber_gets_every_frame_numbered_from_one() -> None:
    hub = RunHub(ScriptedService([{"type": "ai", "content": "a"}, {"type": "done"}]))
    await hub.start("s", StubSession(), "hi")
    got = await _drain(hub, "s")
    assert [seq for seq, _ in got] == [1, 2]
    assert _texts(got) == ["a", None]


async def test_after_cursor_skips_the_frames_already_seen() -> None:
    """重连时带上已收到的最后一个帧号，只补后面的。"""
    hub = RunHub(ScriptedService([{"type": "ai", "content": str(i)} for i in range(4)]))
    await hub.start("s", StubSession(), "hi")
    await settle()
    got = await _drain(hub, "s", after=2)
    assert [seq for seq, _ in got] == [3, 4]


async def test_a_subscriber_joining_mid_run_replays_then_follows_live() -> None:
    """中途接进来的（刷新后接回）：先补已有的，再跟着新帧走。"""
    svc = GatedService([{"type": "ai", "content": c} for c in "abc"])
    hub = RunHub(svc)
    await hub.start("s", StubSession(), "hi")
    await svc.advance(2)

    got: list[tuple[int, dict]] = []

    async def collect() -> None:
        async for seq, frame in hub.subscribe("s", 0):
            got.append((seq, frame))

    task = asyncio.create_task(collect())
    await settle()
    assert [seq for seq, _ in got] == [1, 2]

    await svc.advance(1)
    assert [seq for seq, _ in got] == [1, 2, 3]
    await task


async def test_abandoning_a_subscriber_does_not_kill_the_run() -> None:
    """本轮修的那个 bug 的回归测试。

    ``aclose()`` 就是浏览器刷新时 sse-starlette 对生成器做的事。这一轮必须照跑到尾 ——
    成果落库靠的是运行本身，不是那个订阅者。
    """
    svc = GatedService([{"type": "ai", "content": c} for c in "abcd"])
    hub = RunHub(svc)
    await hub.start("s", StubSession(), "hi")
    await svc.advance(1)

    stream = hub.subscribe("s", 0)
    assert await anext(stream) == (1, {"type": "ai", "content": "a"})
    await stream.aclose()

    await svc.advance(3)
    await settle()
    assert not await hub.is_running("s")
    assert _texts(await _drain(hub, "s")) == list("abcd")


async def test_a_second_round_on_the_same_session_is_rejected() -> None:
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    hub = RunHub(svc)
    await hub.start("s", StubSession(), "hi")
    with pytest.raises(RunInProgress):
        await hub.start("s", StubSession(), "again")
    await svc.advance(2)
    await settle()


async def test_a_new_round_replaces_the_previous_buffer() -> None:
    svc = ScriptedService([{"type": "ai", "content": "old"}])
    hub = RunHub(svc)
    await hub.start("s", StubSession(), "one")
    await settle()
    assert _texts(await _drain(hub, "s")) == ["old"]

    svc.frames = [{"type": "ai", "content": "new"}]
    await hub.start("s", StubSession(), "two")
    await settle()
    assert _texts(await _drain(hub, "s")) == ["new"]


async def test_a_crash_inside_the_service_surfaces_a_fixed_message() -> None:
    """service 在它自己接不住的地方炸了：帧里只给固定措辞，原始异常只进日志。"""
    hub = RunHub(BoomService())
    await hub.start("s", StubSession(), "hi")
    got = await _drain(hub, "s")
    assert got[0] == (1, {"type": "ai", "content": "开头"})
    assert got[1] == (2, {"type": "error", "error": RUN_ERROR_MESSAGE})
    assert "工具输出" not in str(got)


async def test_subscribing_to_an_idle_session_ends_at_once() -> None:
    hub = RunHub(ScriptedService([]))
    assert await _drain(hub, "nope") == []


async def test_cancel_stops_the_run_and_drops_the_buffer() -> None:
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    hub = RunHub(svc)
    await hub.start("s", StubSession(), "hi")
    await svc.advance(1)
    assert await hub.is_running("s")

    await hub.cancel("s")
    await settle()
    assert not await hub.is_running("s")
    assert await _drain(hub, "s") == []


async def test_cancel_reports_whether_a_round_was_really_running() -> None:
    """路由拿这个布尔值回 ``{"cancelled": ...}``：重按一次、或者刚好跑完了，都不是错误。"""
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    hub = RunHub(svc)
    assert await hub.cancel("s") is False  # 这个会话从没跑过

    await hub.start("s", StubSession(), "hi")
    await svc.advance(1)
    assert await hub.cancel("s") is True
    await settle()
    assert await hub.cancel("s") is False  # 已经停了，再按无效


async def test_cancel_on_a_finished_round_is_not_an_error() -> None:
    """用户点慢了、这一轮刚好自己跑完：task 已结束，如实回 false。"""
    hub = RunHub(ScriptedService([{"type": "done"}]))
    await hub.start("s", StubSession(), "hi")
    await settle()
    assert not await hub.is_running("s")
    assert await hub.cancel("s") is False


async def test_cancel_ends_an_attached_stream_without_an_error_frame() -> None:
    """「终止」后挂着的那条流要干净地收尾。

    取消不是故障：``CancelledError`` 是 ``BaseException``，pump 的 ``except Exception``
    接不住它，但 ``finally`` 照跑 —— 订阅者据此醒来、看到 ``done`` 就返回。所以这里不仅
    要「流结束了」，还要**没有** error 帧（有的话前端就会在正文后面接一行「错误」）。
    """
    svc = GatedService([{"type": "ai", "content": c} for c in "abc"])
    hub = RunHub(svc)
    await hub.start("s", StubSession(), "hi")
    await svc.advance(1)

    got: list[tuple[int, dict]] = []

    async def collect() -> None:
        async for seq, frame in hub.subscribe("s", 0):
            got.append((seq, frame))

    task = asyncio.create_task(collect())
    await settle()
    assert [seq for seq, _ in got] == [1]

    assert await hub.cancel("s") is True
    await asyncio.wait_for(task, timeout=5)
    assert _texts(got) == ["a"]
    assert RUN_ERROR_MESSAGE not in str(got)


async def test_shutdown_cancels_the_in_flight_runs() -> None:
    """退出前必须收干净：不然它们会在连接池拆掉之后还去写库。"""
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    hub = RunHub(svc)
    await hub.start("s", StubSession(), "hi")
    await svc.advance(1)
    assert await hub.is_running("s")

    await asyncio.wait_for(hub.shutdown(), timeout=5)
    assert not await hub.is_running("s")
    assert await _drain(hub, "s") == []


# -- 路由：POST /sessions/{id}/cancel ---------------------------------------


def _cancel_app(service: Any) -> FastAPI:
    """只装会话路由的最小应用 —— cancel 只碰 ``app.state.runs``，不查库、不建工作区。"""
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.state.runs = RunHub(service)
    return app


async def test_the_cancel_route_reports_whether_it_stopped_a_round() -> None:
    """前端 ``cancelChat`` 读的就是这个 ``cancelled`` 键：键名或形状变了它就静默失灵。"""
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    app = _cancel_app(svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        assert (await client.post("/sessions/s/cancel")).json() == {"cancelled": False}

        await app.state.runs.start("s", StubSession(), "hi")
        await svc.advance(1)
        assert (await client.post("/sessions/s/cancel")).json() == {"cancelled": True}
        assert (await client.post("/sessions/s/cancel")).json() == {"cancelled": False}
