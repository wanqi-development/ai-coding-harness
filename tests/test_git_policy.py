"""Git command policy: allow, interrupt approval, and hard denial."""

from __future__ import annotations

import pytest

from ai_coding_harness.safety.git_policy import evaluate_git_command

ROOT = "/ai_coding/project/repo"


def level(command: str) -> str:
    return evaluate_git_command(command, repo_root=ROOT).level


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --oneline -5",
        "git fetch origin",
        "git add src/app.py",
        "git commit -m change",
        "git push origin feature/x",
        "git branch -d merged",
        "git clean -n",
        "git checkout feature/x",
    ],
)
def test_normal_development_commands_are_allowed(command: str) -> None:
    assert level(command) == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "git reset --hard",
        "git reset --keep HEAD~1",
        "git clean -fd",
        "git clean -fdx",
        "git checkout -- src/app.py",
        "git checkout .",
        "git checkout -f feature/x",
        "git restore src/app.py",
        "git branch -D old",
        "git tag -d v1",
        "git stash clear",
        "git commit --amend",
        "git rebase -i HEAD~3",
        "git push --force-with-lease origin feature/x",
        "git push origin --delete feature/x",
    ],
)
def test_destructive_but_legitimate_commands_require_approval(
    command: str,
) -> None:
    assert level(command) == "approval"


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin feature/x",
        "git push -f origin feature/x",
        "git push --mirror",
        "git push --force origin main",
        "git push origin --delete main",
        "git filter-branch --all",
        "git filter-repo --force",
        "git update-ref -d refs/heads/x",
        "git reflog expire --expire=now --all",
        "git gc --prune=now",
        "git commit --no-verify -m x",
        "git -C /tmp/other status",
    ],
)
def test_unrecoverable_or_external_commands_are_denied(command: str) -> None:
    assert level(command) == "deny"


def test_global_git_options_do_not_bypass_force_push_policy() -> None:
    decision = evaluate_git_command(
        "git -C repo -c core.quotePath=false push --force origin feature/x",
        repo_root=ROOT,
    )

    assert decision.level == "deny"
    assert decision.risk == "force_push"


def test_nested_shell_payload_is_checked() -> None:
    decision = evaluate_git_command(
        'bash -c "git reset --hard"',
        repo_root=ROOT,
    )

    assert decision.level == "approval"
    assert decision.action == "reset"


def test_cd_outside_repository_blocks_following_git_command() -> None:
    decision = evaluate_git_command(
        "cd /tmp/other && git reset --hard",
        repo_root=ROOT,
    )

    assert decision.level == "deny"
    assert decision.risk == "external_repository"
