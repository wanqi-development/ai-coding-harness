"""Global authentication and CSRF enforcement for the HTTP application."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ai_coding_harness.auth.service import AuthPrincipal

PUBLIC_PATHS = frozenset({
    "/health",
    "/auth/config",
    "/auth/login",
})
FORCE_CHANGE_ALLOWED_PATHS = frozenset({
    "/auth/me",
    "/auth/logout",
    "/auth/change-password",
})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _is_admin_only(path: str, method: str) -> bool:
    if path == "/users" or path.startswith("/users/"):
        return True
    if path == "/app-settings" or path.startswith("/app-settings/"):
        return True
    if path == "/model-config/providers/reload":
        return True
    return path.startswith("/model-config/providers/") and method in {"PUT", "DELETE"}


class AuthMiddleware:
    """Pure ASGI auth gate; BaseHTTPMiddleware buffers and must not wrap SSE."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path.rstrip("/") or "/"
        if request.method == "OPTIONS" or path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        service = getattr(request.app.state, "auth_service", None)
        settings = request.app.state.settings
        if service is None:
            response = JSONResponse({"detail": "认证服务未就绪"}, status_code=503)
            await response(scope, receive, send)
            return
        principal: AuthPrincipal | None = await service.resolve_cookie(
            request.cookies.get(settings.auth_session_cookie)
        )
        if principal is None:
            response = JSONResponse(
                {"detail": "未登录或登录已失效"}, status_code=401
            )
            await response(scope, receive, send)
            return
        request.state.auth = principal
        request.state.auth_user = principal.user
        if (
            principal.user.must_change_password
            and path not in FORCE_CHANGE_ALLOWED_PATHS
        ):
            response = JSONResponse(
                {
                    "detail": "首次登录或密码被重置后，必须先修改密码。",
                    "code": "password_change_required",
                },
                status_code=403,
            )
            await response(scope, receive, send)
            return
        if _is_admin_only(path, request.method) and principal.user.role != "admin":
            response = JSONResponse(
                {"detail": "需要管理员权限", "code": "admin_required"},
                status_code=403,
            )
            await response(scope, receive, send)
            return
        if request.method in UNSAFE_METHODS and not service.csrf_matches(
            principal,
            cookie=request.cookies.get(settings.auth_csrf_cookie, ""),
            header=request.headers.get("X-CSRF-Token", ""),
        ):
            response = JSONResponse({"detail": "CSRF 校验失败"}, status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
