"""Redis 旁路状态（运行缓冲 / 熔断状态）的共用底子：连接与「坏了就降级」的包装。

**Redis 是可选件。** ``REDIS_URL`` 留空 = 不启用，所有旁路状态都留在进程内（就是
2026-09-26 之前的行为）。配了才连，而且连接失败**不阻塞启动** —— 除非显式声明
``REDIS_REQUIRED=true``（多 worker 部署时该开：此时「静默退回进程内」意味着你以为共享
的东西其实各 worker 一份，比启动失败更糟）。

**降级纪律**：任何一次 Redis 调用出错，这个包装就**整体**退回进程内实现，并记一条
warning —— 一轮对话不该因为旁路状态存不进去而失败。第一次失败即永久降级（不再每次
重试），否则 Redis 半死时每一帧都要白等一次超时。

本模块**不在导入期 import redis**：没装这个包（或没配 Redis）的部署与单测照常跑，
只有真要连的时候才 import。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from urllib.parse import urlsplit, urlunsplit

from ai_coding_harness.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RedisBacked:
    """持有 Redis 客户端与「一次失败就整体降级」的状态。

    子类只管写 Redis 那半（``primary``），进程内那半（``fallback``）由子类在每次调用时
    顺带传进来 —— 这样两个实现的语义在同一个方法里对照着看，不会各自漂移。
    """

    def __init__(self, client: Any, *, what: str) -> None:
        self._redis = client
        self._what = what
        self._degraded = False

    @property
    def degraded(self) -> bool:
        """已经退回进程内实现了吗（测试与日志用）。"""
        return self._degraded

    async def run(
        self,
        primary: Callable[[], Awaitable[T]],
        fallback: Callable[[], Awaitable[T]],
    ) -> T:
        """先在 Redis 上做；出错就永久退回进程内那套。"""
        if not self._degraded:
            try:
                return await primary()
            # 旁路状态：任何错都只该降级，不该往上冒
            except Exception as exc:  # noqa: BLE001
                self._degraded = True
                logger.warning(
                    "Redis 上的 %s 不可用，本进程后续退回进程内实现：%s: %s",
                    self._what,
                    type(exc).__name__,
                    exc,
                )
        return await fallback()


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
