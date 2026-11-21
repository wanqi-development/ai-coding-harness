"""Controlled QA repository clone/update tool."""

from __future__ import annotations

from types import SimpleNamespace

from ai_coding_harness.tools.repo_sync import RepoSyncContext, build_repo_sync_tools, sync_script


class _Backend:
    def __init__(self, output: str, exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code
        self.commands: list[str] = []

    async def aexecute(self, command: str, *, timeout: int | None = None):
        self.commands.append(command)
        return SimpleNamespace(output=self.output, exit_code=self.exit_code)


def _tool(backend: _Backend):
    return build_repo_sync_tools(
        RepoSyncContext(
            backend=backend,  # type: ignore[arg-type]
            repo_url="https://gitee.com/acme/demo.git",
            repo_dir="/ai_coding/project/demo",
        )
    )[0]


def test_sync_script_clones_fetches_and_only_fast_forwards() -> None:
    command = sync_script(
        repo_url="https://gitee.com/acme/demo.git",
        repo_dir="/ai_coding/project/demo",
    )

    assert 'git clone "$repo_url" "$repo_dir"' in command
    assert "git fetch --prune origin" in command
    assert 'git merge --ff-only "origin/$branch"' in command
    assert "__HARNESS_WORKTREE_DIRTY__" in command
    assert "__HARNESS_LOCAL_COMMITS__" in command
    assert "git reset --hard" not in command


async def test_sync_tool_returns_commit_and_branch_status() -> None:
    backend = _Backend("__HARNESS_SYNCED__\nabc1234\n## master...origin/master")

    result = await _tool(backend).ainvoke({})

    assert result.splitlines() == ["abc1234", "## master...origin/master"]
    assert "HARNESS_WORKTREE_DIRTY" in backend.commands[0] or "git status" in backend.commands[0]


async def test_sync_tool_refuses_dirty_or_diverged_worktree() -> None:
    dirty = _Backend("__HARNESS_WORKTREE_DIRTY__", exit_code=42)
    diverged = _Backend("__HARNESS_LOCAL_COMMITS__", exit_code=44)

    assert "未提交修改" in await _tool(dirty).ainvoke({})
    assert "未推送提交" in await _tool(diverged).ainvoke({})


async def test_sync_tool_rejects_unsafe_ref_without_running_a_command() -> None:
    backend = _Backend("")
    result = await _tool(backend).ainvoke({"ref": "../main"})

    assert "合法的分支名" in result
    assert backend.commands == []
