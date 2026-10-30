"""WSL local sandbox path mapping and bubblewrap command construction."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai_coding_harness.config import Settings
from ai_coding_harness.sandbox.factory import build_sandbox_provider
from ai_coding_harness.sandbox.wsl import WslSandboxClient, WslSandboxProvider


def _settings(tmp_path: Path, *, network: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        deployment_mode="standalone",
        sqlite_path=str(tmp_path / "harness.sqlite3"),
        sandbox_provider="wsl",
        wsl_root=str(tmp_path / "sessions"),
        wsl_allow_network=network,
    )


def test_factory_selects_wsl_provider(tmp_path: Path) -> None:
    provider = build_sandbox_provider(_settings(tmp_path))

    assert isinstance(provider, WslSandboxProvider)


async def test_wsl_client_maps_files_without_leaving_session_root(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    root = tmp_path / "sessions" / "s1"
    client = WslSandboxClient(settings=settings, session_id="s1", root=root)
    root.mkdir(parents=True)

    await client.write_file("/ai_coding/project/demo.py", "print(1)\n")
    await client.write_file("/ai_coding/project/asset.bin", b"\x00\x01\x02")
    await client.write_file("/usr/local/sbin/git-askpass", "#!/bin/sh\n")

    assert await client.read_file("/ai_coding/project/demo.py") == "print(1)\n"
    assert (root / "ai_coding" / "project" / "demo.py").is_file()
    assert (root / "ai_coding" / "project" / "asset.bin").read_bytes() == b"\x00\x01\x02"
    assert (root / "sbin" / "git-askpass").is_file()
    with pytest.raises(ValueError, match="escapes"):
        await client.write_file("/ai_coding/../escape", "bad")
    with pytest.raises(ValueError, match="unsupported"):
        await client.write_file("/etc/passwd", "bad")


def test_bwrap_command_mounts_session_rw_and_assets_ro(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    root = tmp_path / "sessions" / "s1"
    client = WslSandboxClient(settings=settings, session_id="s1", root=root)

    args = client._bwrap_command(command="python demo.py", cwd=None)
    command = " ".join(args)

    assert "--unshare-pid" in args
    assert "--unshare-net" not in args
    assert f"--bind {root / 'ai_coding' / 'project'} /ai_coding/project" in command
    assert f"--ro-bind {root / 'ai_coding' / 'policies'} /ai_coding/policies" in command
    assert "/ai_coding/project" in args
    assert args[-3:] == ["/bin/bash", "-lc", "python demo.py"]


def test_bwrap_can_disable_network(tmp_path: Path) -> None:
    client = WslSandboxClient(
        settings=_settings(tmp_path, network=False),
        session_id="s1",
        root=tmp_path / "sessions" / "s1",
    )

    assert "--unshare-net" in client._bwrap_command(command="true", cwd=None)


def test_script_assets_are_normalized_to_lf(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "git-askpass").write_bytes(b"#!/bin/sh\r\necho ok\r\n")
    (assets / "setup.sh").write_bytes(b"#!/bin/sh\r\nexit 0\r\n")
    settings = _settings(tmp_path).model_copy(
        update={
            "sandbox_assets_dir": str(assets),
            "sandbox_setup_command": "/usr/local/sbin/setup.sh",
        }
    )
    provider = WslSandboxProvider(settings)
    root = tmp_path / "sessions" / "s1"
    client = WslSandboxClient(settings=settings, session_id="s1", root=root)
    provider._install_scripts(client)

    askpass = root / "sbin" / "git-askpass"
    setup = root / "sbin" / "setup.sh"
    assert b"\r\n" not in askpass.read_bytes()
    assert b"\r\n" not in setup.read_bytes()
    if os.name == "posix":
        assert askpass.stat().st_mode & 0o111
        assert setup.stat().st_mode & 0o111

