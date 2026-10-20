"""``GET /sessions/{id}/pulls``：右栏「PR」标签的数据源。

这一层要钉住三件事：

1. **查不到 ≠ 没有** —— 查询失败必须 502 + 原因，**不能**返回空列表。界面上「这个仓库
   一个 PR 都没有」和「这次没查成」是两句话，混起来就会让人以为改动都合了。
2. **状态判定与闸门同一套** —— 面板说「还开着」、闸门说「没有未合入的」，就是自相矛盾。
   这里直接拿 ``memory_reconcile`` 的那份判定做对照断言。
3. **排序** —— 未合并的排最前（打开面板第一眼要看到还开着的），其余按编号倒序。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ai_coding_harness.agent.middleware import memory_reconcile
from ai_coding_harness.api.routes import sessions as sessions_route
from ai_coding_harness.config import Settings
from ai_coding_harness.core.codeplatform import Platform, RepoSpec
from ai_coding_harness.tools.pr_client import PrLookupError, pull_state


def _pull(number: int, *, state: str = "open", merged: bool = False, **over: Any) -> dict:
    """一条平台原始形状的 PR（字段名按 Gitee / GitHub 共有的那套）。"""
    pull = {
        "number": number,
        "title": f"改动 {number}",
        "state": state,
        "html_url": f"https://gitee.com/acme/demo/pulls/{number}",
        "head": {"ref": f"feat/{number}"},
        "user": {"login": "zhang"},
        "created_at": "2026-10-01T10:00:00+08:00",
        "updated_at": "2026-10-02T10:00:00+08:00",
    }
    if merged:
        pull["merged_at"] = "2026-10-03T10:00:00+08:00"
    pull.update(over)
    return pull


class _FakeClient:
    """替掉 ``build_git_client``：要么给一组 PR，要么按纪律抛 ``PrLookupError``。"""

    def __init__(self, pulls: Any = None, error: Exception | None = None) -> None:
        self._pulls = pulls
        self._error = error

    async def all_pulls(self) -> list[dict]:
        if self._error is not None:
            raise self._error
        return self._pulls


def _session() -> Any:
    return SimpleNamespace(
        public_id="s-1",
        db_id=1,
        repo=RepoSpec(owner="acme", repo="demo", token="t", platform=Platform.GITEE),
        repo_url="https://gitee.com/acme/demo.git",
    )


def _app(client: _FakeClient, session: Any = "auto") -> FastAPI:
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.state.settings = Settings(_env_file=None)
    app.state.sessions = {"s-1": _session() if session == "auto" else session}
    return app


async def _get(app: FastAPI, path: str = "/sessions/s-1/pulls") -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def _patch_factory(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(sessions_route, "build_git_client", lambda *a, **kw: client)


async def test_it_normalizes_and_sorts_open_first(monkeypatch) -> None:
    client = _FakeClient(
        [
            _pull(3, state="closed"),
            _pull(7, state="open"),
            _pull(5, merged=True, state="closed"),
            {"number": None, "title": "没有编号的脏数据"},
        ]
    )
    _patch_factory(monkeypatch, client)

    resp = await _get(_app(client))

    assert resp.status_code == 200
    body = resp.json()
    assert [p["number"] for p in body["pulls"]] == ["7", "5", "3"], "未合并最前，其余倒序"
    assert [p["state"] for p in body["pulls"]] == ["open", "merged", "closed"]
    assert body["open_count"] == 1
    first = body["pulls"][0]
    assert first["branch"] == "feat/7" and first["author"] == "zhang"
    assert first["url"].endswith("/pulls/7")


async def test_a_lookup_failure_is_502_not_an_empty_list(monkeypatch) -> None:
    """**这条是本次最值钱的断言**：查不到不能装成「没有」。"""
    client = _FakeClient(error=PrLookupError("HTTP 401 token 未配置"))
    _patch_factory(monkeypatch, client)

    resp = await _get(_app(client))

    assert resp.status_code == 502
    assert "查询本仓库 PR 失败" in resp.json()["detail"]
    assert "401" in resp.json()["detail"], "原因要原样透传，用户才知道去修什么"


def test_the_panel_and_the_gate_agree_on_every_state() -> None:
    """面板与闸门**同一套判定**：同一条原始 PR，两边的结论不能不一样。

    历史教训的形状：两处各写一份状态映射，于是面板说「还开着」而闸门按「没有未合入」放行。
    """
    cases = [
        _pull(1, state="open"),
        _pull(2, merged=True, state="closed"),
        _pull(3, state="merged"),  # Gitee 的写法
        _pull(4, state="rejected"),
        _pull(5, state="closed"),
        _pull(6, state="未来的新状态"),  # 认不出的：两边都必须「不猜」
    ]
    expected = {
        "open": memory_reconcile.PENDING,
        "merged": memory_reconcile.MERGED,
        "closed": memory_reconcile.DISCARDED,
        "unknown": "",
    }

    for pull in cases:
        code = pull_state(pull)
        assert memory_reconcile._pull_state(pull) == expected[code], f"判定漂了：{pull}"

    # 闸门只认「待合并」那一档
    assert {p.number for p in memory_reconcile._open_pulls(cases)} == {"1"}


async def test_an_unknown_session_is_rejected(monkeypatch) -> None:
    """不在内存注册表里就去库里找；库里也没有 ＝ 404（与其它会话接口同一口径）。

    顺带钉住一件要紧的事：**会话都没找到就不该去查 PR**（那是两次外发）。
    """
    from contextlib import asynccontextmanager

    from ai_coding_harness.db import dao

    client = _FakeClient([_pull(1)])
    _patch_factory(monkeypatch, client)

    class _Db:
        @asynccontextmanager
        async def session(self, *_a: Any, **_kw: Any):
            yield None

    async def no_such_session(db: Any, *, public_id: Any) -> Any:
        return None

    monkeypatch.setattr(dao, "get_session_with_repo", no_such_session)
    app = _app(client, session=None)
    app.state.database = _Db()

    resp = await _get(app, "/sessions/6f1a7c58-0000-4000-8000-000000000009/pulls")

    assert resp.status_code == 404
