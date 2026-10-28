"""一个仓库一个活跃会话：复用的契约（全假装配件，不连库也不连沙箱）。

三件事靠这里钉住：

- 库里已有活跃会话时**不再新建** —— 复用它的 id、凭据与工作区状态（凭据取库里那份，
  不回落 ``.env``，否则旧会话会用上后来换过的令牌）；
- 地址换个写法（``.git``、末尾斜杠、大小写）绕不开复用；
- 并发建同一个仓库只落一行 —— 这条依赖「先拿锁再查」的顺序，把顺序写反了它会红。
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from ai_coding_harness.api.routes import sessions as sessions_route
from ai_coding_harness.config import Settings
from ai_coding_harness.db import dao
from ai_coding_harness.session.manager import (
    WORKSPACE_PROVISIONING,
    WORKSPACE_READY,
    CreatedSession,
    Session,
    SessionManager,
)
from ai_coding_harness.tools.git import GiteeRepo
from ai_coding_harness.tools.repo_access import RepoAccessError

REPO_URL = "https://gitee.com/owner/repo.git"


class FakeStore:
    """假库：落过的会话行 + 仓库行，外加一把按仓库互斥的锁。

    锁在**事务结束时**释放（真库用的是事务级咨询锁），所以由 ``FakeDatabase`` 的上下文
    管理器放开 —— 只学「一把锁跨住查+写」这一点，够用来钉住拿锁与查的先后。
    """

    def __init__(self) -> None:
        self.sessions: list[SimpleNamespace] = []
        self.repos: list[SimpleNamespace] = []
        self.lock_calls: list[str] = []
        self._lock = asyncio.Lock()

    async def get(self, _model: object, _key: object) -> None:
        """UserRuntimeSettings lookup: tests use defaults."""

    async def scalars(self, _query: object) -> list[object]:
        """Per-user model endpoint lookup: tests use no model rows."""
        return []

    async def acquire(self, key: str) -> None:
        self.lock_calls.append(key)
        await self._lock.acquire()

    def release(self) -> None:
        if self._lock.locked():
            self._lock.release()

    def find(
        self,
        owner: str,
        name: str,
        *,
        user_id: int | None = None,
        platform: str | None = None,
    ) -> tuple[SimpleNamespace, SimpleNamespace] | None:
        for row, repo in zip(self.sessions, self.repos, strict=True):
            if (
                repo.owner.lower() == owner.lower()
                and repo.name.lower() == name.lower()
                and (platform is None or getattr(repo, "platform", "gitee") == platform)
                and row.status == "active"
                and (user_id is None or row.user_id == user_id)
            ):
                return row, repo
        return None

    def seed(
        self,
        *,
        owner: str = "owner",
        name: str = "repo",
        url: str = REPO_URL,
        token: str | None = None,
        status: str = "active",
        workspace_status: str = WORKSPACE_READY,
        user_id: int | None = None,
    ) -> SimpleNamespace:
        row = SimpleNamespace(
            id=len(self.sessions) + 1,
            public_id=uuid.uuid4(),
            user_id=user_id,
            status=status,
            gitee_token=token,
            gitee_username="oauth2",
            git_platform="gitee",
            credential_version=0,
            session_version=0,
            workspace_status=workspace_status,
        )
        self.sessions.append(row)
        self.repos.append(
            SimpleNamespace(
                session_id=row.id,
                owner=owner,
                name=name,
                url=url,
                platform="gitee",
            )
        )
        return row


class _Transaction:
    def __init__(self, store: FakeStore) -> None:
        self._store = store

    async def __aenter__(self) -> FakeStore:
        return self._store

    async def __aexit__(self, *exc: object) -> bool:
        self._store.release()
        return False


class FakeDatabase:
    def __init__(self, store: FakeStore) -> None:
        self._store = store

    def session(self) -> _Transaction:
        return _Transaction(self._store)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """把 ``create_session`` 用到的 dao 全换成假件（签名与真实实现一致）。"""
    st = FakeStore()

    async def lock_repo(
        db,
        *,
        owner: str,
        name: str,
        user_id: int | None = None,
        platform: str | None = None,
    ) -> None:
        scope = f"{platform}:{owner}/{name}".lower()
        if user_id is not None:
            scope = f"user:{user_id}:{scope}"
        await st.acquire(scope)

    async def find_active_session_by_repo(
        db,
        *,
        owner: str,
        name: str,
        user_id: int | None = None,
        platform: str | None = None,
    ):
        await asyncio.sleep(0)  # 让并发的另一方有机会插进来（真查询也有 I/O 点）
        return st.find(owner, name, user_id=user_id, platform=platform)

    async def create_session(
        db,
        *,
        public_id,
        user_id=None,
        gitee_token=None,
        gitee_username=None,
        git_platform=None,
        title=None,
    ):
        await asyncio.sleep(0)
        row = SimpleNamespace(
            id=len(st.sessions) + 1,
            public_id=public_id,
            user_id=user_id,
            status="active",
            # 存占位符而不是真值：断言失败时 pytest 会打出 locals，而令牌不许进任何输出
            gitee_token=("tok" if gitee_token else None),
            gitee_username=gitee_username,
            git_platform="gitee",
            credential_version=0,
            session_version=0,
            workspace_status="pending",
        )
        st.sessions.append(row)
        return row

    async def create_repository(
        db, *, session_id, owner, name, url, platform="gitee"
    ) -> None:
        st.repos.append(
            SimpleNamespace(
                session_id=session_id,
                owner=owner,
                name=name,
                url=url,
                platform=platform,
            )
        )

    monkeypatch.setattr(dao, "lock_repo", lock_repo)
    monkeypatch.setattr(dao, "find_active_session_by_repo", find_active_session_by_repo)
    monkeypatch.setattr(dao, "create_session", create_session)
    monkeypatch.setattr(dao, "create_repository", create_repository)
    return st


def make_manager(store: FakeStore) -> SessionManager:
    return SessionManager(
        settings=Settings(verify_repo_access=False),
        model_for=lambda role: SimpleNamespace(),
        fallback_for=lambda role: None,
        web_search_tool=SimpleNamespace(),
        store=SimpleNamespace(),
        checkpointer=SimpleNamespace(),
        database=FakeDatabase(store),
    )


async def test_new_repo_creates_a_session(store: FakeStore) -> None:
    created = await make_manager(store).create_session(REPO_URL)

    assert created.reused is False
    assert created.workspace_status == WORKSPACE_PROVISIONING
    assert [row.public_id for row in store.sessions] == [
        uuid.UUID(created.session.public_id)
    ]
    assert [(r.owner, r.name, r.url) for r in store.repos] == [("owner", "repo", REPO_URL)]


async def test_repository_access_is_checked_before_creating_a_session(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = make_manager(store)
    manager._settings.verify_repo_access = True

    async def denied(settings, repo):
        raise RepoAccessError("无权访问")

    monkeypatch.setattr("ai_coding_harness.session.manager.verify_repo_access", denied)

    with pytest.raises(RepoAccessError, match="无权访问"):
        await manager.create_session(REPO_URL)
    assert store.sessions == []
    assert store.repos == []


async def test_same_repo_reuses_the_active_session(store: FakeStore) -> None:
    """第二个会话不再落库：id、凭据、工作区状态都取库里那一份。"""
    seeded = store.seed()
    created = await make_manager(store).create_session(REPO_URL)

    assert created.reused is True
    assert created.session.public_id == str(seeded.public_id)
    assert created.session.db_id == seeded.id
    assert created.workspace_status == WORKSPACE_READY
    assert created.session.repo.token == "", "凭据取库里那份，不回落 .env"
    assert len(store.sessions) == 1, "复用不该再落一行"
    assert len(store.repos) == 1


async def test_repo_url_writing_does_not_escape_reuse(store: FakeStore) -> None:
    """``.git``、末尾斜杠、大小写换个写法仍算同一个仓库 —— 否则唯一性一绕就过。"""
    store.seed(owner="Owner", name="Repo", url="https://gitee.com/Owner/Repo.git")

    created = await make_manager(store).create_session("https://gitee.com/owner/repo/")

    assert created.reused is True
    assert len(store.sessions) == 1
    assert created.session.repo_url == "https://gitee.com/Owner/Repo.git", "地址用库里那份"


async def test_concurrent_create_for_one_repo_keeps_one_session(
    store: FakeStore,
) -> None:
    """并发建同一仓库只落一行：拿锁必须在查之前，否则两边都查空、各建一个会话。"""
    manager = make_manager(store)

    first, second = await asyncio.gather(
        manager.create_session(REPO_URL), manager.create_session(REPO_URL)
    )

    assert len(store.sessions) == 1
    assert sorted([first.reused, second.reused]) == [False, True]
    assert first.session.public_id == second.session.public_id


async def test_same_repo_is_isolated_between_users(store: FakeStore) -> None:
    manager = make_manager(store)

    first = await manager.create_session(REPO_URL, user_id=7)
    second = await manager.create_session(REPO_URL, user_id=8)

    assert first.reused is False
    assert second.reused is False
    assert first.session.public_id != second.session.public_id
    assert [row.user_id for row in store.sessions] == [7, 8]


class FakeManager:
    """路由层只要这两个方法。"""

    def __init__(self, created: CreatedSession) -> None:
        self._created = created
        self.provisioned: list[str] = []

    async def create_session(
        self,
        repo_url: str,
        *,
        user_id: int | None = None,
    ) -> CreatedSession:
        self.repo_url = repo_url
        self.user_id = user_id
        return self._created

    def start_provisioning(self, session: Session) -> None:
        self.provisioned.append(session.public_id)


def make_app(manager: FakeManager) -> FastAPI:
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.state.manager = manager
    app.state.sessions = {}
    return app


def make_created(*, reused: bool, workspace_status: str) -> CreatedSession:
    return CreatedSession(
        session=Session(
            public_id="11111111-1111-1111-1111-111111111111",
            db_id=1,
            repo=GiteeRepo(owner="owner", repo="repo", token="tok", git_username="oauth2"),
            repo_url=REPO_URL,
        ),
        reused=reused,
        workspace_status=workspace_status,
    )


async def _post(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/sessions", json={"repo_url": REPO_URL})


async def test_route_reports_reuse_and_skips_provisioning() -> None:
    """复用时路由不能踢工作区（它可能早就是 ready），且要把既有会话登记进内存。"""
    created = make_created(reused=True, workspace_status=WORKSPACE_READY)
    manager = FakeManager(created)
    app = make_app(manager)

    response = await _post(app)

    assert response.status_code == 200
    assert response.json() == {
        "session_id": created.session.public_id,
        "workspace_status": WORKSPACE_READY,
        "reused": True,
    }
    assert manager.provisioned == []
    assert app.state.sessions == {created.session.public_id: created.session}


async def test_route_provisions_a_new_session() -> None:
    created = make_created(reused=False, workspace_status=WORKSPACE_PROVISIONING)
    manager = FakeManager(created)

    response = await _post(make_app(manager))

    assert response.json()["reused"] is False
    assert manager.provisioned == [created.session.public_id]
