"""``OpenSandboxProxy`` 的可用性行为单测 —— 全用假沙箱 / 假 provider，不连真沙箱。

关注四件事：探活按 TTL 缓存、故障时「先重连再重建」、只有传输层异常才触发恢复、
并发调用同时发现故障时只恢复一次；外加一件：**沙箱续期**（按间隔节流、失败不影响调用、
句柄失效才走恢复）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from opensandbox.exceptions import (
    SandboxApiException,
    SandboxConnectionException,
    SandboxUnhealthyException,
)

from ai_coding_harness.config import Settings
from ai_coding_harness.sandbox import proxy as proxy_mod
from ai_coding_harness.sandbox.client import CommandResult
from ai_coding_harness.sandbox.proxy import OpenSandboxProxy


class FakeSandbox:
    """与 ``SandboxClient`` 同形的假沙箱。"""

    def __init__(
        self, sandbox_id: str, *, healthy: bool = True, run_delay: float = 0.0
    ) -> None:
        self.id = sandbox_id
        self.healthy = healthy
        self.run_delay = run_delay
        self.health_calls = 0
        self.run_calls: list[str] = []
        self.run_envs: list[dict[str, str] | None] = []
        self.files: dict[str, str] = {}
        self.raise_on_run: Exception | None = None
        self.renew_calls: list[timedelta] = []
        self.renew_error: Exception | None = None

    async def is_healthy(self) -> bool:
        self.health_calls += 1
        await asyncio.sleep(0)  # 让并发调用真正交错，暴露单飞缺陷
        return self.healthy

    async def renew(self, timeout: timedelta) -> datetime:
        self.renew_calls.append(timeout)
        await asyncio.sleep(0)  # 同上：并发续期要能交错
        if self.renew_error is not None:
            raise self.renew_error
        return datetime(2026, 9, 25, 12, 0, tzinfo=UTC) + timeout

    async def run(self, command, *, cwd=None, timeout=None, envs=None) -> CommandResult:
        if self.raise_on_run is not None:
            raise self.raise_on_run
        self.run_calls.append(command)
        self.run_envs.append(envs)
        if self.run_delay:
            await asyncio.sleep(self.run_delay)  # 模拟一次长调用
        return CommandResult(
            exit_code=0, stdout=f"ok:{command}", stderr="", text=f"ok:{command}"
        )

    async def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    async def read_file(self, path: str) -> str:
        return self.files[path]


class FakeProvider:
    """假的 ``SandboxProvider``：记录调用，按预设返回重连 / 重建结果。"""

    def __init__(
        self,
        *,
        reconnect_result: FakeSandbox | None = None,
        create_result: FakeSandbox | None = None,
        delay: float = 0.0,
        destroy_error: BaseException | None = None,
    ) -> None:
        self.reconnect_result = reconnect_result
        self.create_result = create_result
        self.delay = delay
        self.destroy_error = destroy_error
        self.reconnect_calls: list[str] = []
        self.create_calls = 0
        self.destroyed: list[str] = []
        self.created_with: list[tuple] = []

    async def reconnect(self, sandbox_id: str) -> FakeSandbox | None:
        self.reconnect_calls.append(sandbox_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reconnect_result

    async def create(self, volumes=None, env=None) -> FakeSandbox:
        self.create_calls += 1
        self.created_with.append((volumes, env))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.create_result

    async def destroy(self, client) -> None:
        self.destroyed.append(client.id)
        if self.destroy_error is not None:
            raise self.destroy_error


@pytest.fixture
def clock(monkeypatch):
    """可控时钟：代理的 TTL 判断全部走 ``time.monotonic``，故直接替换之。"""
    now = [1000.0]
    monkeypatch.setattr(proxy_mod.time, "monotonic", lambda: now[0])
    return now


def make_proxy(
    client: FakeSandbox,
    provider: FakeProvider,
    *,
    ttl: float = 5.0,
    renew_interval: int = 1800,
    on_rebuild=None,
    volumes=None,
    env=None,
) -> OpenSandboxProxy:
    settings = Settings(
        sandbox_health_probe_ttl_seconds=ttl,
        sandbox_renew_interval_seconds=renew_interval,
    )
    assert settings.sandbox_health_probe_ttl_seconds == ttl  # 防止配置项名写错被静默忽略
    assert settings.sandbox_renew_interval_seconds == renew_interval
    return OpenSandboxProxy(
        settings=settings,
        provider=provider,
        client=client,
        public_id="pub-1",
        volumes=volumes,
        env=env,
        on_rebuild=on_rebuild,
    )


def collect(sink: list[str]):
    """返回一个把新 sandbox_id 记进 ``sink`` 的 on_rebuild 回调。"""

    async def callback(new_id: str) -> None:
        sink.append(new_id)

    return callback


async def test_healthy_call_caches_probe_within_ttl(clock) -> None:
    client = FakeSandbox("sb-1")
    provider = FakeProvider()
    proxy = make_proxy(client, provider, ttl=5.0)

    await proxy.run("ls")
    assert client.health_calls == 1

    await proxy.run("ls")  # TTL 内：跳过探活
    assert client.health_calls == 1

    clock[0] += 6  # 过了 TTL：重新探活
    await proxy.run("ls")
    assert client.health_calls == 2

    assert client.run_calls == ["ls", "ls", "ls"]
    assert provider.reconnect_calls == []
    assert provider.create_calls == 0


async def test_zero_ttl_probes_every_call(clock) -> None:
    client = FakeSandbox("sb-1")
    proxy = make_proxy(client, FakeProvider(), ttl=0.0)

    await proxy.run("ls")
    await proxy.run("ls")
    assert client.health_calls == 2


async def test_probe_failure_reconnects_before_rebuilding() -> None:
    old = FakeSandbox("sb-old", healthy=False)
    new = FakeSandbox("sb-new")
    provider = FakeProvider(reconnect_result=new)
    rebuilt: list[str] = []
    proxy = make_proxy(old, provider, on_rebuild=collect(rebuilt))

    result = await proxy.run("ls")

    assert result.stdout == "ok:ls"
    assert provider.reconnect_calls == ["sb-old"]
    assert provider.create_calls == 0  # 沙箱还在，不重建
    assert rebuilt == []  # 重连不算重建，不通知回填
    assert proxy.id == "sb-new"
    assert new.run_calls == ["ls"]


async def test_reconnect_failure_rebuilds_and_notifies() -> None:
    old = FakeSandbox("sb-old", healthy=False)
    new = FakeSandbox("sb-new")
    provider = FakeProvider(reconnect_result=None, create_result=new)
    rebuilt: list[str] = []
    volumes, env = ["vol"], {"K": "V"}
    proxy = make_proxy(
        old,
        provider,
        on_rebuild=collect(rebuilt),
        volumes=volumes,
        env=env,
    )

    result = await proxy.run("ls")

    assert result.stdout == "ok:ls"
    assert provider.reconnect_calls == ["sb-old"]
    assert provider.create_calls == 1
    # 重建沿用同一卷与环境
    assert provider.created_with == [(volumes, env)]
    assert proxy.id == "sb-new"
    assert rebuilt == ["sb-new"]  # 重建后把新 id 交给上层回填
    assert new.run_calls == ["ls"]


@pytest.mark.parametrize(
    "exc",
    [SandboxConnectionException("boom"), SandboxUnhealthyException("boom")],
    ids=["connection", "unhealthy"],
)
async def test_transport_error_recovers_then_retries(exc) -> None:
    old = FakeSandbox("sb-old")  # 探活通过，故障只在调用时暴露
    new = FakeSandbox("sb-new")
    provider = FakeProvider(reconnect_result=new)
    proxy = make_proxy(old, provider)

    old.raise_on_run = exc
    result = await proxy.run("ls")

    assert result.stdout == "ok:ls"  # 恢复后重试成功
    assert provider.reconnect_calls == ["sb-old"]
    assert new.run_calls == ["ls"]


async def test_business_error_is_not_recoverable() -> None:
    old = FakeSandbox("sb-old")
    provider = FakeProvider(reconnect_result=FakeSandbox("sb-new"))
    proxy = make_proxy(old, provider)

    old.raise_on_run = ValueError("命令参数非法")
    with pytest.raises(ValueError):
        await proxy.run("ls")

    assert provider.reconnect_calls == []
    assert provider.create_calls == 0
    assert proxy.id == "sb-old"


@pytest.mark.parametrize("status", [404, 410, 502, 503], ids=lambda s: f"http{s}")
async def test_gone_or_transient_api_error_recovers(status) -> None:
    """沙箱已被回收（404/410）或网关瞬时故障（502/503）：重建能解决，走恢复。"""
    old = FakeSandbox("sb-old")
    new = FakeSandbox("sb-new")
    provider = FakeProvider(reconnect_result=new)
    proxy = make_proxy(old, provider)

    old.raise_on_run = SandboxApiException("boom", status_code=status)
    result = await proxy.run("ls")

    assert result.stdout == "ok:ls"
    assert provider.reconnect_calls == ["sb-old"]
    assert new.run_calls == ["ls"]


@pytest.mark.parametrize(
    "exc",
    [
        SandboxApiException("too many requests", status_code=429),
        SandboxApiException("bad request", status_code=400),
        SandboxApiException("forbidden", status_code=403),
    ],
    ids=["429-rate-limit", "400-bad-request", "403-forbidden"],
)
async def test_client_side_api_error_is_not_recoverable(exc) -> None:
    """限流与参数/权限类 4xx 不触发恢复：重建解决不了，还会白丢容器。"""
    old = FakeSandbox("sb-old")
    provider = FakeProvider(reconnect_result=FakeSandbox("sb-new"))
    proxy = make_proxy(old, provider)

    old.raise_on_run = exc
    with pytest.raises(SandboxApiException):
        await proxy.run("ls")

    assert provider.reconnect_calls == []
    assert provider.create_calls == 0
    assert proxy.id == "sb-old"


async def test_concurrent_callers_recover_once() -> None:
    old = FakeSandbox("sb-old", healthy=False)
    new = FakeSandbox("sb-new")
    provider = FakeProvider(reconnect_result=new, delay=0.01)
    proxy = make_proxy(old, provider)

    results = await asyncio.gather(*(proxy.run(f"cmd{i}") for i in range(5)))

    assert [r.stdout for r in results] == [f"ok:cmd{i}" for i in range(5)]
    assert provider.reconnect_calls == ["sb-old"]  # 单飞：并发只恢复一次
    assert provider.create_calls == 0
    assert sorted(new.run_calls) == [f"cmd{i}" for i in range(5)]


async def test_file_ops_go_through_proxy() -> None:
    client = FakeSandbox("sb-1")
    proxy = make_proxy(client, FakeProvider())

    await proxy.write_file("/a.txt", "hi")
    assert await proxy.read_file("/a.txt") == "hi"


async def test_destroy_closes_proxy() -> None:
    client = FakeSandbox("sb-1")
    provider = FakeProvider()
    proxy = make_proxy(client, provider)

    await proxy.destroy()
    assert provider.destroyed == ["sb-1"]

    with pytest.raises(RuntimeError):
        await proxy.run("ls")
    assert client.health_calls == 0  # 已关闭：不再探活，也不复活
    assert provider.reconnect_calls == []


@pytest.mark.parametrize("status", [404, 410])
async def test_destroy_tolerates_already_gone_sandbox(status) -> None:
    """沙箱已被 OpenSandbox 先回收：目标状态已达成，按成功处理。"""
    client = FakeSandbox("sb-1")
    provider = FakeProvider(destroy_error=SandboxApiException("not found", status_code=status))
    proxy = make_proxy(client, provider)

    await proxy.destroy()  # 不抛
    assert provider.destroyed == ["sb-1"]


@pytest.mark.parametrize("status", [502, 503, 500, 400, 403, 429])
async def test_destroy_still_raises_on_real_failures(status) -> None:
    """只有「确实没了」才吞 —— 502/503 意味着沙箱可能还在，吞掉会漏容器。"""
    client = FakeSandbox("sb-1")
    provider = FakeProvider(destroy_error=SandboxApiException("boom", status_code=status))
    proxy = make_proxy(client, provider)

    with pytest.raises(SandboxApiException):
        await proxy.destroy()
    # 已经销毁就绝不允许复活，失败也一样
    with pytest.raises(RuntimeError):
        await proxy.run("ls")
    assert client.health_calls == 0


# --------------------------------------------------------------------------- 续期


class TestTheRenewal:
    """沙箱续期（2026-09-25）：按 ``SANDBOX_RENEW_INTERVAL_SECONDS`` 节流，失败不影响调用。"""

    async def test_a_fresh_proxy_does_not_renew(self, clock) -> None:
        """刚拿到的沙箱带着完整 TTL：第一次调用不该顺手续一次。"""
        client = FakeSandbox("sb-1")
        proxy = make_proxy(client, FakeProvider())

        await proxy.run("ls")

        assert client.renew_calls == []

    async def test_a_long_session_renews_once_per_interval(self, clock) -> None:
        client = FakeSandbox("sb-1")
        proxy = make_proxy(client, FakeProvider())

        clock[0] += 1800  # 到点
        await proxy.run("ls")
        assert client.renew_calls == [timedelta(minutes=60)], "续期目标 = SANDBOX_TIMEOUT_MINUTES"

        clock[0] += 1799  # 还没到下一个间隔
        await proxy.run("ls")
        assert len(client.renew_calls) == 1

        clock[0] += 1
        await proxy.run("ls")
        assert len(client.renew_calls) == 2

    async def test_zero_interval_disables_the_renewal(self, clock) -> None:
        client = FakeSandbox("sb-1")
        proxy = make_proxy(client, FakeProvider(), renew_interval=0)

        clock[0] += 10_000
        await proxy.run("ls")

        assert client.renew_calls == []

    async def test_renew_if_due_is_the_round_start_hook(self, clock) -> None:
        """轮首那条路（``SessionManager.ensure_runtime``）调的就是这个公开方法。"""
        client = FakeSandbox("sb-1")
        proxy = make_proxy(client, FakeProvider())

        clock[0] += 1800
        await proxy.renew_if_due()

        assert len(client.renew_calls) == 1

    @pytest.mark.parametrize("status", [429, 400, 403])
    async def test_a_client_side_failure_only_warns(self, clock, status) -> None:
        """续期被限流 / 参数错：记一条日志就完事 —— 本次调用照跑，也不触发恢复。"""
        client = FakeSandbox("sb-1")
        client.renew_error = SandboxApiException("nope", status_code=status)
        provider = FakeProvider()
        proxy = make_proxy(client, provider)

        clock[0] += 1800
        result = await proxy.run("ls")

        assert result.stdout == "ok:ls"
        assert provider.reconnect_calls == [] and provider.create_calls == 0

    async def test_a_failed_renewal_is_not_retried_every_call(self, clock) -> None:
        client = FakeSandbox("sb-1")
        client.renew_error = SandboxApiException("nope", status_code=429)
        proxy = make_proxy(client, FakeProvider())

        clock[0] += 1800
        await proxy.run("ls")
        clock[0] += 1
        await proxy.run("ls")

        assert len(client.renew_calls) == 1, "同一间隔内不反复重试"

    async def test_a_dead_handle_triggers_the_recovery(self, clock) -> None:
        """续期时发现句柄已失效（传输层异常）：走既有的「重连 → 重建」那条路。"""
        old = FakeSandbox("sb-old")
        old.renew_error = SandboxConnectionException("boom")
        new = FakeSandbox("sb-new")
        provider = FakeProvider(reconnect_result=new)
        proxy = make_proxy(old, provider)

        clock[0] += 1800
        result = await proxy.run("ls")

        assert result.stdout == "ok:ls"
        assert provider.reconnect_calls == ["sb-old"]
        assert proxy.id == "sb-new"

    async def test_the_renewal_clock_resets_after_a_rebuild(self, clock) -> None:
        """换了句柄就重新计时：重连回来的那一刻不该马上再续一次。"""
        old = FakeSandbox("sb-old", healthy=False)
        new = FakeSandbox("sb-new")
        provider = FakeProvider(reconnect_result=new)
        proxy = make_proxy(old, provider)

        await proxy.run("ls")  # 探活失败 → 重连
        await proxy.run("ls")  # 新句柄刚接手，不该续期

        assert new.renew_calls == []

    async def test_concurrent_calls_renew_once(self, clock) -> None:
        client = FakeSandbox("sb-1")
        proxy = make_proxy(client, FakeProvider())

        clock[0] += 1800
        await asyncio.gather(proxy.run("a"), proxy.run("b"), proxy.run("c"))

        assert len(client.renew_calls) == 1, "并发调用里只续一次"

    # -- 一次调用自己跑很久：调用进行中的心跳 ---------------------------------

    async def test_a_long_call_is_renewed_while_it_runs(self) -> None:
        """一次工具调用跑几十分钟（build / 大 diff）时，中途也要续上：
        到期是按绝对时间回收的，不续的话命令会被半路杀掉。

        这几条**不用** ``clock`` 那个冻结时钟的 fixture：Windows 的 asyncio 定时器也走
        ``time.monotonic``，时钟一冻结 ``await asyncio.sleep`` 就永远不醒（实测挂住）。
        """
        client = FakeSandbox("sb-1", run_delay=0.3)
        proxy = make_proxy(client, FakeProvider(), renew_interval=0.05)

        await proxy.run("mvn test")

        assert len(client.renew_calls) >= 2, f"调用进行中该续多次，实际 {len(client.renew_calls)}"
        assert client.run_calls == ["mvn test"]

    async def test_the_heartbeat_stops_when_the_call_finishes(self) -> None:
        client = FakeSandbox("sb-1", run_delay=0.15)
        proxy = make_proxy(client, FakeProvider(), renew_interval=0.05)

        await proxy.run("ls")
        after = len(client.renew_calls)
        await asyncio.sleep(0.2)  # 调用早已结束

        assert len(client.renew_calls) == after, "心跳只活在调用期间"

    async def test_a_short_call_never_renews(self) -> None:
        """调用比心跳间隔还短：心跳任务还没醒就被取消了，一次续期都不该发生。"""
        client = FakeSandbox("sb-1")
        proxy = make_proxy(client, FakeProvider(), renew_interval=1800)

        await proxy.run("ls")

        assert client.renew_calls == []

    async def test_a_heartbeat_failure_does_not_break_the_call(self) -> None:
        """心跳里续期失败（限流）：正在跑的那次调用照常拿到结果。"""
        client = FakeSandbox("sb-1", run_delay=0.15)
        client.renew_error = SandboxApiException("nope", status_code=429)
        proxy = make_proxy(client, FakeProvider(), renew_interval=0.05)

        result = await proxy.run("mvn test")

        assert result.stdout == "ok:mvn test"

    async def test_no_heartbeat_when_the_renewal_is_off(self) -> None:
        client = FakeSandbox("sb-1", run_delay=0.15)
        proxy = make_proxy(client, FakeProvider(), renew_interval=0)

        await proxy.run("ls")

        assert client.renew_calls == []
