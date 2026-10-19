"""Redis 装配：没配就不启用、连不上就降级、声明必需才拒绝启动。

真连 Redis 的路径在没装 ``redis`` 包（或没有实例）的机器上跑不了，所以客户端由**工厂**
注入 —— 这里拿假客户端验「连不上怎么办」这两条，比连一个真实例更稳。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pytest

from ai_coding_harness import redis_cache as redis_cache_module
from ai_coding_harness.config import Settings
from ai_coding_harness.redis_cache import (
    RedisBacked,
    build_redis,
    default_client_factory,
    mask_url,
    normalize_url,
    strip_password,
)
from tests.fake_redis import FakeRedis


def _settings(**overrides: object) -> Settings:
    """测试用的设置：**不读 .env**。

    ``Settings()`` 默认会读本机 ``.env`` —— 那会把真实的 ``REDIS_URL`` / ``REDIS_PASSWORD``
    渗进用例（实测踩过：断言失败时真密码被原样打进 pytest 输出）。所以这里显式关掉 env 文件。
    """
    base: dict[str, object] = {"redis_url": "", "redis_password": ""}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


async def test_no_url_means_no_redis() -> None:
    """不配就是不用：连客户端都不建（工厂一次都不该被调用）。"""
    calls: list[Settings] = []

    def factory(settings: Settings) -> FakeRedis:
        calls.append(settings)
        return FakeRedis()

    assert await build_redis(_settings(redis_url=""), factory=factory) is None
    assert calls == []


async def test_a_working_redis_is_returned() -> None:
    client = FakeRedis()

    got = await build_redis(
        _settings(redis_url="redis://127.0.0.1:6379/0"), factory=lambda _s: client
    )

    assert got is client
    assert "ping" in client.calls, "建好先验一次连通，别等用户点下去才炸"


async def test_a_dead_redis_degrades_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """连不上且没声明必需 → 返回 None（调用方据此用进程内实现），并留下可查的痕。"""
    client = FakeRedis()
    client.fail_next(1)  # ping 失败

    with caplog.at_level(logging.WARNING):
        got = await build_redis(
            _settings(redis_url="redis://127.0.0.1:6379/0"), factory=lambda _s: client
        )

    assert got is None
    assert any("退回进程内实现" in r.getMessage() for r in caplog.records)
    assert client.calls == ["ping", "aclose"], "失败后要把半开的客户端收掉"


async def test_a_required_redis_refuses_to_start() -> None:
    """多 worker 部署：宁可启动失败，也不要「以为共享其实各一份」。"""
    client = FakeRedis()
    client.fail_next(1)

    with pytest.raises(RuntimeError) as caught:
        await build_redis(
            _settings(
                redis_url="redis://127.0.0.1:6379/0", redis_required=True
            ),
            factory=lambda _s: client,
        )

    assert "Redis 不可用" in str(caught.value)


async def test_a_missing_client_library_degrades_too(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """配了 ``REDIS_URL`` 但没装 redis 包：按「这次没有 Redis」处理，别抛 ImportError。

    这条曾经真的会崩：工厂（里面有 ``from redis.asyncio import …``）在 try 之外调用，
    于是「本机没装包」会变成一个看不懂的启动失败 —— 而它本该只是「退回进程内」。
    """

    def factory(_settings: Settings) -> FakeRedis:
        raise ModuleNotFoundError("No module named 'redis'")

    with caplog.at_level(logging.WARNING):
        got = await build_redis(
            _settings(redis_url="redis://127.0.0.1:6379/0"), factory=factory
        )

    assert got is None
    assert any("Redis 客户端建不起来" in r.getMessage() for r in caplog.records)


async def test_a_missing_client_library_still_blocks_a_required_setup() -> None:
    def factory(_settings: Settings) -> FakeRedis:
        raise ModuleNotFoundError("No module named 'redis'")

    with pytest.raises(RuntimeError) as caught:
        await build_redis(
            _settings(redis_url="redis://127.0.0.1:6379/0", redis_required=True),
            factory=factory,
        )

    assert "No module named 'redis'" in str(caught.value)


# -- 地址与密码（2026-09-27 用户口径）---------------------------------------


def test_a_url_without_a_scheme_is_normalized() -> None:
    """``127.0.0.1:6379`` 这种写法很自然，但 redis-py 会为它抛 ValueError。

    实测（2026-09-27）：真那样填的部署会变成「配了 Redis 却静默退回进程内」，只在日志里
    留一行，排障时最容易被忽略。补 scheme 比让人去读 redis-py 的报错便宜。
    """
    assert normalize_url("127.0.0.1:6379") == "redis://127.0.0.1:6379"
    assert normalize_url("redis://127.0.0.1:6379/0") == "redis://127.0.0.1:6379/0"
    assert normalize_url("rediss://h:6380/1") == "rediss://h:6380/1"

    # 归一化之后真能建成客户端（真库，不发连接）
    client = default_client_factory(_settings(redis_url="127.0.0.1:6379"))
    assert client.connection_pool.connection_kwargs["host"] == "127.0.0.1"
    assert client.connection_pool.connection_kwargs["port"] == 6379


def test_the_password_from_env_reaches_the_client() -> None:
    client = default_client_factory(
        _settings(redis_url="redis://127.0.0.1:6379/0", redis_password="pw-from-env")
    )

    assert client.connection_pool.connection_kwargs["password"] == "pw-from-env"


def test_no_password_means_no_password_argument() -> None:
    """没配就别传空串 —— redis-py 会把 ``""`` 当成一个真的密码拿去 AUTH。"""
    assert default_client_factory(
        _settings(redis_url="redis://127.0.0.1:6379/0")
    ).connection_pool.connection_kwargs.get("password") is None


def test_the_env_password_beats_the_one_in_the_url() -> None:
    """两边都写时以 ``REDIS_PASSWORD`` 为准。

    实测 redis-py 的 ``from_url(url, password=…)`` **不会**覆盖 URL 里的密码（URL 那个赢），
    所以必须自己先把 URL 里那份剥掉，否则「改了 REDIS_PASSWORD 却不生效」。
    """
    client = default_client_factory(
        _settings(
            redis_url="redis://theuser:url-pw@127.0.0.1:6379/0",
            redis_password="pw-from-env",
        )
    )
    kwargs = client.connection_pool.connection_kwargs

    assert kwargs["password"] == "pw-from-env"
    assert kwargs["username"] == "theuser", "用户名留在 URL 里那份由 redis-py 解析"


def test_a_url_only_password_still_works() -> None:
    """只写在 URL 里的密码也不能丢：那说明没配 REDIS_PASSWORD，就用 URL 里的。"""
    client = default_client_factory(
        _settings(redis_url="redis://theuser:url-pw@127.0.0.1:6379/0")
    )

    assert client.connection_pool.connection_kwargs["password"] == "url-pw"


def test_strip_password_keeps_the_user_and_the_rest() -> None:
    assert (
        strip_password("redis://user:pw@127.0.0.1:6379/2")
        == "redis://user@127.0.0.1:6379/2"
    )
    assert strip_password("redis://127.0.0.1:6379/0") == "redis://127.0.0.1:6379/0"


def test_mask_url_keeps_the_password_out_of_the_logs() -> None:
    assert (
        mask_url("redis://user:pw@127.0.0.1:6379/0")
        == "redis://user:***@127.0.0.1:6379/0"
    )
    assert mask_url("redis://127.0.0.1:6379/0") == "redis://127.0.0.1:6379/0"
    assert mask_url("不是个 URL") == "不是个 URL"  # 解析不了就原样，别在日志里再抛一次


async def test_the_password_never_reaches_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """连不上时那两条日志会带地址 —— 密码在 URL 里的话必须被打码。"""
    client = FakeRedis()
    client.fail_next(1)

    with caplog.at_level(logging.WARNING):
        got = await build_redis(
            _settings(redis_url="redis://user:topsecret@127.0.0.1:6379/0"),
            factory=lambda _s: client,
        )

    assert got is None
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "topsecret" not in logged
    assert "redis://user:***@127.0.0.1:6379/0" in logged


# -- 降级纪律：失败 → 冷却 → 再试一次（2026-10-11）--------------------------


class _Clock:
    """可控时钟：``RedisBacked.run`` 只看 ``time.monotonic`` / ``time.perf_counter``。

    直接替换 ``redis_cache.time``（**不是全局的 time 模块**）：全局那份同时被 asyncio 的
    定时器用着，冻住它会把事件循环一起冻住。
    """

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def perf_counter(self) -> float:
        return self.now


def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(redis_cache_module, "time", clock)
    return clock


async def test_a_failure_degrades_only_until_the_cooldown_is_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一次失败 → 冷却窗口内不再撞 Redis；窗口过了**再试一次**，成功即恢复。

    这条纪律的由来（实测）：``127.0.0.1:6379`` 其实是 SSH 隧道，PING 中位 62ms、p95 92ms，
    一次 TCP 重传就能把一条命令顶过超时。若「一次失败即永久降级」，这个进程此后彻底失去
    跨进程能力、要重启才回来 —— 代价与收益完全不成比例。
    """
    clock = _frozen_clock(monkeypatch)
    redis = FakeRedis()
    backed: RedisBacked[str] = RedisBacked(redis, what="测试状态", cooldown_seconds=60)
    redis.fail_next(1)

    assert await backed.run(lambda: redis.get("k"), _value("兜底-1")) == "兜底-1"
    assert backed.degraded is True

    calls = len(redis.calls)
    assert await backed.run(lambda: redis.get("k"), _value("兜底-2")) == "兜底-2"
    assert len(redis.calls) == calls, "冷却窗口内不该再白撞一次"

    clock.now += 61  # 冷却结束
    assert await backed.run(lambda: redis.get("k"), _value("兜底-3")) is None
    assert len(redis.calls) == calls + 1, "窗口过了要真的再试一次"
    assert backed.degraded is False


