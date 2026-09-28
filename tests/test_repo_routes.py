"""``/sessions/{id}/repo*`` 四个接口的契约：状态、同步、目录、文件，以及错误口径。

用真仓库 + 真缓存跑（夹具见 ``tests/git_repo.py``），不做 mock：这一层要钉住的正是
「git 的错误怎么变成用户看得懂的一句话」。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ai_coding_harness.api.routes import repo as repo_route
from ai_coding_harness.config import Settings
from ai_coding_harness.tools.git import GiteeRepo
from tests.git_repo import make_origin, requires_git

#: 本机没装 git 时整个文件跳过（与夹具模块同一个判据）
pytestmark = requires_git


class _Session:
    """``repo_cache`` 只看这几个字段（``RepoSpec.from_session``）。"""

    public_id = "s-1"
    repo = GiteeRepo(owner="acme", repo="demo", token="")

    def __init__(self, url: str) -> None:
        self.repo_url = url


def _app(settings: Settings, session: Any = None) -> FastAPI:
    """只装 repo 路由的最小应用：会话塞进内存注册表，免得 ``resolve_session`` 去连库。"""
    app = FastAPI()
    app.include_router(repo_route.router)
    app.state.settings = settings
    app.state.sessions = {"s-1": session} if session is not None else {}
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(repo_view_dir=str(tmp_path / "cache"))


@pytest.fixture
def app(settings: Settings, tmp_path: Path) -> FastAPI:
    origin = make_origin(tmp_path)
    return _app(settings, _Session(str(origin)))


class TestStatusAndSync:
    async def test_before_syncing_it_reports_no_cache(self, app):
        async with _client(app) as client:
            resp = await client.get("/sessions/s-1/repo")

        assert resp.status_code == 200
        body = resp.json()
        assert body["cloned"] is False
        assert body["branches"] == []
        assert body["fetched_at"] is None
        assert body["git"], "把探测到的 git 路径也告诉前端，便于排障"

    async def test_sync_clones_then_the_status_shows_branches(self, app):
        async with _client(app) as client:
            synced = await client.post("/sessions/s-1/repo/sync")
            status = await client.get("/sessions/s-1/repo")

        assert synced.status_code == 200
        first = synced.json()
        assert first["cloned_now"] is True, "这次是把仓库下载下来"
        assert first["default_branch"] == "master"
        assert [b["name"] for b in first["branches"]] == ["master", "feature/x"]
        assert first["branches"][0]["is_default"] is True
        assert first["fetched_at"]

        second = status.json()
        assert second["cloned"] is True
        assert second["fetched_at"] == first["fetched_at"]

    async def test_syncing_twice_only_fetches_the_second_time(self, app):
        async with _client(app) as client:
            await client.post("/sessions/s-1/repo/sync")
            again = await client.post("/sessions/s-1/repo/sync")

        assert again.json()["cloned_now"] is False


class TestTreeAndFile:
    async def _synced(self, app: FastAPI) -> None:
        async with _client(app) as client:
            await client.post("/sessions/s-1/repo/sync")

    async def test_tree_lists_one_level(self, app):
        await self._synced(app)

        async with _client(app) as client:
            resp = await client.get("/sessions/s-1/repo/tree", params={"path": "docs"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == "docs"
        assert body["entries"] == [
            {"name": "guide.md", "path": "docs/guide.md", "type": "file", "size": 6}
        ]

    async def test_file_returns_the_text_and_the_ref(self, app):
        await self._synced(app)

        async with _client(app) as client:
            resp = await client.get("/sessions/s-1/repo/file", params={"path": "readme.md"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ref"] == "master"
        assert body["text"] == "# hello\n"
        assert body["binary"] is False and body["too_big"] is False

    async def test_the_branch_decides_what_is_read(self, app):
        await self._synced(app)

        async with _client(app) as client:
            resp = await client.get(
                "/sessions/s-1/repo/file",
                params={"path": "readme.md", "ref": "feature/x"},
            )

        assert "feature 里改过的" in resp.json()["text"]

    async def test_too_big_is_not_an_error_but_a_flag(self, tmp_path, settings):
        settings.repo_view_file_max_bytes = 10
        app = _app(settings, _Session(str(make_origin(tmp_path))))
        await self._synced(app)

        async with _client(app) as client:
            resp = await client.get("/sessions/s-1/repo/file", params={"path": "big.txt"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["too_big"] is True
        assert body["text"] is None
        assert body["size"] == 500


class TestErrors:
    async def test_before_syncing_the_panel_is_told_to_sync(self, app):
        async with _client(app) as client:
            resp = await client.get("/sessions/s-1/repo/file", params={"path": "readme.md"})

        assert resp.status_code == 409
        assert "同步" in resp.json()["detail"]

    async def test_a_traversal_path_is_refused_with_a_readable_reason(self, app):
        async with _client(app) as client:
            await client.post("/sessions/s-1/repo/sync")
            resp = await client.get("/sessions/s-1/repo/file", params={"path": "../readme.md"})

        assert resp.status_code == 409
        assert "`..`" in resp.json()["detail"]

    async def test_an_unknown_branch_is_refused(self, app):
        async with _client(app) as client:
            await client.post("/sessions/s-1/repo/sync")
            resp = await client.get(
                "/sessions/s-1/repo/tree", params={"ref": "nosuch", "path": ""}
            )

        assert resp.status_code == 409
        assert "nosuch" in resp.json()["detail"]

    async def test_without_git_it_is_503_not_a_500(self, app, monkeypatch):
        monkeypatch.setattr("ai_coding_harness.repo_cache.find_git", lambda settings: None)
        # 路由在读状态时也调 find_git，这里让它返回 None（面板会显示「没找到 git」）
        async with _client(app) as client:
            resp = await client.post("/sessions/s-1/repo/sync")

        assert resp.status_code == 503
        assert "REPO_VIEW_GIT" in resp.json()["detail"]

    async def test_the_switch_turns_the_whole_thing_off(self, settings, tmp_path):
        settings.repo_view_enabled = False
        app = _app(settings, _Session(str(make_origin(tmp_path))))

        async with _client(app) as client:
            resp = await client.get("/sessions/s-1/repo")

        assert resp.status_code == 404
        assert "REPO_VIEW_ENABLED" in resp.json()["detail"]

    async def test_an_unknown_session_is_404(self, app, monkeypatch):
        """会话不在内存里时会落到库里查 —— 这里把库桩成「查不到」。"""

        async def _missing(db_session: Any, *, public_id: Any) -> None:
            return None

        @asynccontextmanager
        async def _session():
            yield object()

        monkeypatch.setattr(
            "ai_coding_harness.api.session_lookup.dao.get_session_with_repo", _missing
        )
        app.state.database = type("_Db", (), {"session": staticmethod(_session)})()
        app.state.sessions = {}

        async with _client(app) as client:
            resp = await client.get(
                "/sessions/11111111-1111-1111-1111-111111111111/repo"
            )

        assert resp.status_code == 404
