"""Authentication primitives and the replaceable identity-provider boundary."""

from .service import (
    AuthFailure,
    AuthLocked,
    AuthPrincipal,
    AuthService,
    AuthUser,
    LoginSession,
)

__all__ = [
    "AuthFailure",
    "AuthLocked",
    "AuthPrincipal",
    "AuthService",
    "AuthUser",
    "LoginSession",
]
