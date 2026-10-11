"""``GET /sessions/{id}/memory``：仓库记忆的只读视图。

真 store 是 Postgres 上的 LangGraph store，测试里不连库；用一个替身，钉住的正是这一层最容易
出错的两件事：

1. **读的是哪把 key** —— 记忆写入走 ``StoreBackend``（namespace ``("memories",)`` + 文件路径），
   这个接口必须落在同一把 key 上。读错 namespace 不会报错，只会永远显示「没有记忆」（历史上
   服务层与工具层各拼一份路径，出过「写入成功没人读得到」的事故）。
2. **没有记忆时返回什么** —— ``content: null``（没写过），不是空串、也不是 404。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from ai_coding_harness.api.routes import sessions as sessions_route
from ai_coding_harness.backends.composite import (
    MEMORIES_NAMESPACE,
    memory_path,
    memory_store_key,
    read_memory,
)
from ai_coding_harness.config import Settings
from ai_coding_harness.db import dao

SESSION_ID = "6f1a7c58-0000-4000-8000-000000000001"
PUBLIC_ID = UUID(SESSION_ID)
OWNER, REPO = "acme", "demo"
MEMORY = "# 仓库记忆：acme/demo\n\n> 最后更新：2026-10-10 09:12　｜　最近 PR：#7 待合并\n"


class _FakeStore:
    """只实现 ``aget``（接口用到的那一个）；把每次读的 ``(namespace, key)`` 记下来。"""

    def __init__(self, value: dict[str, Any] | None) -> None:
        self._value = value
        self.reads: list[tuple[tuple[str, ...], str]] = []

    async def aget(self, namespace: tuple[str, ...], key: str) -> Any:
        self.reads.append((tuple(namespace), key))
        if self._value is None:
            return None
        return SimpleNamespace(value=self._value)


class _FakeDb:
    @asynccontextmanager
    async def session(self, *_a: Any, **_kw: Any):
        yield None


def _app(store: _FakeStore, *, repo: Any = "auto") -> FastAPI:
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.state.database = _FakeDb()
    app.state.store = store
    app.state.settings = Settings(_env_file=None)
    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def _patch_session(monkeypatch: pytest.MonkeyPatch, repo: Any) -> None:
    async def get_session_with_repo(db: Any, *, public_id: UUID) -> Any:
        assert public_id == PUBLIC_ID
        row = SimpleNamespace(id=1, public_id=public_id)
        return row, repo

    monkeypatch.setattr(dao, "get_session_with_repo", get_session_with_repo)


def _repo_row() -> Any:
    return SimpleNamespace(owner=OWNER, name=REPO, url="https://gitee.com/acme/demo.git")


async def test_it_reads_the_key_the_composite_actually_wrote(monkeypatch) -> None:
    """读的必须是**复合后端真正落盘的那把 key**。

    两层要分开看：响应里的 ``path`` 是**给用户看的逻辑路径**（带 ``/memories`` 前缀，与
    ``save_repo_memory`` 同一口径），而 store 里的 key **被剥掉了路由前缀** —— 复合后端命中
    路由后先剥前缀再交给子后端。这条断言以前写成了拿 ``memory_path()`` 当 key（错误假设），
    于是「读不到已有记忆」的 bug 一路绿灯；真正的端到端回归在 ``test_composite_backend``。
    """
    _patch_session(monkeypatch, _repo_row())
    store = _FakeStore({"content": MEMORY})
    settings = Settings(_env_file=None)

    resp = await _get(_app(store), f"/sessions/{SESSION_ID}/memory")

    assert resp.status_code == 200
    body = resp.json()
    logical = memory_path(OWNER, REPO, settings.memories_prefix)
    key = memory_store_key(OWNER, REPO, settings.memories_prefix)
    assert body["content"] == MEMORY, "全文原样返回"
    assert body["path"] == logical, "回给前端的路径是逻辑路径"
    assert key == f"/{OWNER}/{REPO}.md", "store 里存的是剥掉前缀的那半截"
    assert store.reads == [(MEMORIES_NAMESPACE, key)], "namespace 与 key 都要对"
    # 与 ``build_composite_backend`` 里那条路由同一个常量（那边也用的它，改一处就两边一起变）
    assert MEMORIES_NAMESPACE == ("memories",)


async def test_a_repo_without_memory_returns_null_not_an_error(monkeypatch) -> None:
    """没写过记忆 ＝ ``content: null``（前端显示空态）；不是空串、也不是 404。"""
    _patch_session(monkeypatch, _repo_row())

    resp = await _get(_app(_FakeStore(None)), f"/sessions/{SESSION_ID}/memory")

    assert resp.status_code == 200
    assert resp.json()["content"] is None


async def test_a_legacy_list_content_is_joined(monkeypatch) -> None:
    """老的 ``list[str]`` 内容也要能读（库自己的归一化认这种形状）。"""
    _patch_session(monkeypatch, _repo_row())

    resp = await _get(
        _app(_FakeStore({"content": ["# 仓库记忆", "第二行"]})),
        f"/sessions/{SESSION_ID}/memory",
    )

    assert resp.status_code == 200
    assert "第二行" in resp.json()["content"]


async def test_a_bad_id_is_400_and_a_missing_session_is_404(monkeypatch) -> None:
    _patch_session(monkeypatch, _repo_row())
    store = _FakeStore({"content": MEMORY})

    assert (await _get(_app(store), "/sessions/not-a-uuid/memory")).status_code == 400

    async def missing(db: Any, *, public_id: UUID) -> Any:
        return None

    monkeypatch.setattr(dao, "get_session_with_repo", missing)
    assert (await _get(_app(store), f"/sessions/{SESSION_ID}/memory")).status_code == 404
    assert store.reads == [], "会话都没找到，不该去读 store"


async def test_a_session_without_repo_is_409(monkeypatch) -> None:
    """没有仓库信息的历史会话：返 409 说清（与 ``resolve_session`` 同一口径）。"""
    _patch_session(monkeypatch, None)

    resp = await _get(_app(_FakeStore({"content": MEMORY})), f"/sessions/{SESSION_ID}/memory")

    assert resp.status_code == 409


async def test_read_memory_ignores_a_broken_item_shape() -> None:
    """store 里那条记录形状不对（不是 dict）时按「没有记忆」处理，别把接口带崩。"""
    item = SimpleNamespace(value=["坏数据"])

    class _Store:
        async def aget(self, namespace: tuple[str, ...], key: str) -> Any:
            return item

    assert await read_memory(_Store(), OWNER, REPO) is None  # type: ignore[arg-type]
