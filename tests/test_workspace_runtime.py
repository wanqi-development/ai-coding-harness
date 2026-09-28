"""``SessionManager`` 工作区运行时的单测 —— 全假装配件，不连沙箱也不连库。

会话创建与工作区构建拆开之后，几条关键契约靠这里的用例钉住：

- **合流**：并发 ensure 只建一次；已有运行时直接复用；
- **缓存**：不管谁等的结果，建成的运行时都收进 ``_runtimes``（后台建的若不收，下一次
  ensure 会当成本进程没建过而重连一趟）；失败或被放弃的构建不收；
- **重试**：构建失败只重试一次，两次都失败才抛 ``WorkspaceError``（对用户说固定措辞），
  且任何异常类型都走这条路径（不能穿成 500）；
- **软取消**：等待方放弃（超时/删会话）后，构建任务自己回收刚建出来的容器；
- **回收**：删会话时进程里没有运行时，就按库里记的 sandbox_id 调 ``terminate``；
- **重连优先**：库里记着 sandbox_id 且重连成功时，不新建容器。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai_coding_harness.config import Settings
from ai_coding_harness.db import dao
from ai_coding_harness.session import manager as manager_module
from ai_coding_harness.session.manager import (
    WORKSPACE_FAILED,
    WORKSPACE_PROVISIONING,
    WORKSPACE_READY,
    Session,
    SessionManager,
    WorkspaceError,
)
from ai_coding_harness.tools.git import GiteeRepo

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
DB_ID = 7
STORED_SANDBOX = "sb-stored"


def make_session() -> Session:
    return Session(
        public_id=PUBLIC_ID,
        db_id=DB_ID,
        repo=GiteeRepo(owner="owner", repo="repo", token="tok", git_username="oauth2"),
        repo_url="https://gitee.com/owner/repo.git",
    )


class FakeClient:
    def __init__(self, client_id: str) -> None:
        self.id = client_id


class _NullSession:
    """dao 全被打桩，这里只要一个能 ``async with`` 返回值的空壳。"""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeDatabase:
    def session(self) -> _NullSession:
        return _NullSession()


class Recorder:
    """替身 dao 的记录本。"""

    def __init__(self) -> None:
        self.workspace_status: list[tuple[str, str | None]] = []
        self.sandbox_writes: list[tuple[str | None, str | None]] = []
        self.session_status: list[str] = []
        self.stored_sandbox_id: str | None = None


class FakeProvider:
    """假 provider：可指定失败次数、可把建容器卡住以便观察合流与软取消。"""

    def __init__(
        self,
        *,
        fail_times: int = 0,
        reconnectable: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.fail_times = fail_times
        self.reconnectable = reconnectable
        self.delay = delay
        self.gate: asyncio.Event | None = None
        self.created: list[str] = []
        self.created_with: list[tuple] = []
        self.reconnected: list[str] = []
        self.destroyed: list[str] = []
        self.terminated: list[str] = []

    async def _blocking_point(self) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.gate is not None:
            await self.gate.wait()

    async def create(self, volumes, env):
        client_id = f"sb-new-{len(self.created) + 1}"
        self.created.append(client_id)
        self.created_with.append((volumes, env))
        await self._blocking_point()
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("opensandbox 503")
        return FakeClient(client_id)

    async def reconnect(self, sandbox_id: str | None):
        if sandbox_id is None or not self.reconnectable:
            return None
        self.reconnected.append(sandbox_id)
        await self._blocking_point()
        return FakeClient(sandbox_id)

    async def destroy(self, client) -> None:
        self.destroyed.append(client.id)

    async def terminate(self, sandbox_id: str) -> None:
        self.terminated.append(sandbox_id)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """把 manager 用到的 dao 全部换成记录本（签名与真实实现保持一致）。"""
    rec = Recorder()

    async def set_workspace_status(db, session_id, status, *, error=None) -> None:
        rec.workspace_status.append((status, error))

    async def set_session_sandbox(
        db, session_id, *, sandbox_id, workspace_volume
    ) -> None:
        rec.sandbox_writes.append((sandbox_id, workspace_volume))

    async def set_session_status(db, session_id, status) -> None:
        rec.session_status.append(status)

    async def get_session(db, session_id):
        return SimpleNamespace(sandbox_id=rec.stored_sandbox_id)

    monkeypatch.setattr(dao, "set_workspace_status", set_workspace_status)
    monkeypatch.setattr(dao, "set_session_sandbox", set_session_sandbox)
    monkeypatch.setattr(dao, "set_session_status", set_session_status)
    monkeypatch.setattr(dao, "get_session", get_session)
    return rec


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """文件后端与真沙箱无关，换成一个哨兵好断言。"""
    monkeypatch.setattr(
        manager_module,
        "build_composite_backend",
        lambda *a, **kw: SimpleNamespace(kind="composite"),
    )


def make_manager(provider: FakeProvider) -> SessionManager:
    mgr = SessionManager(
        settings=Settings(),
        model_for=lambda role: SimpleNamespace(),
        fallback_for=lambda role: None,
        web_search_tool=SimpleNamespace(),
        store=SimpleNamespace(),
        checkpointer=SimpleNamespace(),
        database=FakeDatabase(),
    )
    mgr._provider = provider
    return mgr


async def settle() -> None:
    """让出几轮事件循环，等 done_callback 之类被排队的回调跑完。"""
    for _ in range(3):
        await asyncio.sleep(0)


async def test_concurrent_ensure_builds_only_once(recorder: Recorder) -> None:
    """并发 ensure 合流到同一次构建：两个调用方拿到的是同一个运行时。"""
    provider = FakeProvider(delay=0.02)
    mgr = make_manager(provider)
    session = make_session()

    first, second = await asyncio.gather(
        mgr.ensure_runtime(session), mgr.ensure_runtime(session)
    )

    assert first is second
    assert provider.created == ["sb-new-1"]
    assert recorder.workspace_status == [
        (WORKSPACE_PROVISIONING, None),
        (WORKSPACE_READY, None),
    ]
    assert mgr._pending == {}, "构建结束后不该留在飞记录"


async def test_ensure_reuses_ready_runtime(recorder: Recorder) -> None:
    provider = FakeProvider()
    mgr = make_manager(provider)
    session = make_session()

    await mgr.ensure_runtime(session)
    await mgr.ensure_runtime(session)

    assert provider.created == ["sb-new-1"], "已有运行时不该再建一次"


async def test_the_dependency_cache_volumes_ride_along(recorder: Recorder) -> None:
    """依赖缓存都挂在卷上（默认跨会话共享）—— 否则每次构建都要重新下依赖。

    这台是「建沙箱时挂了哪些卷」的唯一断言点：工作区卷（project）+ Maven 依赖缓存（.m2）。
    """
    provider = FakeProvider()
    mgr = make_manager(provider)

    await mgr.ensure_runtime(make_session())

    volumes, _env = provider.created_with[0]
    mounts = {volume.mount_path: volume for volume in volumes}
    assert set(mounts) == {
        "/ai_coding/project",
        "/ai_coding/.m2",
    }
    assert mounts["/ai_coding/.m2"].pvc.claim_name == "ai-coding-m2"


async def test_ensure_renews_the_existing_sandbox(recorder: Recorder) -> None:
    """轮首续期：每次取「已存在的运行时」时顺手让沙箱续一次（节流在代理内部判）。

    运行中那一侧由代理在每次沙箱调用前兜住 —— 一轮可能跑很久，只靠轮首这一处不够。
    """
    mgr = make_manager(FakeProvider())
    session = make_session()
    runtime = await mgr.ensure_runtime(session)
    calls: list[int] = []

    async def spy() -> None:
        calls.append(1)

    runtime.sandbox.renew_if_due = spy  # type: ignore[method-assign]

    await mgr.ensure_runtime(session)

    assert calls == [1], "轮首该调一次 renew_if_due"


async def test_retry_once_then_success(recorder: Recorder) -> None:
    """首次失败 → 自动重建一次；第二次成功则整体算成功。"""
    provider = FakeProvider(fail_times=1)
    mgr = make_manager(provider)

    runtime = await mgr.ensure_runtime(make_session())

    assert provider.created == ["sb-new-1", "sb-new-2"]
    assert runtime.sandbox.id == "sb-new-2"
    assert recorder.workspace_status == [
        (WORKSPACE_PROVISIONING, None),
        (WORKSPACE_FAILED, "opensandbox 503"),
        (WORKSPACE_PROVISIONING, None),
        (WORKSPACE_READY, None),
    ]


async def test_two_failures_raise_workspace_error(recorder: Recorder) -> None:
    """两次都失败：抛 ``WorkspaceError``（调用方据此说固定措辞），不无限重试。"""
    provider = FakeProvider(fail_times=2)
    mgr = make_manager(provider)

    with pytest.raises(WorkspaceError):
        await mgr.ensure_runtime(make_session())

    assert len(provider.created) == 2
    assert recorder.workspace_status[-1] == (WORKSPACE_FAILED, "opensandbox 503")
    await settle()
    assert mgr._pending == {}


async def test_provider_failure_becomes_workspace_error(recorder: Recorder) -> None:
    """provider 抛的任意异常都必须收敛成 ``WorkspaceError``，否则 API 会穿成 500。"""
    provider = FakeProvider(fail_times=99)
    mgr = make_manager(provider)

    with pytest.raises(WorkspaceError):
        await mgr.ensure_runtime(make_session())


async def test_background_failure_is_swallowed(recorder: Recorder) -> None:
    """后台构建失败且无人 await 时，不能留下未取回的异常（asyncio 会告警）。"""
    provider = FakeProvider(fail_times=99)
    mgr = make_manager(provider)
    session = make_session()

    mgr.start_provisioning(session)
    await asyncio.sleep(0.02)

    assert mgr._pending == {}
    assert mgr._runtimes == {}, "没建成的空壳运行时不能进缓存"
    assert recorder.workspace_status[-1] == (WORKSPACE_FAILED, "opensandbox 503")


async def test_background_build_result_is_cached(recorder: Recorder) -> None:
    """后台建成的运行时必须收进缓存。

    ``POST /sessions`` 起了构建就不管了，结果只有 done_callback 接着。若不收进
    ``_runtimes``，下一次 ``ensure_runtime`` 会当成本进程没建过 —— 容器与 sandbox_id
    都在，于是重走一遍 ``reconnect``（实测 6~7s），白等一趟。
    """
    provider = FakeProvider()
    mgr = make_manager(provider)
    session = make_session()

    mgr.start_provisioning(session)
    await settle()
    built = mgr._runtimes.get(session.public_id)
    assert built is not None, "后台建成的运行时没进缓存"

    assert await mgr.ensure_runtime(session) is built, "该复用缓存，而不是再建/再连一次"
    assert provider.created == ["sb-new-1"]
    assert provider.reconnected == []


async def test_soft_cancel_reclaims_container(recorder: Recorder) -> None:
    """等待方放弃后，构建任务拿到容器就地回收 —— 不能硬 cancel 掉一个已建出的容器。"""
    provider = FakeProvider()
    provider.gate = asyncio.Event()
    mgr = make_manager(provider)
    session = make_session()

    mgr.start_provisioning(session)
    pending = mgr._pending[session.public_id]
    await settle()  # 让构建跑到卡点（容器已建出、还没返回）

    await mgr.destroy_session(public_id=session.public_id, db_id=session.db_id)
    provider.gate.set()

    with pytest.raises(WorkspaceError):
        await pending.task

    assert provider.destroyed == ["sb-new-1"], "半成品容器必须被回收"
    assert mgr._runtimes == {}
    assert recorder.session_status == ["destroyed"]


async def test_destroy_without_runtime_terminates_by_id(recorder: Recorder) -> None:
    """进程重启过、运行时不在内存时，按库里记的 sandbox_id 直接销毁，不遗留容器。"""
    provider = FakeProvider()
    mgr = make_manager(provider)
    recorder.stored_sandbox_id = STORED_SANDBOX

    await mgr.destroy_session(public_id=PUBLIC_ID, db_id=DB_ID)

    assert provider.terminated == [STORED_SANDBOX]
    assert provider.destroyed == []
    assert recorder.session_status == ["destroyed"]


async def test_destroy_without_stored_sandbox_is_noop(recorder: Recorder) -> None:
    provider = FakeProvider()
    mgr = make_manager(provider)

    await mgr.destroy_session(public_id=PUBLIC_ID, db_id=DB_ID)

    assert provider.terminated == []
    assert recorder.session_status == ["destroyed"]


async def test_reconnect_skips_create(recorder: Recorder) -> None:
    """库里记着 sandbox_id 且重连成功：沿用旧容器，不新建（否则旧容器会被漏掉）。"""
    provider = FakeProvider(reconnectable=True)
    mgr = make_manager(provider)
    recorder.stored_sandbox_id = STORED_SANDBOX

    runtime = await mgr.ensure_runtime(make_session())

    assert provider.reconnected == [STORED_SANDBOX]
    assert provider.created == []
    assert runtime.sandbox.id == STORED_SANDBOX


async def test_reconnect_failure_falls_back_to_create(recorder: Recorder) -> None:
    """重连不上（容器已被回收）→ 回落新建，并回填新的 sandbox_id。"""
    provider = FakeProvider(reconnectable=False)
    mgr = make_manager(provider)
    recorder.stored_sandbox_id = STORED_SANDBOX

    runtime = await mgr.ensure_runtime(make_session())

    assert provider.created == ["sb-new-1"]
    assert runtime.sandbox.id == "sb-new-1"
    assert recorder.sandbox_writes[-1][0] == "sb-new-1"
