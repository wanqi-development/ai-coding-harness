"""OAuth 登录路由（Gitee / GitHub）：不联网，换 token 那一步用 MockTransport 截获。

真去连 gitee.com / github.com 的用例在离线机器上跑不了，也会因为「网络抖一下」变红。
路由里建 HTTP 客户端的那一步留了个可替换的工厂（``auth._client_factory``），这里换成
挂了 ``httpx.MockTransport`` 的客户端 —— 顺带能在同一处断言「请求头带没带
``Accept: application/json``」这种两家平台的差异。
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from ai_coding_harness.api.routes import auth as auth_route
from ai_coding_harness.config import Settings

GH = {
    "github_oauth_client_id": "gh-id",
    "github_oauth_client_secret": "gh-secret",
    "github_oauth_redirect_uri": "http://localhost:5173/auth/github/callback",
}
GITEE = {
    "gitee_oauth_client_id": "gitee-id",
    "gitee_oauth_client_secret": "gitee-secret",
    "gitee_oauth_redirect_uri": "http://localhost:5173/auth/gitee/callback",
}


def _app(**overrides: Any) -> FastAPI:
    """只挂 auth 路由的最小应用（``app.state.settings`` 是路由唯一的依赖）。"""
    app = FastAPI()
    app.include_router(auth_route.router)
    app.state.settings = Settings(_env_file=None, **overrides)  # type: ignore[arg-type]
    return app


def _stub_client(handler: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """把「建 HTTP 客户端」换成走 MockTransport 的客户端。"""

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(auth_route, "_client_factory", factory)


async def _get(
    app: FastAPI, path: str, params: dict[str, str] | None = None
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, params=params)


def _query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query))


async def _issued_state(platform: str, app: FastAPI) -> str:
    """先走一次 authorize，把服务端签发的那份 state 取出来（回调要用它）。"""
    resp = await _get(app, f"/auth/{platform}/authorize")
    assert resp.status_code in (302, 307), resp.text
    return _query(resp.headers["location"])["state"]


def _ok_token_handler(payload: dict[str, Any], seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    return handler


# -- 授权页跳转 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "authorize_url", "scope", "client_id", "redirect_uri"),
    [
        (
            "/auth/github/authorize",
            "https://github.com/login/oauth/authorize",
            "repo",
            "gh-id",
            GH["github_oauth_redirect_uri"],
        ),
        (
            "/auth/gitee/authorize",
            "https://gitee.com/oauth/authorize",
            "projects",
            "gitee-id",
            GITEE["gitee_oauth_redirect_uri"],
        ),
    ],
)
async def test_authorize_jumps_to_the_platform(
    path: str, authorize_url: str, scope: str, client_id: str, redirect_uri: str
) -> None:
    resp = await _get(_app(**GH, **GITEE), path)

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert location.startswith(f"{authorize_url}?")
    query = _query(location)
    assert query["client_id"] == client_id
    assert query["scope"] == scope
    assert query["response_type"] == "code"
    assert query["redirect_uri"] == redirect_uri
    assert query["state"], "必须带上 state（防 CSRF）"


@pytest.mark.parametrize(
    ("path", "label"), [("/auth/github/authorize", "GitHub"), ("/auth/gitee/authorize", "Gitee")]
)
async def test_authorize_without_config_is_a_clear_500(path: str, label: str) -> None:
    resp = await _get(_app(), path)  # 三件套一个都没配

    assert resp.status_code == 500
    assert label in resp.json()["detail"] and "未配置" in resp.json()["detail"]


# -- 回调换 token -----------------------------------------------------------


async def test_github_callback_exchanges_the_code(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    _stub_client(
        _ok_token_handler(
            {"access_token": "gho_token", "scope": "repo", "token_type": "bearer"}, seen
        ),
        monkeypatch,
    )
    app = _app(**GH)

    resp = await _get(
        app,
        "/auth/github/callback",
        {"code": "the-code", "state": await _issued_state("github", app)},
    )

    assert resp.status_code == 200
    # 两个平台同一个形状（GitHub 不回 expires_in，那里就是 None）
    assert resp.json() == {"access_token": "gho_token", "scope": "repo", "expires_in": None}
    request = seen[0]
    assert str(request.url) == "https://github.com/login/oauth/access_token"
    assert request.headers["accept"] == "application/json", "GitHub 必须显式要 JSON"
    form = dict(parse_qsl(request.content.decode()))
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "the-code"
    assert form["client_id"] == "gh-id" and form["client_secret"] == "gh-secret"


async def test_gitee_callback_sends_no_accept_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gitee 默认就回 JSON，多带一个头只会多一份要维护的差异。"""
    seen: list[httpx.Request] = []
    _stub_client(
        _ok_token_handler({"access_token": "gitee_token", "scope": "projects"}, seen),
        monkeypatch,
    )
    app = _app(**GITEE)

    resp = await _get(
        app,
        "/auth/gitee/callback",
        {"code": "c", "state": await _issued_state("gitee", app)},
    )

    assert resp.status_code == 200
    assert resp.json()["access_token"] == "gitee_token"
    # ``*/*`` 是 httpx 自己的默认头 —— 也就是说我们**没有**显式替 Gitee 要 JSON
    assert seen[0].headers["accept"] == "*/*"


