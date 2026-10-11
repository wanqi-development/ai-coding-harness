"""一轮对话的「运行宿主」：把智能体的运行从 HTTP 请求的生命周期里剥出来。

背景：``EventSourceResponse`` 收到 ``http.disconnect`` 时会取消整个 task group，而
``service.chat()`` 原来就直接挂在那个 group 上 —— 于是**浏览器一刷新，这一轮就被杀掉**，
``_run_agent`` 走不到收尾的落库，成果与 token 一起丢。

这里让运行跑在自己的 task 里，产出的帧按序号攒着；HTTP 侧只是**订阅**这份缓冲。请求断了
只是少一个订阅者，运行照跑到落库为止；刷新后带游标重新订阅就能接着看。

帧与运行权放在 ``RunBuffer`` / ``RunLock`` 后面（见 ``server/run_store.py``）：

- **单进程**（没配 Redis）：就是原来那份进程内缓冲，语义一字未改；
- **多 worker**（配了 ``REDIS_URL``）：帧、运行权、终止请求都在 Redis 上 —— 刷新后落到
  哪个 worker 都能接回这一轮，同一个会话也不会被两个 worker 同时跑。

本进程内仍留一份轻量注册表：``task`` 句柄只有本进程有（取消、退出时必须够得着），
外加一个唤醒事件 —— **本进程在跑的那一轮走事件驱动**，不给用户多加一次轮询延迟；
只有「别的 worker 在跑」那种订阅才走轮询。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ai_coding_harness.server.run_store import (
    MemoryRunBuffer,
    MemoryRunLock,
    RunBuffer,
    RunLock,
)

if TYPE_CHECKING:
    from ai_coding_harness.server.service import HarnessService
    from ai_coding_harness.session.manager import Session

logger = logging.getLogger(__name__)

# 兜底错误的固定措辞：运行在 ``service.chat`` 接得住的地方炸了，原始异常文本可能夹带
# 工具输出，不进帧、只进日志。
RUN_ERROR_MESSAGE = "本轮运行异常，请重试。"


class RunInProgress(RuntimeError):
    """同一会话已有一轮在跑 —— 两轮并发会互相踩（同一个 thread_id、同一个工作区）。"""


@dataclass
class _Local:
    """本进程这一轮的句柄：task + 唤醒信号（**帧不在这儿**，在 ``RunBuffer`` 里）。"""

    task: asyncio.Task | None = None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    done: bool = False
    watcher: asyncio.Task | None = None


class RunHub:
    """运行注册表：启动、订阅、取状态、取消。

    ``buffer`` / ``lock`` 默认是进程内那套；配了 Redis 时由装配处换成 Redis 版。
    ``poll_interval`` 是**没有本地唤醒**时的轮询间隔（订阅别的 worker 的轮次、以及
    轮询「别处请求终止」），默认 0.2s —— 远小于一次模型往返，用户感知不到。
    """

    def __init__(
        self,
        service: HarnessService,
        *,
        buffer: RunBuffer | None = None,
        lock: RunLock | None = None,
        poll_interval: float = 0.2,
        renew_interval: float = 30.0,
    ) -> None:
        self._service = service
        self._buffer: RunBuffer = buffer or MemoryRunBuffer()
        self._lock: RunLock = lock or MemoryRunLock()
        self._poll = poll_interval
        self._renew_interval = renew_interval
        self._runs: dict[str, _Local] = {}

    # -- 启动 ---------------------------------------------------------------
    async def start(self, session_id: str, session: Session, message: str) -> None:
        """起一轮；同一会话已有在跑的轮次时抛 ``RunInProgress``。

        两道判定：**本地注册表**（这一轮就在这个进程里，含 task 是否已经结束）与
        **运行权**（Redis 版才拦得住别的 worker）。上一轮跑完的缓冲会被这一轮顶掉 ——
        ``buffer.open()`` 即清空，不另设过期清理（Redis 那边靠 TTL）。
        """
        current = self._runs.get(session_id)
        if current is not None and not current.done:
            raise RunInProgress(session_id)
        if not await self._lock.acquire(session_id):
            raise RunInProgress(session_id)
        await self._buffer.open(session_id)
        run = _Local()
        self._runs[session_id] = run
        run.task = asyncio.create_task(
            self._pump(session_id, session, message, run), name=f"run:{session_id}"
        )
        if self._lock.cross_process:
            # 只有跨进程那份才需要盯：本进程的取消是直接 cancel，没有可盯的东西
            run.watcher = asyncio.create_task(
                self._watch(session_id, run), name=f"watch:{session_id}"
            )

    async def _pump(
        self, session_id: str, session: Session, message: str, run: _Local
    ) -> None:
        """把 ``service.chat()`` 的帧搬进缓冲，直到它结束。

        ``session_id`` 用 ``start`` 传进来的那一个（缓冲的键就是它），**不要**改从
        ``session.public_id`` 现取 —— 两个来源在测试里就不相等，帧会写到没人读的键上
        （现象是订阅者永远等下去）。
        """
        try:
            async for frame in self._service.chat(session, message):
                await self._buffer.append(session_id, frame)
                run.event.set()
        except asyncio.CancelledError:
            raise  # 用户终止：不是故障，别吞（收尾在 finally）
        except Exception:
            # service.chat 自己接得住的地方（含 _run_agent）都会发 error 帧；走到这里说明
            # 它在更早处就炸了（意图分类、写用户记录）。原始文本不出口。
            logger.exception("本轮运行异常：public_id=%s", session_id)
            await self._buffer.append(
                session_id, {"type": "error", "error": RUN_ERROR_MESSAGE}
            )
        finally:
            # 收尾不能再抛：这里已经是无主的异步任务，抛出去只会变成一条
            # 「Task exception was never retrieved」的噪音（缓冲/锁自己会降级，不会上抛）
            try:
                await self._buffer.finish(session_id)
                await self._lock.release(session_id)
            except Exception:  # pragma: no cover —— 两个实现都自己吞了，兜个底
                logger.exception("收尾运行时状态时出错：public_id=%s", session_id)
            # 先让缓冲「确实结束了」再唤醒订阅者：反过来的话，醒来的订阅者可能看到
            # 「本地这轮完了、缓冲却说还在跑」，于是切回轮询 —— 白等一个周期。
            run.done = True
            run.event.set()

    async def _watch(self, session_id: str, run: _Local) -> None:
        """盯两件事：别处发来的终止请求（跨 worker），以及运行权的续期。

        本进程的取消不走这条路（``cancel`` 直接 cancel task，用户不用等一个轮询周期）；
        这里兜的是「用户在 A 上点了终止，而这一轮挂在 B 上」。轮次结束时自己退出。
        """
        loop = asyncio.get_running_loop()
        next_renew = loop.time() + self._renew_interval
        while True:
            await asyncio.sleep(self._poll)
            if run.task is None or run.task.done():
                return
            try:
                if await self._buffer.cancel_requested(session_id):
                    logger.info("收到别处的终止请求，取消本轮：public_id=%s", session_id)
                    run.task.cancel()
                    # 与本地取消对齐：缓冲丢掉（订阅者据此收尾，不留半截回放）
                    await self._buffer.clear(session_id)
                    return
                if loop.time() >= next_renew:
                    next_renew = loop.time() + self._renew_interval
                    if not await self._lock.renew(session_id):
                        logger.warning(
                            "会话运行权已不在本进程手里（另一个 worker 可能接手了）：%s",
                            session_id,
                        )
            except Exception:  # pragma: no cover —— 两个实现都自己吞了，兜个底
                logger.exception("检查运行状态时出错：public_id=%s", session_id)

    # -- 订阅 ---------------------------------------------------------------
    async def subscribe(
        self, session_id: str, after: int = 0
    ) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        """从帧号 ``after`` 之后开始产帧，直到本轮结束。

        会话没有在跑（也从没跑过）就直接返回，调用方得到一条空流。订阅者随时可以弃流
        （``aclose()`` / 被取消），**不会**影响运行本身。
        """
        if not await self._buffer.exists(session_id):
            return
        cursor = max(after, 0)
        local = self._runs.get(session_id)
        while True:
            for seq, frame in await self._buffer.read(session_id, cursor):
                cursor = seq
                yield seq, frame
            if await self._buffer.is_done(session_id):
                return
            # 持有者已经不在（进程被 kill / 重启）＝ 不会再有帧进来：直接收尾，别让**已经接上**
            # 的订阅者也跟着空转（理由同 ``is_running``）
            if not await self._lock.held(session_id):
                logger.warning("本轮已无持有者，订阅收尾：%s", session_id)
                return
            if local is not None and not local.done:
                # 本进程的轮次：清事件 → 再查一次 → 等事件。
                # 「查」在「清」之后、「等」在「查」之后：读完之后才追加的帧必然 set 过
                # 事件，于是不会漏唤醒（原来那句「clear 与查之间没有 await」多了一份存储
                # 之后不再成立，换成了这个顺序）。
                local.event.clear()
                if await self._buffer.read(session_id, cursor):
                    continue
                await local.event.wait()
            else:
                await asyncio.sleep(self._poll)

    # -- 状态与清理 ---------------------------------------------------------
    async def is_running(self, session_id: str) -> bool:
        """这个会话现在有没有一轮在跑（本进程的、或别的 worker 的）。"""
        local = self._runs.get(session_id)
        if local is not None and not local.done:
            return True
        if not await self._buffer.exists(session_id):
            return False
        if await self._buffer.is_done(session_id):
            return False
        # 缓冲还在、也没打 done，但**运行权不在了** ＝ 持有那一轮的进程已经死了（被 kill /
        # 重启）：不会再有人推进它，答案就是「没在跑」。只看缓冲会永远返回 True —— 实测撞到
        # 过一次：只剩 ``seq``（TTL 还有 29 分钟），界面一直显示「正在接续本轮」，
        # 订阅循环空转到 TTL 到期才自愈。
        if not await self._lock.held(session_id):
            logger.warning("本轮已无持有者（进程重启或被杀），按未在跑处理：%s", session_id)
            return False
        return True

    async def cancel(self, session_id: str) -> bool:
        """终止并丢弃某会话的运行（会话被删时、用户点「终止」时调用）。

        返回**是否真有一轮在跑** —— 路由据此回 ``{"cancelled": false}``：重按一次、
        或者这一轮刚好自己跑完了，都不算错误。已在跑的订阅者会在 pump 的 ``finally``
        里收到 ``done``，流随之结束（取消不产 error 帧：那不是故障）。

        本进程在跑的立即 cancel；挂在别的 worker 上的只能留个终止标记，由那边在
        ``_watch`` 里轮询到之后自己取消（并自己清缓冲）。
        """
        local = self._runs.pop(session_id, None)
        if local is not None and local.task is not None and not local.task.done():
            local.task.cancel()
            await self._buffer.clear(session_id)
            return True
        if await self._buffer.exists(session_id) and not await self._buffer.is_done(
            session_id
        ):
            await self._buffer.request_cancel(session_id)
            return True
        await self._buffer.clear(session_id)
        return False

    async def shutdown(self) -> None:
        """进程退出前收掉所有在跑的轮次，别让它们在后端已经拆池子时还去写库。"""
        tasks: list[asyncio.Task] = []
        for session_id, run in list(self._runs.items()):
            if run.watcher is not None and not run.watcher.done():
                run.watcher.cancel()
            if run.task is not None and not run.task.done():
                run.task.cancel()
                tasks.append(run.task)
            await self._buffer.clear(session_id)
            await self._lock.release(session_id)
        self._runs.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
