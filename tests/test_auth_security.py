"""Password and token primitives for local authentication."""

from ai_coding_harness.auth.security import (
    hash_password,
    normalize_username,
    password_problem,
    token_hash,
    verify_password,
)


def test_password_hash_round_trip_and_rejects_wrong_password() -> None:
    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong password", encoded) is False
    assert hash_password("correct horse battery staple") != encoded


def test_password_length_rules() -> None:
    assert password_problem("short") is not None
    assert password_problem("a" * 12) is None
    assert password_problem("a" * 129) is not None


def test_username_normalization_and_token_hash() -> None:
    assert normalize_username("  Admin ") == "admin"
    assert token_hash("token") == token_hash("token")
    assert token_hash("token") != token_hash("other")