async def test_github_callback_treats_a_200_with_error_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub 授权码无效时**回 200**、错误在 body 里 —— 只看状态码就会当成成功。"""
    seen: list[httpx.Request] = []
    _stub_client(
        _ok_token_handler(
            {"error": "bad_verification_code", "error_description": "bad code"}, seen
        ),
        monkeypatch,
    )
    app = _app(**GH)

    resp = await _get(
        app,
        "/auth/github/callback",
        {"code": "bad", "state": await _issued_state("github", app)},
    )

    assert resp.status_code == 400
    assert "bad_verification_code" in resp.json()["detail"]


async def test_a_non_json_token_response_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    """端点回表单文本（忘了 Accept: application/json 时 GitHub 就这样）：说清原因。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="access_token=abc&scope=repo")

    _stub_client(handler, monkeypatch)
    app = _app(**GH)

    resp = await _get(
        app,
        "/auth/github/callback",
        {"code": "c", "state": await _issued_state("github", app)},
    )

    assert resp.status_code == 502
    assert "不是 JSON" in resp.json()["detail"]


async def test_an_endpoint_failure_passes_the_status_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad credentials")

    _stub_client(handler, monkeypatch)
    app = _app(**GH)

    resp = await _get(
        app,
        "/auth/github/callback",
        {"code": "c", "state": await _issued_state("github", app)},
    )

    assert resp.status_code == 401
    assert "bad credentials" in resp.json()["detail"]


async def test_callback_without_secret_is_a_clear_500() -> None:
    resp = await _get(
        _app(github_oauth_client_id="gh-id"), "/auth/github/callback", {"code": "c", "state": "x"}
    )

    assert resp.status_code == 500
    assert "未配置" in resp.json()["detail"]


# -- state（防 CSRF）--------------------------------------------------------


async def test_a_forged_state_is_rejected_without_calling_the_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """伪造的 state 一律 400，而且**不该**把授权码送去端点（那也是一次外发）。"""
    seen: list[httpx.Request] = []
    _stub_client(_ok_token_handler({"access_token": "x"}, seen), monkeypatch)

    resp = await _get(
        _app(**GH), "/auth/github/callback", {"code": "c", "state": "1.2.3"}
    )

    assert resp.status_code == 400
    assert "state" in resp.json()["detail"]
    assert seen == [], "state 不对就不该外发请求"


async def test_a_state_signed_for_the_other_platform_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用各自平台的 client_secret 签名，于是 Gitee 签的 state 在 GitHub 的回调上过不了。"""
    seen: list[httpx.Request] = []
    _stub_client(_ok_token_handler({"access_token": "x"}, seen), monkeypatch)
    gitee_state = await _issued_state("gitee", _app(**GITEE))

    resp = await _get(
        _app(**GH), "/auth/github/callback", {"code": "c", "state": gitee_state}
    )

    assert resp.status_code == 400
    assert seen == []


async def test_an_expired_state_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    _stub_client(_ok_token_handler({"access_token": "x"}, seen), monkeypatch)
    raw = f"{int(time.time()) - auth_route._STATE_MAX_AGE_SECONDS - 60}.nonce"
    stale = f"{raw}.{auth_route._signature('gh-secret', raw)}"

    resp = await _get(
        _app(**GH), "/auth/github/callback", {"code": "c", "state": stale}
    )

    assert resp.status_code == 400
    assert seen == []


async def test_each_authorize_mints_a_fresh_state() -> None:
    app = _app(**GH)

    first = await _issued_state("github", app)
    second = await _issued_state("github", app)

    assert first != second, "state 必须每次都换（否则可被重放）"
