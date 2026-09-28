"""OAuth 登录路由（Gitee / GitHub）：方案 A —— 前端拿 token 后随 ``create_session`` 带上。

两个平台只差三样东西（授权地址 / 换 token 地址 / scope），所以这里用一份 ``_OAuthApp``
描述 + 每个平台两个薄入口，公共逻辑（拼参数、换码、错误归一）只写一遍。逐字复制两份的
代价是以后每改一处都得记住改两处。

**平台差异**（实测口径，别想当然）：

- **Gitee**：``GET https://gitee.com/oauth/authorize``、
  ``POST https://gitee.com/oauth/token``（表单参数）；scope 用 ``projects``。
- **GitHub**：``GET https://github.com/login/oauth/authorize``、
  ``POST https://github.com/login/oauth/access_token`` —— **必须显式带 ``Accept:
  application/json``**，否则回的是 ``application/x-www-form-urlencoded`` 文本；而且授权码
  无效时它**回 200**、把 ``{"error": "bad_verification_code"}`` 放在 body 里，所以**不能
  只看状态码**（否则会拿着 ``access_token=None`` 往下走，那种失败最难查）。scope 用
  ``repo``：PR 创建 / 评论 / 读私有仓库都要它。

``state`` **自签、不落存储**（见 ``_sign_state``）：多个 worker 之间没有共享内存，而 Redis
是可选件 —— 自签的 state 无状态，跨进程、跨重启都能验，代价只是多一个 HMAC。

返回体两个平台**同一个形状**（``access_token`` / ``scope`` / ``expires_in``），前端不必
按平台分支；GitHub 不回 ``expires_in``，那里就是 ``None``。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ai_coding_harness.codeplatform import Platform

router = APIRouter(prefix="/auth", tags=["auth"])

#: 建 HTTP 客户端的那一步（测试替换它来挂 MockTransport，避免联网）
_client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient

_TOKEN_URLS = {
    Platform.GITEE: "https://gitee.com/oauth/token",
    Platform.GITHUB: "https://github.com/login/oauth/access_token",
}
_AUTHORIZE_URLS = {
    Platform.GITEE: "https://gitee.com/oauth/authorize",
    Platform.GITHUB: "https://github.com/login/oauth/authorize",
}
#: 要的权限。Gitee 的 ``projects`` 与 GitHub 的 ``repo`` 都覆盖「读私有仓库 + 操作 PR」
_SCOPES = {Platform.GITEE: "projects", Platform.GITHUB: "repo"}
#: 换 token 的请求头。GitHub 必须显式要 JSON（见模块开头），Gitee 默认就回 JSON
_TOKEN_HEADERS: dict[Platform, dict[str, str]] = {
    Platform.GITEE: {},
    Platform.GITHUB: {"Accept": "application/json"},
}
#: ``state`` 的有效期：授权页停太久（去建 OAuth App、切账号）再回来就重来一次
_STATE_MAX_AGE_SECONDS = 600
#: ``state`` 不合法时对外的统一措辞（**不区分**签名错 / 过期 / 格式错，见 ``_check_state``）
_STATE_BAD = "state 校验失败（防 CSRF 用）：请从 /auth/<平台>/authorize 重新发起登录"


def _sign_state(secret: str) -> str:
    """生成 ``state``：``<时间戳>.<随机串>.<HMAC>``。

    签名密钥用**该平台的 client_secret** —— 于是 Gitee 签发的 state 在 GitHub 的回调上
    也过不了（两边密钥不同），不必再加一个「这份 state 属于哪个平台」的字段。
    """
    raw = f"{int(time.time())}.{secrets.token_urlsafe(16)}"
    return f"{raw}.{_signature(secret, raw)}"


def _signature(secret: str, raw: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def _check_state(secret: str, state: str) -> None:
    """验 ``state``：签名对得上、且没过期。

    不成立的一律回同一句 400 —— 把「签名错」与「过期」分开说，等于给探测的人送情报。
    """
    parts = state.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail=_STATE_BAD)
    stamp, nonce, signature = parts
    if not hmac.compare_digest(_signature(secret, f"{stamp}.{nonce}"), signature):
        raise HTTPException(status_code=400, detail=_STATE_BAD)
    try:
        age = time.time() - int(stamp)
    except ValueError:
        raise HTTPException(status_code=400, detail=_STATE_BAD) from None
    if age > _STATE_MAX_AGE_SECONDS:
        raise HTTPException(status_code=400, detail=_STATE_BAD)


@dataclass(frozen=True)
class _OAuthApp:
    """一个平台的 OAuth 应用：端点常量 + 平台配置里的三件套（client_id / secret / redirect）。"""

    platform: Platform
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def label(self) -> str:
        return self.platform.label

    def authorize_redirect(self) -> RedirectResponse:
        """跳授权页。

        三件套缺一不可：没有 ``client_secret`` 就签不出 state（回调也换不了码），所以它
        在**这一端**也要求配上 —— 配一半的 OAuth 应用跳出去只会卡在回调那一步。
        """
        if not self.client_id or not self.client_secret or not self.redirect_uri:
            raise HTTPException(
                status_code=500,
                detail=f"{self.label} OAuth 未配置（client_id / client_secret / redirect_uri）",
            )
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": _SCOPES[self.platform],
                "state": _sign_state(self.client_secret),
            }
        )
        return RedirectResponse(f"{_AUTHORIZE_URLS[self.platform]}?{query}")

    async def exchange(self, code: str, state: str) -> dict[str, str | int | None]:
        """先验 ``state``（防 CSRF），再拿授权码换 access_token（换不成一律抛，不返回半成品）。"""
        if not self.client_id or not self.client_secret or not self.redirect_uri:
            raise HTTPException(
                status_code=500,
                detail=f"{self.label} OAuth 未配置（client_id / client_secret / redirect_uri）",
            )
        # 先验再换码：state 不对就没必要把授权码送去端点（那也是一次外发）
        _check_state(self.client_secret, state)
        async with _client_factory(timeout=30) as client:
            resp = await client.post(
                _TOKEN_URLS[self.platform],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri,
                },
                headers=_TOKEN_HEADERS[self.platform],
            )
        if resp.status_code != 200:
            # 原始文本可能很长（HTML 错误页），截断再抛；它只进响应体，不进日志
            raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])
        try:
            data = resp.json()
        # 忘了 Accept: application/json 时 GitHub 回的是表单文本 —— 说清是这个原因，
        # 别让人对着 "Expecting value: line 1 column 1" 猜
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"{self.label} 换 token 的响应不是 JSON：{resp.text[:200]}",
            ) from exc
        if data.get("error"):
            detail = " ".join(
                str(item)
                for item in (data.get("error"), data.get("error_description"))
                if item
            )
            raise HTTPException(status_code=400, detail=f"{self.label} 授权失败：{detail}")
        return {
            "access_token": data.get("access_token"),
            "scope": data.get("scope"),
            "expires_in": data.get("expires_in"),
        }


def _app(request: Request, platform: Platform) -> _OAuthApp:
    """按平台从 ``app.state.settings`` 取那份 OAuth 配置（两个平台各三个键）。"""
    settings = request.app.state.settings
    if platform is Platform.GITHUB:
        client_id = settings.github_oauth_client_id
        client_secret = settings.github_oauth_client_secret
        redirect_uri = settings.github_oauth_redirect_uri
    else:
        client_id = settings.gitee_oauth_client_id
        client_secret = settings.gitee_oauth_client_secret
        redirect_uri = settings.gitee_oauth_redirect_uri
    return _OAuthApp(
        platform=platform,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )


@router.get("/gitee/authorize")
async def gitee_authorize(request: Request) -> RedirectResponse:
    """跳转到 Gitee 授权页（用户登录并授权）。"""
    return _app(request, Platform.GITEE).authorize_redirect()


@router.get("/gitee/callback")
async def gitee_callback(
    request: Request, code: str, state: str
) -> dict[str, str | int | None]:
    """Gitee 授权回调：验 state → 用授权码换 access_token，返回给前端。"""
    return await _app(request, Platform.GITEE).exchange(code, state)


@router.get("/github/authorize")
async def github_authorize(request: Request) -> RedirectResponse:
    """跳转到 GitHub 授权页（用户登录并授权）。"""
    return _app(request, Platform.GITHUB).authorize_redirect()


@router.get("/github/callback")
async def github_callback(
    request: Request, code: str, state: str
) -> dict[str, str | int | None]:
    """GitHub 授权回调：验 state → 用授权码换 access_token（只回令牌，不落库、不进日志）。"""
    return await _app(request, Platform.GITHUB).exchange(code, state)
