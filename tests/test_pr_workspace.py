"""PR head → 沙箱工作区的安全同步。"""

from __future__ import annotations

from types import SimpleNamespace

from ai_coding_harness.server.pr_workspace import (
    pull_head,
    sync_command,
    sync_pull_workspace,
    target_pull,
)


def _pull(number: int, *, ref: str = "feature/x", sha: str = "a" * 40) -> dict:
    return {"number": number, "head": {"ref": ref, "sha": sha}, "state": "open"}


def test_target_prefers_an_explicit_number() -> None:
    pulls = (_pull(18), _pull(24))

    assert target_pull("修复 PR #24", pulls)["number"] == 24
    assert target_pull("看 https://gitee.com/a/b/pulls/18", pulls)["number"] == 18


def test_target_uses_the_only_open_pull_without_an_explicit_number() -> None:
    assert target_pull("按评论修一下", (_pull(24),))["number"] == 24
    assert target_pull("按评论修一下", (_pull(18), _pull(24))) is None


def test_sync_command_quotes_user_controlled_branch_names() -> None:
    command = sync_command(
        repo_url="https://gitee.com/acme/demo.git",
        repo_dir="/ai_coding/project/demo",
        head_ref="feature/x; touch /tmp/pwned",
        head_sha="a" * 40,
    )

    assert "head_ref='feature/x; touch /tmp/pwned'" in command
    assert "git fetch origin \"$head_ref\"" in command


def test_pull_head_tolerates_missing_payload() -> None:
    assert pull_head({"head": None}) == ("", "")
    assert pull_head(_pull(1)) == ("feature/x", "a" * 40)


async def test_sync_reports_dirty_worktree_without_entering_agent() -> None:
    class _Sandbox:
        async def run(self, command, *, timeout):
            assert "git checkout -B" in command
            return SimpleNamespace(
                exit_code=42,
                stdout="__HARNESS_WORKTREE_DIRTY__\n",
                stderr="",
            )

    result = await sync_pull_workspace(
        SimpleNamespace(sandbox=_Sandbox()),
        repo_url="https://gitee.com/acme/demo.git",
        repo_dir="/ai_coding/project/demo",
        pull=_pull(24),
    )

    assert result.ok is False
    assert "未提交的本地修改" in result.message


async def test_sync_returns_the_synced_pull() -> None:
    class _Sandbox:
        async def run(self, command, *, timeout):
            return SimpleNamespace(
                exit_code=0,
                stdout="__HARNESS_SYNCED__\n",
                stderr="",
            )

    pull = _pull(24)
    result = await sync_pull_workspace(
        SimpleNamespace(sandbox=_Sandbox()),
        repo_url="https://gitee.com/acme/demo.git",
        repo_dir="/ai_coding/project/demo",
        pull=pull,
    )

    assert result.ok is True
    assert result.pull is pull
