"""Generated first-admin credentials for the standalone profile."""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path

import pytest

from ai_coding_harness import manage
from ai_coding_harness.auth.security import verify_password
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database, dao


def _standalone(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        deployment_mode="standalone",
        sqlite_path=str(tmp_path / "harness.sqlite3"),
        sandbox_provider="wsl",
        log_dir=str(tmp_path / "logs"),
    )


async def test_standalone_admin_is_generated_and_written_privately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _standalone(tmp_path)
    credential_file = tmp_path / "logs" / "initial-admin.txt"
    monkeypatch.setattr(manage, "get_settings", lambda: settings)
    args = Namespace(username="admin", credentials_file=str(credential_file))

    assert await manage._ensure_standalone_admin(args) == 0
    output = capsys.readouterr().out
    password = output.split("密码：", 1)[1].splitlines()[0]

    assert "Standalone 初始管理员已创建" in output
    assert f"凭据文件：{credential_file}" in output
    assert "首次登录后必须修改密码" in output
    assert len(password) >= 24

    stored = credential_file.read_text(encoding="utf-8")
    assert "用户名：admin" in stored
    assert f"密码：{password}" in stored
    if os.name == "posix":
        assert credential_file.stat().st_mode & 0o777 == 0o600

    database = Database(settings)
    try:
        async with database.session() as db:
            login = await dao.get_local_login(db, username="admin")
            assert login is not None
            user, credential = login
            assert user.role == "admin"
            assert credential.must_change_password is True
            assert verify_password(password, credential.password_hash)
    finally:
        await database.dispose()

    original = stored
    assert await manage._ensure_standalone_admin(args) == 0
    second_output = capsys.readouterr().out
    assert "管理员已存在，未生成新密码" in second_output
    assert credential_file.read_text(encoding="utf-8") == original


async def test_multi_profile_refuses_generated_admin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(
        _env_file=None,
        deployment_mode="multi",
        redis_url="redis://127.0.0.1:6379/0",
        redis_required=True,
        sandbox_provider="opensandbox",
    )
    credential_file = tmp_path / "initial-admin.txt"
    monkeypatch.setattr(manage, "get_settings", lambda: settings)

    result = await manage._ensure_standalone_admin(
        Namespace(username="admin", credentials_file=str(credential_file))
    )

    assert result == 2
    assert "multi 模式不自动创建管理员" in capsys.readouterr().err
    assert not credential_file.exists()