async def test_the_recovery_is_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """恢复要留一条 info —— 否则「什么时候回来的」只能靠猜。"""
    clock = _frozen_clock(monkeypatch)
    redis = FakeRedis()
    backed: RedisBacked[str] = RedisBacked(redis, what="测试状态", cooldown_seconds=60)
    redis.fail_next(1)

    with caplog.at_level(logging.INFO):
        await backed.run(lambda: redis.get("k"), _value("兜底"))
        clock.now += 61
        await backed.run(lambda: redis.get("k"), _value("兜底"))
        await backed.run(lambda: redis.get("k"), _value("兜底"))

    recovered = [r.getMessage() for r in caplog.records if "已恢复" in r.getMessage()]
    assert len(recovered) == 1


async def test_a_second_failure_re_arms_the_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冷却后重试**又**失败：重新进冷却，而不是就此放行每次都撞。"""
    clock = _frozen_clock(monkeypatch)
    redis = FakeRedis()
    backed: RedisBacked[str] = RedisBacked(redis, what="测试状态", cooldown_seconds=60)
    redis.fail_next(1)
    await backed.run(lambda: redis.get("k"), _value("兜底"))

    clock.now += 61
    redis.fail_next(1)
    await backed.run(lambda: redis.get("k"), _value("兜底"))
    assert backed.degraded is True

    calls = len(redis.calls)
    await backed.run(lambda: redis.get("k"), _value("兜底"))
    assert len(redis.calls) == calls, "重新进的冷却窗口同样要挡住"


async def test_the_failure_log_says_how_long_the_command_took(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """只说「不可用」看不出是服务端慢还是链路抖 —— 耗时必须进日志。"""
    clock = _frozen_clock(monkeypatch)
    redis = FakeRedis()
    backed: RedisBacked[str] = RedisBacked(redis, what="测试状态", cooldown_seconds=60)

    async def slow_failure() -> str:
        clock.now += 0.312  # 这条命令「花」了 312ms 才失败
        raise ConnectionError("tunnel hiccup")

    with caplog.at_level(logging.WARNING):
        await backed.run(slow_failure, _value("兜底"))

    message = caplog.records[-1].getMessage()
    assert "测试状态" in message
    assert "312 ms 才失败" in message
    assert "60 秒" in message


async def test_a_zero_cooldown_means_the_old_permanent_degrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """设 0 ＝ 退回旧纪律（重启才回来）：给「宁可不要跨进程能力」的部署一个开关。"""
    clock = _frozen_clock(monkeypatch)
    redis = FakeRedis()
    backed: RedisBacked[str] = RedisBacked(redis, what="测试状态", cooldown_seconds=0)
    redis.fail_next(1)

    assert await backed.run(lambda: redis.get("k"), _value("兜底-1")) == "兜底-1"
    assert backed.degraded is True

    clock.now += 10_000  # 再久也不回来
    calls = len(redis.calls)
    assert await backed.run(lambda: redis.get("k"), _value("兜底-2")) == "兜底-2"
    assert len(redis.calls) == calls, "0 冷却 = 永久降级，不该再撞 Redis"


async def test_a_command_that_never_returns_is_cut_off_by_the_guard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """兜底超时：``socket_timeout`` 只管读，隧道半死时**写**能一直阻塞。

    实测（2026-10-12，SSH 隧道被重建的那一刻）一条命令花了 **556 秒**才由 TCP 自己放弃。
    这一层保证「一条命令」不会把一轮对话拖住几分钟 —— 超时按失败处理，走进程内 + 冷却。
    """
    from ai_coding_harness.redis_cache import RedisBacked

    async def hangs() -> str:
        await asyncio.sleep(30)  # 模拟「写阻塞」：既不报错也不返回
        return "不该走到这里"

    async def fallback() -> str:
        return "进程内"

    backed: RedisBacked[str] = RedisBacked(
        FakeRedis(), what="测试状态", cooldown_seconds=60, guard_seconds=0.05
    )

    with caplog.at_level(logging.WARNING):
        assert await backed.run(hangs, fallback) == "进程内"

    assert backed.degraded is True
    # 掐掉它的必须是**兜底超时**（TimeoutError），不是那条命令自己报的错
    message = caplog.records[-1].getMessage()
    assert "测试状态" in message
    assert "TimeoutError" in message


async def test_a_slow_but_finite_command_inside_the_guard_is_not_cut() -> None:
    """兜底不能把正常的慢命令掐掉：隧道量级的往返（几十毫秒级）要照常返回真值。"""
    from ai_coding_harness.redis_cache import RedisBacked

    async def slow_but_ok() -> str:
        await asyncio.sleep(0.02)
        return "真值"

    async def fallback() -> str:
        return "进程内"

    backed: RedisBacked[str] = RedisBacked(
        FakeRedis(), what="测试状态", cooldown_seconds=60, guard_seconds=0.5
    )

    assert await backed.run(slow_but_ok, fallback) == "真值"
    assert backed.degraded is False


def test_the_guard_follows_the_configured_socket_timeout() -> None:
    """兜底由配置推出来（socket 超时的 3 倍），不另设一个键也不写死常数。

    配置放大超时（远端 / 慢隧道）的人，需要兜底一起放大；写死 15 秒会让「30 秒的 socket
    超时」被兜底先掐掉，与配置的意图相反。
    """
    from ai_coding_harness.redis_cache import COMMAND_GUARD_FACTOR, command_guard_seconds

    assert command_guard_seconds(_settings(redis_timeout_seconds=5.0)) == pytest.approx(
        5.0 * COMMAND_GUARD_FACTOR
    )
    assert command_guard_seconds(_settings(redis_timeout_seconds=30.0)) == pytest.approx(
        30.0 * COMMAND_GUARD_FACTOR
    )
    assert command_guard_seconds(_settings(redis_timeout_seconds=0.0)) == 0.0


def _value(text: str) -> Callable[[], Awaitable[str]]:
    """进程内那半：一个返回固定值的协程（``run`` 要的是「能被调用出 awaitable」）。"""

    async def get() -> str:
        return text

    return get
