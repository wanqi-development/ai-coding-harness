"""Controlled repository clone/update for read-only QA turns."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from deepagents.backends.protocol import BackendProtocol
from langchain_core.tools import BaseTool, tool

_SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]+$")


@dataclass(frozen=True)
class RepoSyncContext:
    backend: BackendProtocol
    repo_url: str
    repo_dir: str


def sync_script(*, repo_url: str, repo_dir: str, ref: str = "") -> str:
    """Clone or fast-forward the checkout without discarding local work."""
    return f"""set -eu
repo_url={shlex.quote(repo_url)}
repo_dir={shlex.quote(repo_dir)}
requested_ref={shlex.quote(ref)}
if [ ! -d "$repo_dir/.git" ]; then
  if [ -e "$repo_dir" ] && [ -n "$(ls -A "$repo_dir" 2>/dev/null)" ]; then
    echo "__HARNESS_REPO_NOT_GIT__"
    exit 41
  fi
  mkdir -p "$(dirname "$repo_dir")"
  git clone "$repo_url" "$repo_dir"
fi
cd "$repo_dir"
git fetch --prune origin
if [ -n "$(git status --porcelain)" ]; then
  echo "__HARNESS_WORKTREE_DIRTY__"
  exit 42
fi
if [ -n "$requested_ref" ]; then
  git check-ref-format --branch "$requested_ref" >/dev/null
  branch="$requested_ref"
else
  git remote set-head origin -a >/dev/null 2>&1 || true
  remote_head="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [ -z "$remote_head" ]; then
    echo "__HARNESS_DEFAULT_BRANCH_UNKNOWN__"
    exit 43
  fi
  branch="${{remote_head#origin/}}"
fi
git fetch origin "$branch"
if git show-ref --verify --quiet "refs/heads/$branch"; then
  if [ "$(git rev-list --count "origin/$branch..$branch")" -ne 0 ]; then
    echo "__HARNESS_LOCAL_COMMITS__"
    exit 44
  fi
  git switch "$branch" >/dev/null
else
  git switch --create "$branch" --track "origin/$branch" >/dev/null
fi
git merge --ff-only "origin/$branch" >/dev/null
printf '__HARNESS_SYNCED__\\n'
git rev-parse --short HEAD
git status --short --branch
"""


def build_repo_sync_tools(ctx: RepoSyncContext) -> list[BaseTool]:
    @tool
    async def sync_repo_code(ref: str = "") -> str:
        """拉取或快进更新当前仓库代码，供 QA 分析和运行只读命令。

        - 项目目录为空/不存在时自动 clone；
        - 已有仓库时 fetch 并仅允许 fast-forward；
        - 有本地未提交修改或本地领先提交时拒绝更新，不覆盖工作区；
        - `ref` 留空使用远端默认分支，也可传明确分支名。
        """
        clean_ref = ref.strip()
        if clean_ref and (
            not _SAFE_REF.fullmatch(clean_ref)
            or clean_ref.startswith("-")
            or ".." in clean_ref
        ):
            return "ref 不是合法的分支名。"
        response = await ctx.backend.aexecute(
            sync_script(
                repo_url=ctx.repo_url,
                repo_dir=ctx.repo_dir,
                ref=clean_ref,
            ),
            timeout=320,
        )
        output = str(getattr(response, "output", "") or "").strip()
        if getattr(response, "exit_code", 1) == 0 and "__HARNESS_SYNCED__" in output:
            return output.replace("__HARNESS_SYNCED__\n", "").strip()
        if "__HARNESS_WORKTREE_DIRTY__" in output:
            return "仓库有未提交修改，已拒绝更新，避免覆盖工作区。"
        if "__HARNESS_LOCAL_COMMITS__" in output:
            return "本地分支包含未推送提交，已拒绝强制更新。"
        if "__HARNESS_REPO_NOT_GIT__" in output:
            return f"目标目录不是 Git 仓库：{ctx.repo_dir}"
        if "__HARNESS_DEFAULT_BRANCH_UNKNOWN__" in output:
            return "无法识别远端默认分支；请传 ref。"
        return f"仓库同步失败：{output or '未知错误'}"

    return [sync_repo_code]


__all__ = ["RepoSyncContext", "build_repo_sync_tools", "sync_script"]
