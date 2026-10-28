"""Single-user login, logout, current-user and password-change routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ai_coding_harness.api.schemas import (
    AuthConfigInfo,
    AuthSessionResponse,
    AuthUserInfo,
    ChangePasswordRequest,
    LoginRequest,
)
from ai_coding_harness.auth.service import AuthFailure, AuthService, AuthUser, LoginSession
from ai_coding_harness.config import Settings
from ai_coding_harness.db import dao

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_user(user: AuthUser) -> AuthUserInfo:
    return AuthUserInfo(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        must_change_password=user.must_change_password,
    )


def _secure_cookie(request: Request, settings: Settings) -> bool:
    return settings.auth_cookie_secure or request.url.scheme == "https"


def _set_login_cookies(
    response: JSONResponse,
    *,
    request: Request,
    settings: Settings,
    login: LoginSession,
) -> None:
    secure = _secure_cookie(request, settings)
    max_age = max(1, settings.auth_session_ttl_hours) * 3600
    response.set_cookie(
        settings.auth_session_cookie,
        login.token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.auth_csrf_cookie,
        login.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_login_cookies(
    response: JSONResponse, *, request: Request, settings: Settings
) -> None:
    secure = _secure_cookie(request, settings)
    response.delete_cookie(
        settings.auth_session_cookie,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    response.delete_cookie(
        settings.auth_csrf_cookie,
        path="/",
        httponly=False,
        secure=secure,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/config", response_model=AuthConfigInfo)
async def auth_config(request: Request) -> AuthConfigInfo:
    """Public capability probe used by the login page."""
    async with request.app.state.database.session() as db:
        initialized = await dao.count_users(db) > 0
    return AuthConfigInfo(
        initialized=initialized,
        csrf_cookie=request.app.state.settings.auth_csrf_cookie,
    )


@router.post("/login")
async def login(req: LoginRequest, request: Request) -> JSONResponse:
    service: AuthService = request.app.state.auth_service
    client = request.client
    login_result = await service.login(
        username=req.username,
        password=req.password,
        ip=client.host if client is not None else "",
        user_agent=request.headers.get("user-agent", ""),
    )
    if login_result is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误。")
    response = JSONResponse(
        AuthSessionResponse(user=_auth_user(login_result.user)).model_dump()
    )
    _set_login_cookies(
        response,
        request=request,
        settings=request.app.state.settings,
        login=login_result,
    )
    return response


@router.get("/me", response_model=AuthSessionResponse)
async def me(request: Request) -> AuthSessionResponse:
    user: AuthUser = request.state.auth.user
    return AuthSessionResponse(user=_auth_user(user))


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    service: AuthService = request.app.state.auth_service
    await service.revoke_current(request.state.auth)
    response = JSONResponse({"ok": True})
    _clear_login_cookies(
        response,
        request=request,
        settings=request.app.state.settings,
    )
    return response


@router.post("/change-password", response_model=AuthSessionResponse)
async def change_password(
    req: ChangePasswordRequest, request: Request
) -> JSONResponse:
    service: AuthService = request.app.state.auth_service
    client = request.client
    try:
        login_result = await service.change_password(
            principal=request.state.auth,
            current_password=req.current_password,
            new_password=req.new_password,
            ip=client.host if client is not None else "",
            user_agent=request.headers.get("user-agent", ""),
        )
    except AuthFailure as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    response = JSONResponse(
        AuthSessionResponse(user=_auth_user(login_result.user)).model_dump()
    )
    _set_login_cookies(
        response,
        request=request,
        settings=request.app.state.settings,
        login=login_result,
    )
    return response
