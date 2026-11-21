"""Portability contract for the local development launchers."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_LAUNCHERS = (
    _ROOT / "start.bat",
    _ROOT / "stop.bat",
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
    _ROOT / "scripts" / "windows" / "_host-runtime.ps1",
    _ROOT / "scripts" / "windows" / "start-standalone.ps1",
    _ROOT / "scripts" / "windows" / "start-wsl-standalone.ps1",
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
    assert "NODE_HOME" not in combined
    assert "node --version" in combined
    assert "npm.cmd --version" in combined


def test_linux_launchers_use_the_same_runtime_contract() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _LINUX_LAUNCHERS)

    assert 'PYTHON="${REPO_ROOT}/.venv/bin/python"' in combined
    assert 'PYTHON="${PYTHON:-python3}"' not in combined
    assert "NODE_HOME" not in combined
    assert "node --version" in combined
    assert "npm --version" in combined
    assert "exec npm --prefix frontend run dev" in combined


def test_standalone_launchers_do_not_add_runtime_managers() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in _STANDALONE_LAUNCHERS
    )

    assert 'PYTHON="${HARNESS_PYTHON:-${VENV}/bin/python}"' not in combined
    assert 'PYTHON="${VENV}/bin/python"' in combined
    assert "NODE_HOME" not in combined
    assert "& node --version" in combined
    assert "Get-Command npm.cmd" in combined


def test_standalone_persistent_paths_are_resolved_on_windows() -> None:
    helper = (_ROOT / "scripts" / "windows" / "_host-runtime.ps1").read_text(
        encoding="utf-8"
    )
    wsl_launcher = (
        _ROOT / "scripts" / "windows" / "start-wsl-standalone.ps1"
    ).read_text(encoding="utf-8")
    shell_launcher = (_ROOT / "scripts" / "wsl" / "start-standalone.sh").read_text(
        encoding="utf-8"
    )

    assert "HARNESS_HOST_HOME" in helper
    assert "HARNESS_HOST_DATA_DIR" in helper
    assert "HARNESS_HOST_LOG_DIR" in helper
    assert "HARNESS_CREDENTIALS_DIR" in wsl_launcher
    assert "HARNESS_BACKUP_DIR" in wsl_launcher
    assert "backend.stdout.log" in wsl_launcher
    assert "frontend.stdout.log" in wsl_launcher
    assert '$env:TEMP' not in wsl_launcher
    assert 'DATA_DIR="${HARNESS_DATA_DIR:-}"' in shell_launcher
    assert 'LOG_DIR="${HARNESS_LOG_DIR:-}"' in shell_launcher
    assert 'SQLITE_BACKUP_DIR="${BACKUP_DIR}"' in shell_launcher
    assert "prepare_host_dir" in shell_launcher
    assert 'install -d -m 700 "${DATA_DIR}"' not in shell_launcher
    assert "typesafe_sdk" in shell_launcher
    assert "pip install -e" in shell_launcher
    bootstrap = (_ROOT / "scripts" / "wsl" / "bootstrap.sh").read_text(
        encoding="utf-8"
    )
    assert "prepare_host_dir" in bootstrap
    assert 'install -d -m 700 -o "${HARNESS_USER}" -g "${HARNESS_USER}" \\' in bootstrap


def test_python_environment_has_one_version_and_lock_source() -> None:
    assert (_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.14"
    assert (_ROOT / "uv.lock").is_file()
    assert not (_ROOT / "requirements.txt").exists()


def test_windows_start_uses_a_process_supervisor_with_logs() -> None:
    wrapper = (_ROOT / "start.bat").read_text(encoding="ascii")
    supervisor = (_ROOT / "scripts" / "windows" / "start-dev.ps1").read_text(
        encoding="utf-8"
    )
    stopper = (_ROOT / "scripts" / "windows" / "stop-dev.ps1").read_text(
        encoding="utf-8"
    )

    assert "start-dev.ps1" in wrapper
    assert "if not defined HARNESS_NO_PAUSE" in wrapper
    assert '$logDir = Join-Path $repoRoot "logs"' in supervisor
    assert '$launcherLog = Join-Path $logDir "launcher.log"' in supervisor
    assert '[IO.File]::WriteAllText($launcherLog, "", $launcherEncoding)' in supervisor
    assert "function Initialize-LogDirectory" in supervisor
    assert "HARNESS_LOG_DIR" in supervisor
    assert "logs.archive-" not in supervisor
    assert "Move-Item -LiteralPath $logDir" not in supervisor
    assert "Write-CapturedOutput" in supervisor
    assert '>> `"$launcherLog`" 2>&1' not in supervisor
    assert "launcher-state.json" in supervisor
    assert "service_log_dir" in supervisor
    assert "shutdown_file" in supervisor
    assert "HARNESS_SHUTDOWN_FILE" in supervisor
    assert "backend.log" in supervisor and "frontend.log" in supervisor
    assert '$serviceLogDir = Join-Path $logDir "services"' in supervisor
    assert "$Process.HasExited" in supervisor
    assert "Wait-HttpReady" in supervisor
    assert 'Uri "http://localhost:$frontendPort/"' in supervisor
    assert 'Uri "http://127.0.0.1:$frontendPort/"' not in supervisor
    assert 'Test-TcpPort -Port $frontendPort -HostName "localhost"' in supervisor
    assert "exit 1" in supervisor
    assert "backend_started_at" in stopper
    assert "shutdown_file" in stopper
    assert "stopped gracefully" in stopper
    assert "/T /F" in stopper
    assert "Could not open the browser automatically" in supervisor
    assert "Skipping unsafe service log path" in stopper
    assert "Preserving service logs" in stopper
    assert '"ai-coding-harness-"' in stopper
    assert "StartsWith(" in stopper
