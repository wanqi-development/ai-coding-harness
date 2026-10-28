"""PostgreSQL connection timeout wiring and operator-facing diagnostics."""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

from ai_coding_harness.config import Settings
from ai_coding_harness.db import engine as engine_module
from ai_coding_harness.db.engine import Database
from ai_coding_harness.manage import _database_error_message


def test_database_passes_configured_connect_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_engine(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(engine_module, "create_async_engine", fake_engine)
    monkeypatch.setattr(
        engine_module, "async_sessionmaker", lambda *_args, **_kwargs: object()
    )
    settings = Settings(
        _env_file=None,
        postgres_connect_timeout_seconds=7,
    )

    Database(settings)

    assert captured["connect_args"] == {"connect_timeout": 7}


def test_database_error_is_actionable_and_does_not_echo_password() -> None:
    settings = Settings(
        _env_file=None,
        postgres_host="192.0.2.10",
        postgres_port=5433,
        postgres_db="harness_test",
        postgres_password="must-not-appear",
        postgres_connect_timeout_seconds=5,
    )

    class ConnectionTimeout(Exception):
        pass

    message = _database_error_message(
        settings,
        OperationalError("connect", {}, ConnectionTimeout("timed out")),
    )

    assert "192.0.2.10:5433/harness_test" in message
    assert "POSTGRES_CONNECT_TIMEOUT_SECONDS" not in message
    assert "连接超时：5 秒" in message
    assert "ConnectionTimeout" in message
    assert "must-not-appear" not in message
