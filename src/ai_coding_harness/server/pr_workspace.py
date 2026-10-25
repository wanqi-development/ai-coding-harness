"""rework 前的 PR head 安全同步。"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

_EXPLICIT_PR = re.compile(
    r"(?:#|(?:^|\s)pr\s*#?|/(?:pulls?|pull)/)(\d+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PullSyncResult:
    ok: bool
    message: str = ""
    pull: dict[str, Any] | None = None


def target_pull(
    message: str, pulls: tuple[dict[str, Any], ...]
) -> dict[str, Any] | None:
    """有明确编号就按编号选；否则只有一个 open PR 时才自动选它。"""
    match = _EXPLICIT_PR.search(message)
    if match is not None:
        wanted = int(match.group(1))
        return next(
            (pull for pull in pulls if int(str(pull.get("number") or 0)) == wanted),
            None,
        )
    if len(pulls) == 1:
        return pulls[0]
    return None


def pull_head(pull: dict[str, Any]) -> tuple[str, str]:
    head = pull.get("head")
    if not isinstance(head, dict):
        return "", ""
    return str(head.get("ref") or "").strip(), str(head.get("sha") or "").strip()


def pull_refs(pull: dict[str, Any]) -> dict[str, str] | None:
    """取 PR 的 base / head diff 参照；分支名优先，缺失时回落到 SHA。"""
    base = pull.get("base")
    head = pull.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        return None
    base_ref = str(base.get("ref") or base.get("sha") or "").strip()
    head_ref = str(head.get("ref") or head.get("sha") or "").strip()
    return {"base": base_ref, "head": head_ref} if base_ref and head_ref else None


def sync_command(*, repo_url: str, repo_dir: str, head_ref: str, head_sha: str) -> str:
    """生成只允许快进的安全同步脚本；脏工作区或分叉时主动失败。"""
    return f"""set -eu
repo_url={shlex.quote(repo_url)}
repo_dir={shlex.quote(repo_dir)}
head_ref={shlex.quote(head_ref)}
head_sha={shlex.quote(head_sha)}
if [ ! -d "$repo_dir/.git" ]; then
  if [ -e "$repo_dir" ]; then
    echo "__HARNESS_REPO_NOT_GIT__"
    exit 41
  fi
  git clone "$repo_url" "$repo_dir"
fi
cd "$repo_dir"
if [ -n "$(git status --porcelain)" ]; then
  echo "__HARNESS_WORKTREE_DIRTY__"
  exit 42
fi
git fetch origin "$head_ref"
remote_sha=$(git rev-parse FETCH_HEAD)
if [ "$remote_sha" != "$head_sha" ]; then
  echo "__HARNESS_HEAD_MOVED__"
  exit 43
fi
if git show-ref --verify --quiet "refs/heads/$head_ref"; then
  if ! git merge-base --is-ancestor "refs/heads/$head_ref" "$head_sha"; then
    echo "__HARNESS_DIVERGED__"
    exit 44
  fi
fi
git checkout -B "$head_ref" "$head_sha"
if [ "$(git rev-parse HEAD)" != "$head_sha" ]; then
  echo "__HARNESS_HEAD_MISMATCH__"
  exit 45
fi
echo "__HARNESS_SYNCED__"
"""


def _failure_message(output: str, number: int) -> str:
    if "__HARNESS_REPO_NOT_GIT__" in output:
        reason = "项目目录存在但不是 git 仓库"
    elif "__HARNESS_WORKTREE_DIRTY__" in output:
        reason = "工作区存在未提交的本地修改"
    elif "__HARNESS_DIVERGED__" in output:
        reason = "本地分支与 PR 远端已经分叉"
    elif "__HARNESS_HEAD_MOVED__" in output:
        reason = "同步期间 PR head 又发生了变化"
    elif "__HARNESS_HEAD_MISMATCH__" in output:
        reason = "同步后的本地 HEAD 与 PR head 不一致"
    else:
        reason = "git fetch / checkout 失败"
    return (
        f"PR #{number} 的最新提交无法安全同步到工作区：{reason}。"
        "本轮未进入 Agent，请先检查工作区或 PR 分支后重试。"
    )


async def sync_pull_workspace(
    runtime: Any,
    *,
    repo_url: str,
    repo_dir: str,
    pull: dict[str, Any],
) -> PullSyncResult:
    number = int(str(pull.get("number") or 0))
    head_ref, head_sha = pull_head(pull)
    if number <= 0 or not head_ref or not head_sha:
        return PullSyncResult(
            False,
            "PR 快照缺少编号、head 分支或 head SHA，无法安全同步工作区。",
            pull,
        )
    command = sync_command(
        repo_url=repo_url,
        repo_dir=repo_dir,
        head_ref=head_ref,
        head_sha=head_sha,
    )
    try:
        result = await runtime.sandbox.run(
            command,
            timeout=timedelta(seconds=320),
        )
    except Exception as exc:  # noqa: BLE001
        return PullSyncResult(
            False,
            f"PR #{number} 同步工作区失败：{type(exc).__name__}。本轮未进入 Agent。",
            pull,
        )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.exit_code != 0 or "__HARNESS_SYNCED__" not in output:
        return PullSyncResult(False, _failure_message(output, number), pull)
    return PullSyncResult(True, pull=pull)
