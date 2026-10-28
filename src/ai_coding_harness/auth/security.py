"""Password hashing and opaque token helpers.

Passwords use hashlib.scrypt so local authentication works without an extra runtime
dependency. Session and CSRF tokens are random opaque values; only their SHA-256 hashes
are persisted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import unicodedata

_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_PASSWORD_MIN = 12
_PASSWORD_MAX = 128


def normalize_username(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


def password_problem(password: str) -> str | None:
    if len(password) < _PASSWORD_MIN:
        return f"密码至少需要 {_PASSWORD_MIN} 位。"
    if len(password) > _PASSWORD_MAX:
        return f"密码不能超过 {_PASSWORD_MAX} 位。"
    if "\x00" in password:
        return "密码包含非法字符。"
    return None


def hash_password(password: str) -> str:
    problem = password_problem(password)
    if problem is not None:
        raise ValueError(problem)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Used for nonexistent usernames so login keeps a similar password-verification cost.
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")
