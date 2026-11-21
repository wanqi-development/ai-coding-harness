"""Graceful restart keeps an auditable copy of uncommitted workspace work."""

from __future__ import annotations

from types import SimpleNamespace

from ai_coding_harness.session.manager import SessionManager


async def test_capture_restart_recovery_writes_snapshot_under_git() -> None:
    seen = ""

    class Sandbox:
        async def run(self, command, *, timeout):
            nonlocal seen
            seen = command
            return SimpleNamespace(
                ok=True,
                stderr="",
                text="__HARNESS_RECOVERY_SAVED__ .git/harness-recovery/stamp",
            )

    manager = object.__new__(SessionManager)
    manager._settings = SimpleNamespace(sandbox_project_dir="/ai_coding/project")
    manager._runtimes = {
        "session-1": SimpleNamespace(sandbox=Sandbox()),
    }
    session = SimpleNamespace(
        public_id="session-1",
        repo=SimpleNamespace(repo="demo"),
    )

    result = await manager.capture_restart_recovery(session)

    assert result is not None and "RECOVERY_SAVED" in result
    assert "recovery_dir=.git/harness-recovery" in seen
    assert "git status --porcelain=v1" in seen
    assert "git diff --binary" in seen
    assert "git diff --cached --binary" in seen
