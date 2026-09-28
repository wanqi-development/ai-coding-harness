"""一轮运行的**帧缓冲**与**运行权**：进程内 / Redis 两套实现。

为什么要有这一层（见 CHANGELOG 里 RunHub 那条的「出界」）：缓冲原本就在 ``RunHub`` 的
进程内存里，多 worker 或重启即失效 —— 浏览器刷新后接回这一轮时可能落到**另一个**
worker 上，拿到一条空流；``cancel`` 也找不到那一轮。把「帧存在哪、谁有权跑」抽成两个
协议之后，Redis 版让多个 worker 共用同一份，没配 Redis 时进程内版就是原来的行为。

四条纪律：

1. **失败一律降级，不打断本轮。** 任何一次 Redis 调用出错就退回进程内那半并记 warning
   （``RedisBacked`` 负责）—— 一轮对话不该因为旁路状态存不进去而失败。
2. **帧号只认 ``INCR``，不拿 ``LLEN`` 推。** 缓冲有上限（``LTRIM`` 丢老帧），一旦丢过，
   ``LLEN`` 与「最后一帧的号」当场脱钩；号错一格，前端按游标续传就会重复或跳帧。
3. **少了缓冲就当「跑完了」**：``is_done`` 对「压根没有这份缓冲」也返回真 —— 订阅循环
   靠它收尾，把「不见了」当成「还在跑」会让订阅者永远等下去。
4. **取消＝标记 + 本地立即取消**：跨进程只能留个标记（别的 worker 的 task 我们碰不到），
   由持有那一轮的那个 worker 轮询到之后自己取消；本进程内的取消仍走快路径（直接 cancel），
   不让用户按一下等半拍。

Redis 键（前缀默认 ``harness:``，全部带 TTL，不会无限堆积）::

    harness:run:<session_id>:seq      自增帧号（也是「这一轮存在」的标记，open 时置 0）
    harness:run:<session_id>:frames   帧正文（JSON 一行一条，超过上限从头截）
    harness:run:<session_id>:done     本轮跑完的标记
    harness:run:<session_id>:cancel   跨进程的终止请求
    harness:run:<session_id>:lock     该会话的运行权（SET NX + 续期）
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ai_coding_harness.redis_cache import RedisBacked

logger = logging.getLogger(__name__)


class RunBuffer(Protocol):
    """一轮运行的帧缓冲（跨进程可见）。"""

    async def open(self, session_id: str) -> None:
        """开始新一轮：**清掉上一轮的帧**（与原来「重建缓冲区」同一个语义）。"""

    async def append(self, session_id: str, frame: dict[str, Any]) -> int:
        """追加一帧，返回它的帧号。"""

    async def read(self, session_id: str, after: int) -> list[tuple[int, dict[str, Any]]]:
        """帧号大于 ``after`` 的那些帧。"""

    async def exists(self, session_id: str) -> bool:
        """这个会话有一份缓冲（在跑的、或刚跑完留着重放的）。"""

    async def is_done(self, session_id: str) -> bool:
        """这一轮结束了（含缓冲已经不在的情形，见模块开头的纪律 3）。"""

    async def finish(self, session_id: str) -> None:
        """打上「跑完了」，缓冲留着给前端重放。"""

    async def clear(self, session_id: str) -> None:
        """丢掉这份缓冲（取消、会话销毁、进程退出）。"""

    async def request_cancel(self, session_id: str) -> None:
        """请求终止这一轮（本进程内不靠它，见 ``RedisRunLock`` 那段说明）。"""

    async def cancel_requested(self, session_id: str) -> bool:
        """有人请求过终止吗。"""


class RunLock(Protocol):
    """一个会话的**运行权**：同一会话同一时刻只该有一个 worker 在跑。"""

    #: 这份实现是不是跨进程的。``RunHub`` 只给跨进程的那份挂「盯终止请求 + 续期」的
    #: 后台任务：进程内的取消本来就够快，多一个 0.2s 的轮询只是白忙。
    cross_process: bool

    async def acquire(self, session_id: str) -> bool:
        """抢运行权；已被别人持有返回 ``False``。"""

    async def renew(self, session_id: str) -> bool:
        """续期（长轮次用）；已经不属于自己时返回 ``False``。"""

    async def release(self, session_id: str) -> None:
        """放手。"""


@dataclass
class _MemoryRun:
    """进程内那一轮的缓冲。``seq`` 与 ``frames`` 的长度始终相等。"""

    frames: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    cancel: bool = False


class MemoryRunBuffer:
    """进程内缓冲 —— 单 worker 下就是原来 ``RunHub._runs`` 里那份。"""

    def __init__(self) -> None:
        self._runs: dict[str, _MemoryRun] = {}

    async def open(self, session_id: str) -> None:
        self._runs[session_id] = _MemoryRun()

    async def append(self, session_id: str, frame: dict[str, Any]) -> int:
        run = self._runs.setdefault(session_id, _MemoryRun())
        run.frames.append(frame)
        return len(run.frames)

    async def read(self, session_id: str, after: int) -> list[tuple[int, dict[str, Any]]]:
        run = self._runs.get(session_id)
        if run is None:
            return []
        return [(i + 1, run.frames[i]) for i in range(max(after, 0), len(run.frames))]

    async def exists(self, session_id: str) -> bool:
        return session_id in self._runs

    async def is_done(self, session_id: str) -> bool:
        run = self._runs.get(session_id)
        return run is None or run.done

    async def finish(self, session_id: str) -> None:
        run = self._runs.get(session_id)
        if run is not None:  # 已经 clear 掉的就别再复活一个空壳
            run.done = True

    async def clear(self, session_id: str) -> None:
        self._runs.pop(session_id, None)

    async def request_cancel(self, session_id: str) -> None:
        self._runs.setdefault(session_id, _MemoryRun()).cancel = True

    async def cancel_requested(self, session_id: str) -> bool:
        run = self._runs.get(session_id)
        return run is not None and run.cancel


class MemoryRunLock:
    """进程内的「运行权」：**永远放行**。

    单进程里的互斥由 ``RunHub`` 的本地注册表兜着（同一个会话还有一轮在跑就拒绝），这份
    实现只为把接口凑齐 —— 在这里再拦一道只会让「取消之后立刻重开一轮」多等一拍。
    """

    cross_process = False

    async def acquire(self, session_id: str) -> bool:
        return True

    async def renew(self, session_id: str) -> bool:
        return True

    async def release(self, session_id: str) -> None:
        return None


class RedisRunBuffer(RedisBacked):
    """Redis 上的帧缓冲：多 worker 共用一份，刷新后落到哪个 worker 都能接回。"""

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "harness:",
        ttl_seconds: int = 3600,
        max_frames: int = 2000,
    ) -> None:
        super().__init__(client, what="运行缓冲")
        self._prefix = prefix
        self._ttl = ttl_seconds
        self._max = max_frames
        self._fallback = MemoryRunBuffer()

    def _key(self, session_id: str, part: str) -> str:
        return f"{self._prefix}run:{session_id}:{part}"

    # -- 接口 ---------------------------------------------------------------
    async def open(self, session_id: str) -> None:
        await self._fallback.open(session_id)
        await self.run(lambda: self._open_redis(session_id), _noop)

    async def append(self, session_id: str, frame: dict[str, Any]) -> int:
        return await self.run(
            lambda: self._append_redis(session_id, frame),
            lambda: self._fallback.append(session_id, frame),
        )

    async def read(self, session_id: str, after: int) -> list[tuple[int, dict[str, Any]]]:
        return await self.run(
            lambda: self._read_redis(session_id, after),
            lambda: self._fallback.read(session_id, after),
        )

    async def exists(self, session_id: str) -> bool:
        return await self.run(
            lambda: self._exists_redis(session_id),
            lambda: self._fallback.exists(session_id),
        )

    async def is_done(self, session_id: str) -> bool:
        return await self.run(
            lambda: self._is_done_redis(session_id),
            lambda: self._fallback.is_done(session_id),
        )

    async def finish(self, session_id: str) -> None:
        await self._fallback.finish(session_id)
        await self.run(lambda: self._finish_redis(session_id), _noop)

    async def clear(self, session_id: str) -> None:
        await self._fallback.clear(session_id)
        await self.run(lambda: self._clear_redis(session_id), _noop)

    async def request_cancel(self, session_id: str) -> None:
        await self._fallback.request_cancel(session_id)
        await self.run(lambda: self._cancel_redis(session_id), _noop)

    async def cancel_requested(self, session_id: str) -> bool:
        return await self.run(
            lambda: self._cancel_requested_redis(session_id),
            lambda: self._fallback.cancel_requested(session_id),
        )

    # -- Redis 那半 ---------------------------------------------------------
    async def _open_redis(self, session_id: str) -> None:
        # 清旧帧与置号进一个事务：分开写的话，订阅者可能在中间态里看到「帧没了、号也没了」，
        # 而 ``_is_done_redis`` 把「没有号」判成「跑完了」—— 那一轮会被提前收尾。
        pipe = self._redis.pipeline(transaction=True)
        pipe.delete(
            self._key(session_id, "frames"),
            self._key(session_id, "done"),
            self._key(session_id, "cancel"),
        )
        # 置 0 而不是删掉：这一步之后「这一轮存在、只是还没有帧」就该被订阅者看见
        pipe.set(self._key(session_id, "seq"), "0", ex=self._ttl)
        await pipe.execute()

    async def _append_redis(self, session_id: str, frame: dict[str, Any]) -> int:
        """取号与入列必须**一次事务**：分两条命令写，读侧就能看见「号涨了、帧还没进列表」。

        读侧靠 ``首帧号 = 号 - 长度 + 1`` 反推，撞上那个中间态就会把所有帧整体多算一号、
        游标跳过去把尾巴上的一帧**丢掉**（回归用例
        ``test_frame_numbers_survive_reading_while_a_frame_is_being_written`` 钉着这条）。
        截断与续期顺手也放进同一个事务 —— 不额外花往返，还能让「长度」与「号」始终同步。
        """
        seq_key = self._key(session_id, "seq")
        frames = self._key(session_id, "frames")
        pipe = self._redis.pipeline(transaction=True)
        pipe.incr(seq_key)
        pipe.rpush(frames, json.dumps(frame, ensure_ascii=False))
        # 无条件截断：列表没超上限时 LTRIM 是空操作，换来的是「不必先知道长度」
        pipe.ltrim(frames, -self._max, -1)
        # 每帧续一次 TTL：一轮可能跑很久，只在 open 时设会让缓冲半路过期
        pipe.expire(seq_key, self._ttl)
        pipe.expire(frames, self._ttl)
        results = await pipe.execute()
        return int(results[0])

    async def _read_redis(
        self, session_id: str, after: int
    ) -> list[tuple[int, dict[str, Any]]]:
        """长度与号要读**同一瞬间**的。

        分两条命令读，中间可能夹着一次完整写入：拿到旧的长度配新的号，反推出来的首帧号
        同样偏一号（与写侧那道缝是同一类问题，只是方向相反）。两条读放进一个事务就一致了。

        ``LRANGE`` 仍在事务外：它是**追加**语义，晚一点读只会多拿到尾巴上的新帧，已有元素
        的下标不变。**已知的窄缝**：若正好在这两次读之间触发了截断（要一轮攒够
        ``RUN_BUFFER_MAX_FRAMES``，默认 2000 帧），首帧号会算高、这一轮下次轮询才补齐 ——
        截断与入列同在写事务里，窗口是微秒级，按当前用法不值当再引 Lua。
        """
        frames = self._key(session_id, "frames")
        pipe = self._redis.pipeline(transaction=True)
        pipe.llen(frames)
        pipe.get(self._key(session_id, "seq"))
        size, last = await pipe.execute()
        size = int(size)
        if not size or last is None:
            return []
        # 截过老帧之后，列表第一项的号是 last - size + 1，不能拿下标当帧号
        first = int(last) - size + 1
        start = max(int(after) - first + 1, 0)
        if start >= size:
            return []
        raw = await self._redis.lrange(frames, start, -1)
        return [(first + start + i, json.loads(item)) for i, item in enumerate(raw)]

    async def _exists_redis(self, session_id: str) -> bool:
        return bool(
            await self._redis.exists(
                self._key(session_id, "seq"), self._key(session_id, "done")
            )
        )

    async def _is_done_redis(self, session_id: str) -> bool:
        if await self._redis.exists(self._key(session_id, "done")):
            return True
        return not await self._redis.exists(self._key(session_id, "seq"))

    async def _finish_redis(self, session_id: str) -> None:
        await self._redis.set(self._key(session_id, "done"), "1", ex=self._ttl)

    async def _clear_redis(self, session_id: str) -> None:
        await self._redis.delete(
            self._key(session_id, "seq"),
            self._key(session_id, "frames"),
            self._key(session_id, "done"),
            self._key(session_id, "cancel"),
        )

    async def _cancel_redis(self, session_id: str) -> None:
        await self._redis.set(self._key(session_id, "cancel"), "1", ex=self._ttl)

    async def _cancel_requested_redis(self, session_id: str) -> bool:
        return bool(await self._redis.exists(self._key(session_id, "cancel")))


class RedisRunLock(RedisBacked):
    """Redis 上的运行权：``SET NX EX`` + 续期。

    **已知的窄缝**（写在这里，免得下次有人以为它严丝合缝）：续期是「``GET`` 看一眼是不是
    我的 → ``EXPIRE``」两条命令，之间理论上可能刚好过期并被另一个 worker 抢走，于是我们
    把**别人**的锁续了一次。后果是那个 worker 的接管被推迟（最多一个 TTL），不会更糟 ——
    要严丝合缝得引 Lua 脚本或 fencing token，按当前用法不值这个复杂度。同理，持有者进程
    被 kill 之后锁最多存活一个 TTL（默认 120s），这就是「进程挂了」的兜底。
    """

    cross_process = True

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "harness:",
        ttl_seconds: int = 120,
        owner: str | None = None,
    ) -> None:
        super().__init__(client, what="会话运行权")
        self._prefix = prefix
        self._ttl = ttl_seconds
        self._owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}run:{session_id}:lock"

    async def acquire(self, session_id: str) -> bool:
        async def primary() -> bool:
            return bool(
                await self._redis.set(
                    self._key(session_id), self._owner, nx=True, ex=self._ttl
                )
            )

        async def fallback() -> bool:
            # 降级＝退回单进程语义：本地注册表已经在上面拦过一道，这里放行
            return True

        return await self.run(primary, fallback)

    async def renew(self, session_id: str) -> bool:
        async def primary() -> bool:
            if await self._redis.get(self._key(session_id)) != self._owner:
                return False
            await self._redis.expire(self._key(session_id), self._ttl)
            return True

        return await self.run(primary, _true)

    async def release(self, session_id: str) -> None:
        async def primary() -> None:
            if await self._redis.get(self._key(session_id)) == self._owner:
                await self._redis.delete(self._key(session_id))

        await self.run(primary, _noop)


async def _noop() -> None:
    return None


async def _true() -> bool:
    return True
