"""Portability contract for the local development launchers."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_LAUNCHERS = (
    _ROOT / "start.bat",
    _ROOT / "scripts" / "windows" / "_common.bat",
    _ROOT / "scripts" / "windows" / "start.bat",
    _ROOT / "scripts" / "windows" / "start-backend.bat",
    _ROOT / "scripts" / "windows" / "start-frontend.bat",
)
_LINUX_LAUNCHERS = (
    _ROOT / "scripts" / "linux" / "_common.sh",
    _ROOT / "scripts" / "linux" / "start-backend.sh",
    _ROOT / "scripts" / "linux" / "start-frontend.sh",
)
_STANDALONE_LAUNCHERS = (
    _ROOT / "scripts" / "windows" / "start-standalone.ps1",
    _ROOT / "scripts" / "wsl" / "start-standalone.sh",
)


@pytest.mark.parametrize("path", _LAUNCHERS, ids=lambda path: path.name)
def test_windows_launchers_are_ascii_crlf(path: Path) -> None:
    """Windows cmd reads batch files before chcp can make UTF-8/GBK handling reliable."""
    data = path.read_bytes()

    assert not data.startswith(b"\xef\xbb\xbf")
    assert all(byte < 128 for byte in data)
    assert data.count(b"\n") == data.count(b"\r\n")


def test_windows_launchers_do_not_embed_machine_specific_paths() -> None:
    """A GitHub checkout must not depend on a contributor's drive or Python install."""
    combined = "\n".join(path.read_text(encoding="ascii") for path in _LAUNCHERS)

    assert "D:\\development" not in combined
    assert "C:\\Users\\" not in combined
    assert "chcp 936" not in combined
    assert "HARNESS_PYTHON" not in combined
    assert "HARNESS_NPM" not in combined
    assert "%REPO_ROOT%\\.venv\\Scripts\\python.exe" in combined
    assert "NODE_HOME" in combined


def test_linux_launchers_use_the_same_runtime_contract() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _LINUX_LAUNCHERS)

    assert 'PYTHON="${REPO_ROOT}/.venv/bin/python"' in combined
    assert 'NPM="${NODE_HOME}/bin/npm"' in combined
    assert 'PYTHON="${PYTHON:-python3}"' not in combined
    assert "exec npm " not in combined


def test_standalone_launchers_do_not_add_runtime_managers() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in _STANDALONE_LAUNCHERS
    )

    assert 'PYTHON="${HARNESS_PYTHON:-${VENV}/bin/python}"' not in combined
    assert 'PYTHON="${VENV}/bin/python"' in combined
    assert "-not $env:NODE_HOME" in combined
    assert 'Join-Path $env:NODE_HOME "npm.cmd"' in combined


def test_python_environment_has_one_version_and_lock_source() -> None:
    assert (_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.14"
    assert (_ROOT / "uv.lock").is_file()
    assert not (_ROOT / "requirements.txt").exists()
