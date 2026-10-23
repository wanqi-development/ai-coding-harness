"""``RunHub`` 及其路由的测试假件（照 ``v3_events.py`` 的做法共用一份）。

``GatedService`` 是这里的关键：一帧一放行，才测得了「跑到一半」—— 断流、晚订阅、
并发发起都发生在一轮还没跑完的时候。
"""

from __future__ import annotations

import asyncio
from typing import Any


class StubSession:
    """运行只需要它的 ``public_id``（出错时进日志）。"""

    public_id = "s-1"


class ScriptedService:
    """按脚本一口气吐完。``frames`` 可改，用来测「下一轮」。"""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.frames = frames
        self.calls: list[dict[str, Any]] = []

    async def chat(self, session, message, **extra: Any):
        self.calls.append({"message": message, **extra})
        for frame in self.frames:
            await asyncio.sleep(0)
            yield frame


class GatedService:
    """一帧一放行，用来测「跑到一半」的场景。"""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = frames
        self._gate = asyncio.Event()
        self.calls: list[dict[str, Any]] = []

    async def chat(self, session, message, **extra: Any):
        self.calls.append({"message": message, **extra})
        for frame in self._frames:
            await self._gate.wait()
            self._gate.clear()
            yield frame

    async def advance(self, count: int) -> None:
        """放行 ``count`` 帧，并等它们真的落进缓冲。"""
        for _ in range(count):
            self._gate.set()
            await settle()


class BoomService:
    """在它自己接不住的地方炸掉（``service.chat`` 只管得住中段）。"""

    async def chat(self, session, message):
        yield {"type": "ai", "content": "开头"}
        raise ValueError("原始异常里夹着工具输出")


async def settle() -> None:
    """让够多次控制权：一帧要过生成器与 ``async for`` 两层才落到缓冲里。"""
    for _ in range(10):
        await asyncio.sleep(0)
