"""Redis 旁路状态（运行缓冲 / 熔断状态）的共用底子：连接与「坏了就降级」的包装。

**Redis 是可选件。** ``REDIS_URL`` 留空 = 不启用，所有旁路状态都留在进程内（就是
2026-09-26 之前的行为）。配了才连，而且连接失败**不阻塞启动** —— 除非显式声明
``REDIS_REQUIRED=true``（多 worker 部署时该开：此时「静默退回进程内」意味着你以为共享
的东西其实各 worker 一份，比启动失败更糟）。

**降级纪律**：任何一次 Redis 调用出错，这个包装就**整体**退回进程内实现，并记一条
warning（带「这条命令花了多久才失败」）—— 一轮对话不该因为旁路状态存不进去而失败。
失败后有一段**冷却窗口**（``REDIS_DEGRADE_COOLDOWN_SECONDS``，默认 60s）：窗口内不再
试 Redis（否则 Redis 半死时每一帧都要白等一次超时），**窗口过了再试一次**，成功即恢复。
窗口设 0 ＝ 退回旧纪律「一次失败即永久降级」。

> 「冷却后重试」是 2026-10-11 改的（原来是「第一次失败即永久降级」）。触发点是一次实测：
> ``127.0.0.1:6379`` 其实是 **SSH 隧道**（PING 中位 62ms / p95 92ms），一次 TCP 重传就把
> 一条命令顶过 1 秒的超时；而「永久」意味着这个进程此后彻底失去跨进程能力、要重启才回来。

另外每条命令外面还有一层**兜底超时**（``command_guard_seconds``＝socket 超时的 3 倍）：
``socket_timeout`` 只管读，隧道半死时**写**能一直阻塞（实测 556 秒才由 TCP 放弃）。

本模块**不在导入期 import redis**：没装这个包（或没配 Redis）的部署与单测照常跑，
只有真要连的时候才 import。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from urllib.parse import urlsplit, urlunsplit

from ai_coding_harness.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: 「一条命令最多等多久」＝ socket 超时的几倍。
#:
#: ``socket_timeout`` 只管**读**：隧道半死时**写**会一直阻塞，实测一条命令等了
#: **556 秒**才由 TCP 自己放弃（2026-10-12，SSH 隧道被重建的那一刻）。所以外面再套一层
#: 整条调用的兜底超时：超了就按失败处理，交给冷却纪律重试，而不是让这一条拖住九分钟。
#: 倍数取 3 —— 一条命令可能是「取号 + 入列 + 过期」这种几次往返，但都在隧道量级。
COMMAND_GUARD_FACTOR = 3


def command_guard_seconds(settings: Settings) -> float:
    """一条 Redis 命令的兜底超时（秒）：由配置的 socket 超时推出来，不另设一个键。

    为什么跟着 socket 超时走：配置放大超时（远端 / 慢隧道）的人，需要兜底也一起放大 ——
    写死一个常数会让「30 秒的 socket 超时」被 15 秒的兜底先掐掉，与配置的意图相反。
    ``REDIS_TIMEOUT_SECONDS=0``（redis-py 语义＝不超时）时返回 0 ＝ 不兜底。
    """
    return max(0.0, settings.redis_timeout_seconds) * COMMAND_GUARD_FACTOR


class RedisBacked:
    """持有 Redis 客户端与「失败后退回进程内、冷却一段时间再试」的状态。

    子类只管写 Redis 那半（``primary``），进程内那半（``fallback``）由子类在每次调用时
    顺带传进来 —— 这样两个实现的语义在同一个方法里对照着看，不会各自漂移。
    """

    def __init__(
        self,
        client: Any,
        *,
        what: str,
        cooldown_seconds: float = 60.0,
        guard_seconds: float = 15.0,
    ) -> None:
        self._redis = client
        self._what = what
        #: 降级窗口长度。一次失败之后这么多秒内不再试 Redis（避免半死时每一帧都白等一次
        #: 超时），但**过了这段就再试一次** —— 见 ``run``。
        self._cooldown = max(0.0, cooldown_seconds)
        #: 降级窗口的截止时刻（``time.monotonic``）；0 ＝ 没在降级
        self._degraded_until = 0.0
        #: 降级之后还没试成功过 —— 只记一次「已恢复」
        self._recovering = False
        #: 整条命令的兜底超时（见 ``command_guard_seconds``）：``socket_timeout`` 管不到
        #: 写阻塞，这一层保证「一条命令」不会把整轮对话拖住。0 ＝ 不兜底。
        self._guard = max(0.0, guard_seconds)

    @property
    def degraded(self) -> bool:
        """**现在**处于降级窗口吗（测试与日志用）。"""
        return time.monotonic() < self._degraded_until

    async def run(
        self,
        primary: Callable[[], Awaitable[T]],
        fallback: Callable[[], Awaitable[T]],
    ) -> T:
        """先在 Redis 上做；失败就退回进程内那套，**冷却一段时间后再试一次**。

        为什么不是「一次失败即永久降级」：那条纪律的初衷是「Redis 半死时别让每一帧都白等一次
        超时」，在**本机 Redis** 的假设下没问题。但 Redis 走隧道 / 在远端时（2026-10-11 实测：
        ``127.0.0.1:6379`` 其实是 SSH 隧道，PING 中位 62ms / p95 92ms，服务端慢日志是空的），
        一次瞬时抖动（TCP 重传）就能把一条命令顶过超时 —— 那会儿「永久」意味着**这个进程此后
        彻底失去跨进程能力、要重启才回来**，代价与收益完全不成比例。所以：失败 → 冷却 N 秒 →
        再试一次；试成功即恢复，再失败就重新进冷却。

        日志里带上**这条命令花了多久才失败**：只说「不可用」看不出是服务端慢还是链路抖，
        而 62ms 的基线与 1000ms 的超时之间差的是一个 TCP 重传。

        ``cooldown_seconds`` 设成 0（或负数）＝ 退回旧纪律「一次失败即永久降级」，
        留给「宁可不要跨进程能力也别每帧白等」的部署。
        """
        if self.degraded:
            return await fallback()
        started = time.perf_counter()
        try:
            call = primary()
            # 兜底超时：``socket_timeout`` 只管读，写阻塞能拖到 TCP 自己放弃（实测 556 秒）。
            # 超时按失败处理 —— 下面同一个 except 会把这条命令记成降级并走进程内。
            result = await (asyncio.wait_for(call, timeout=self._guard) if self._guard else call)
        # 旁路状态：任何错都只该降级，不该往上冒
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - started) * 1000
            if self._cooldown > 0:
                self._degraded_until = time.monotonic() + self._cooldown
                self._recovering = True
                logger.warning(
                    "Redis 上的 %s 不可用（这条命令花了 %.0f ms 才失败），接下来 %.0f 秒退回"
                    "进程内，过了会再试一次：%s: %s",
                    self._what,
                    elapsed_ms,
                    self._cooldown,
                    type(exc).__name__,
                    exc,
                )
            else:
                self._degraded_until = float("inf")
                logger.warning(
                    "Redis 上的 %s 不可用（这条命令花了 %.0f ms 才失败），本次退回进程内且不再"
                    "重试（降级窗口被设成了 0）：%s: %s",
                    self._what,
                    elapsed_ms,
                    type(exc).__name__,
                    exc,
                )
            return await fallback()
        if self._recovering:
            self._recovering = False
            logger.info("Redis 上的 %s 已恢复（冷却结束后的重试成功）", self._what)
        return result


def normalize_url(raw: str) -> str:
    """没写 scheme 时按 ``redis://`` 处理。

    ``REDIS_URL=127.0.0.1:6379`` 是很自然的写法（2026-09-27 用户就是这么填的），而
    redis-py 的 ``parse_url`` 对没有 scheme 的串直接抛 ``ValueError`` —— 那会让「配了
    Redis」变成「静默退回进程内」，只在日志里留一行。所以这里补一下，不让人踩。
    """
    return raw if "://" in raw else f"redis://{raw}"


def _rebuild(url: str, password: str) -> str:
    """把 URL 里的密码换掉（``password`` 为空串＝把密码段整个去掉，用户名保留）。"""
    parts = urlsplit(url)
    if parts.password is None and not password:
        return url
    host = parts.hostname or ""
    if ":" in host:  # IPv6 要还原方括号
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    user = parts.username or ""
    if user and password:
        userinfo = f"{user}:{password}@{host}"
    elif user:
        userinfo = f"{user}@{host}"
    elif password:  # 有密码没用户名：redis://:pw@host 也是合法写法
        userinfo = f":{password}@{host}"
    else:
        userinfo = host
    return urlunsplit((parts.scheme, f"{userinfo}{port}", parts.path, parts.query, parts.fragment))


def strip_password(url: str) -> str:
    """去掉 URL userinfo 里的密码（用户名留着）—— 显式的 ``REDIS_PASSWORD`` 优先时用。"""
    return _rebuild(url, "")


def mask_url(url: str) -> str:
    """日志用：把 URL 里的密码换成 ``***``（用户名保留），**绝不把密码打进日志**。"""
    try:
        parts = urlsplit(url)
        if parts.password is None:
            return url
    except ValueError:  # URL 本身不合法（端口写成非数字等）：原样返回，别在日志里再抛一次
        return url
    return _rebuild(url, "***")


def default_client_factory(settings: Settings) -> Any:
    """默认的客户端工厂：真的去建一个 ``redis.asyncio`` 客户端。

    延迟 import：没装 redis 包（或没配 Redis）的部署与单测照常跑，只有真要连的时候才
    import。单测可以传一个假工厂进来，于是「连不上怎么办」这条也能在没装包的机器上验。

    密码的口径（2026-09-27）：**显式的 ``REDIS_PASSWORD`` 优先** —— 两边都写时把 URL 里
    那份剥掉。实测 redis-py 的 ``from_url(url, password=…)`` **不会**覆盖 URL 里的密码
    （URL 里那个赢），不剥就会出现「改了 ``REDIS_PASSWORD`` 却不生效」这种怪事。
    """
    from redis.asyncio import from_url

    url = normalize_url(settings.redis_url)
    password = settings.redis_password
    if password:
        url = strip_password(url)
    return from_url(
        url,
        password=password or None,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
    )


def _display(settings: Settings) -> str:
    """日志与报错里显示的那个地址：归一化 + 打码。"""
    return mask_url(normalize_url(settings.redis_url)) if settings.redis_url else ""


async def build_redis(
    settings: Settings,
    *,
    factory: Callable[[Settings], Any] = default_client_factory,
) -> Any | None:
    """按配置建一个异步 Redis 客户端；没配或连不上（且不 required）时返回 ``None``。

    ``None`` 的语义是「这次没有 Redis」：调用方据此用进程内实现。连接在这里就验一次
    （``ping``），免得把「配了个错的地址」拖到用户点下第一轮对话时才炸。
    """
    if settings.is_standalone:
        logger.info("standalone 模式固定使用进程内运行状态，忽略 Redis 配置")
        return None
    if not settings.redis_url:
        return None

    try:
        client = factory(settings)
    # 建不起来最常见的原因就是没装 redis 包（导入在工厂里），其次是地址写错；
    # 两种都按「这台机器这次没有 Redis」处理，除非显式要求必需
    except Exception as exc:
        message = f"Redis 客户端建不起来（{_display(settings)}）：{type(exc).__name__}: {exc}"
        if settings.redis_required:
            raise RuntimeError(message) from exc
        logger.warning("%s；本次退回进程内实现（运行缓冲 / 熔断状态不再跨进程）", message)
        return None
    try:
        await client.ping()
    # 连不上是配置/环境问题，不是崩溃：收掉半开的客户端，按 required 决定是否继续
    except Exception as exc:
        await _close_quietly(client)
        message = f"Redis 不可用（{_display(settings)}）：{type(exc).__name__}: {exc}"
        if settings.redis_required:
            raise RuntimeError(message) from exc
        logger.warning("%s；本次退回进程内实现（运行缓冲 / 熔断状态不再跨进程）", message)
        return None
    logger.info("已连上 Redis，运行缓冲与熔断状态走 %s", _display(settings))
    return client


async def _close_quietly(client: Any) -> None:
    try:
        await client.aclose()
    # 收尾失败没有补救动作，也不该盖住原始错误
    except Exception:
        logger.debug("关闭 Redis 客户端时出错", exc_info=True)
