"""会话沙箱的可用性代理 —— 每次调用前探活，失效先重连、再重建。

沙箱会被 OpenSandbox 侧回收（空闲、超时、节点故障），而进程内的会话仍持有旧句柄，
于是后续工具调用抛出 `SandboxConnectionException`。本代理把「探活 + 恢复」收拢在一处：
所有沙箱调用经它转发，探活失败或调用抛传输层异常时自动恢复，调用方无感。

恢复分两级，代价差很多：

- **重连**（`SandboxProvider.reconnect`）：沙箱还在，只是连接废了 → 容器与容器内状态保留；
- **重建**（`SandboxProvider.create`）：沙箱真被回收了 → 挂同一工作区卷新建（clone 的仓库在卷里，不丢）。

传输层之外的失败（命令非 0 退出、参数错误等）不触发恢复 —— 那是业务结果，不是故障。
例外是「沙箱句柄失效」的 API 错误（404/410 已被回收、502/503 网关瞬时故障）：它与传输层
失败同义，同样走恢复；而 429 限流及其余 4xx 不恢复，重建解决不了还会白白丢掉容器。

**续期**（``renew_if_due``）：沙箱有 TTL（建时传 ``SANDBOX_TIMEOUT_MINUTES``），到期就被
OpenSandbox 侧回收。到期后再调用虽然也能恢复，但重建会丢容器里非卷的内容，代价不小；
所以活跃会话按 ``SANDBOX_RENEW_INTERVAL_SECONDS`` 节流续期一次（把到期时间推后一个 TTL）。
续期走同一个代理：失败只记日志（不影响本次调用），其中「句柄失效」那类照样触发恢复。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import TypeVar

from opensandbox.exceptions import (
    SandboxApiException,
    SandboxInternalException,
    SandboxUnhealthyException,
)
from opensandbox.models.filesystem import EntryInfo
from opensandbox.models.sandboxes import Volume

from ai_coding_harness.config import Settings
from ai_coding_harness.sandbox.client import CommandResult, SandboxClient
from ai_coding_harness.sandbox.provider import GONE_API_STATUS, SandboxProvider

logger = logging.getLogger(__name__)

# 传输层失败才触发恢复（SandboxInternalException 覆盖连接/超时两类）。
_RECOVERABLE = (SandboxInternalException, SandboxUnhealthyException)

# API 层的可恢复状态码：沙箱已不存在（404/410）或网关侧瞬时故障（502/503）。
# 429 限流与其余 4xx（参数、权限）不恢复 —— 重建解决不了，还会白白丢掉容器。
_RECOVERABLE_API_STATUS = frozenset({404, 410, 502, 503})

#: 长调用期间的心跳间隔：**一次调用**跑几十分钟时（一次 build、一次大 diff），中途没有别的
#: 沙箱调用，惰性续期就轮不到 —— 到期是按绝对时间回收的，命令会被中途杀掉。所以调用进行中
#: 也跑一个心跳，每隔这么久检查一次「该续期了吗」（真正的节流仍在 ``_maybe_renew`` 里）。
#: 取 30 秒：远小于默认 TTL（60 分钟），又不会给沙箱添压力。
RENEW_TICK_SECONDS = 30.0


def _is_recoverable(exc: BaseException) -> bool:
    """判断异常是否代表「沙箱句柄失效」，即恢复（重连/重建）能解决。"""
    if isinstance(exc, _RECOVERABLE):
        return True
    return (
        isinstance(exc, SandboxApiException)
        and exc.status_code in _RECOVERABLE_API_STATUS
    )

T = TypeVar("T")


class OpenSandboxProxy:
    """代理一个会话的沙箱：探活、恢复、重建，对外保持 ``SandboxClient`` 同形接口。"""

    def __init__(
        self,
        *,
        settings: Settings,
        provider: SandboxProvider,
        client: SandboxClient,
        public_id: str,
        volumes: list[Volume] | None = None,
        env: dict[str, str] | None = None,
        on_rebuild: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._client = client
        self._public_id = public_id
        self._volumes = volumes
        self._env = env
        self._on_rebuild = on_rebuild
        self._generation = 0  # 每次恢复自增，用于让并发调用者感知句柄已换代
        self._lock = asyncio.Lock()
        self._renew_lock = asyncio.Lock()  # 续期单飞（与恢复分开，避免两把锁互相等）
        self._checked_at = 0.0  # 上次探活成功的时间（monotonic）
        # 上次续期（或刚接手这个沙箱）的时间。初值取构造时刻：新建的沙箱刚拿到完整 TTL，
        # 不该在第一次调用时就续期。
        self._renewed_at = time.monotonic()
        self._closed = False

    @property
    def id(self) -> str:
        return self._client.id

    # -- 与 SandboxClient 同形的接口 --------------------------------------

    async def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: timedelta | None = None,
        envs: dict[str, str] | None = None,
    ) -> CommandResult:
        return await self._call(
            lambda c: c.run(command, cwd=cwd, timeout=timeout, envs=envs)
        )

    async def write_file(self, path: str, content: str) -> None:
        await self._call(lambda c: c.write_file(path, content))

    async def read_file(self, path: str) -> str:
        return await self._call(lambda c: c.read_file(path))

    async def list_dir(self, path: str, *, depth: int | None = None) -> list[EntryInfo]:
        return await self._call(lambda c: c.list_dir(path, depth=depth))

    async def search(self, path: str, pattern: str) -> list[EntryInfo]:
        return await self._call(lambda c: c.search(path, pattern))

    async def destroy(self) -> None:
        """销毁沙箱；之后任何调用都会失败，避免静默复活已销毁的会话。

        沙箱被 OpenSandbox 侧先一步回收时 kill 会返 404 —— 目标状态已经达成，
        按成功处理，否则会话会卡在「未销毁」状态删不掉。其余失败照抛。
        """
        self._closed = True
        try:
            await self._provider.destroy(self._client)
        except SandboxApiException as exc:
            if exc.status_code not in GONE_API_STATUS:
                raise
            logger.info("沙箱已不存在，跳过销毁：sandbox=%s", self._client.id)

    async def renew_if_due(self) -> None:
        """按节流续期沙箱寿命（轮首 / 每次沙箱调用前都会走这里）。

        失败只记日志、不抛：续期是**保命**动作，不是本轮的业务结果 —— 真过期了还有
        「探活失败 → 重连 / 重建」那条既有兜底。
        """
        await self._maybe_renew()

    # -- 内部：探活与恢复 --------------------------------------------------

    async def _call(self, op: Callable[[SandboxClient], Awaitable[T]]) -> T:
        await self._ensure_alive()
        generation = self._generation
        try:
            # 调用进行中也维持续期心跳：这一步覆盖的是「一次调用自己跑很久」那种场景，
            # 与 ``_ensure_alive`` 里那次（调用之前）合起来，长会话的两种形态都盖到了。
            async with self._renewing():
                return await op(self._client)
        except Exception as exc:
            if not _is_recoverable(exc):
                raise
            logger.warning(
                "沙箱调用失败，触发恢复：public_id=%s sandbox=%s err=%s",
                self._public_id,
                self._client.id,
                exc,
            )
            await self._recover(generation)
            return await op(self._client)

    async def _ensure_alive(self) -> None:
        """按 TTL 缓存探活结果，避免给每次工具调用都加一次网络往返。"""
        if self._closed:
            raise RuntimeError(f"沙箱代理已关闭：public_id={self._public_id}")
        await self._maybe_renew()
        ttl = self._settings.sandbox_health_probe_ttl_seconds
        now = time.monotonic()
        if ttl > 0 and now - self._checked_at < ttl:
            return
        generation = self._generation
        if await self._client.is_healthy():
            self._checked_at = now
            return
        logger.warning(
            "沙箱探活失败，触发恢复：public_id=%s sandbox=%s",
            self._public_id,
            self._client.id,
        )
        await self._recover(generation)

    async def _maybe_renew(self) -> None:
        """距上次续期超过间隔就续一次；没到间隔时只是一次浮点比较。"""
        interval = self._settings.sandbox_renew_interval_seconds
        if interval <= 0:
            return
        now = time.monotonic()
        if now - self._renewed_at < interval:
            return
        async with self._renew_lock:
            if now - self._renewed_at < interval:
                return  # 并发调用里已经有人续过
            # 先记「尝试过」再发请求：失败也不至于让后续每个调用都重试一遍
            self._renewed_at = now
            timeout = timedelta(minutes=self._settings.sandbox_timeout_minutes)
            try:
                expires_at = await self._client.renew(timeout)
            # 续期是保命动作：任何异常都只记日志，绝不冒到调用方（真要过期了还有恢复兜底）
            except Exception as exc:  # noqa: BLE001
                if _is_recoverable(exc):
                    logger.warning(
                        "沙箱续期失败（句柄可能已失效），触发恢复：public_id=%s sandbox=%s err=%s",
                        self._public_id,
                        self._client.id,
                        exc,
                    )
                    await self._recover(self._generation)
                    return
                logger.warning(
                    "沙箱续期失败（不影响本次调用）：public_id=%s sandbox=%s err=%s",
                    self._public_id,
                    self._client.id,
                    exc,
                )
                return
            logger.info(
                "沙箱已续期：public_id=%s sandbox=%s 到期=%s",
                self._public_id,
                self._client.id,
                expires_at,
            )

    @contextlib.asynccontextmanager
    async def _renewing(self) -> AsyncIterator[None]:
        """在 ``with`` 体（一次沙箱调用）进行期间跑续期心跳；关闭续期时是空操作。

        心跳任务只做「到点了就续」，异常一律吞掉并记日志 —— 它绝不能让正在跑的那次调用失败。
        """
        interval = self._settings.sandbox_renew_interval_seconds
        if interval <= 0:
            yield
            return
        beat = asyncio.create_task(self._renew_heartbeat())
        try:
            yield
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

    async def _renew_heartbeat(self) -> None:
        interval = self._settings.sandbox_renew_interval_seconds
        tick = min(RENEW_TICK_SECONDS, interval)
        while True:
            await asyncio.sleep(tick)
            try:
                await self._maybe_renew()
            except asyncio.CancelledError:
                raise
            # 心跳异常绝不能让在跑的那次调用失败（ruff 已认这里的多分支 try，故不加 noqa）
            except Exception:
                logger.warning(
                    "续期心跳异常（忽略，不影响本次调用）：public_id=%s sandbox=%s",
                    self._public_id,
                    self._client.id,
                    exc_info=True,
                )

    async def _recover(self, generation: int) -> None:
        """恢复沙箱（单飞）：并发调用同时发现故障时只恢复一次，其余复用新句柄。"""
        if self._closed:
            raise RuntimeError(f"沙箱代理已关闭：public_id={self._public_id}")
        async with self._lock:
            if self._generation != generation:
                return  # 已有其他调用完成恢复，直接用新句柄
            await self._rebuild()

    async def _rebuild(self) -> None:
        old_id = self._client.id
        client = await self._provider.reconnect(old_id)
        if client is not None:
            self._client = client
            self._generation += 1
            self._checked_at = time.monotonic()
            # 换了句柄就重置续期时钟：重连回来的沙箱寿命未知，但没必要立刻再续一次
            self._renewed_at = self._checked_at
            logger.warning(
                "沙箱已重连：public_id=%s sandbox=%s", self._public_id, old_id
            )
            return

        client = await self._provider.create(self._volumes, self._env)
        self._client = client
        self._generation += 1
        self._checked_at = time.monotonic()
        self._renewed_at = self._checked_at  # 新建的沙箱刚拿到完整 TTL
        logger.warning(
            "沙箱已重建：public_id=%s %s → %s",
            self._public_id,
            old_id,
            client.id,
        )
        if self._on_rebuild is not None:
            await self._on_rebuild(client.id)
