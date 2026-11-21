"""Create persistent snapshots and approval rows for dangerous Git commands."""

from __future__ import annotations

import asyncio
import hashlib
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from deepagents.backends.protocol import BackendProtocol

from ai_coding_harness.db import Database, dao
from ai_coding_harness.db.models import GitApproval
from ai_coding_harness.safety.git_policy import GitPolicyDecision

APPROVAL_OPTIONS: list[dict[str, Any]] = [
    {
        "id": "deny",
        "label": "拒绝执行，让 Agent 换一种做法",
        "description": "不执行命令，把拒绝结果返回给主控模型。",
        "tone": "safe",
    },
    {
        "id": "allow",
        "label": "允许本次执行",
        "description": "只允许这一条命令执行一次。",
        "tone": "danger",
    },
    {
        "id": "terminate",
        "label": "终止本轮",
        "description": "不执行命令，结束当前运行并保留已产生内容。",
        "tone": "neutral",
    },
]


@dataclass(frozen=True)
class GitApprovalContext:
    database: Database
    session_id: int
    repo_dir: str
    backend: BackendProtocol


async def _exec(backend: BackendProtocol, command: str) -> tuple[str, int]:
    execute = getattr(backend, "aexecute", None)
    if not callable(execute):
        raise TypeError("当前后端不支持命令执行")
    response = await execute(command, timeout=30)
    output = str(getattr(response, "output", "") or "").strip()
    code = getattr(response, "exit_code", None)
    return output, int(code if code is not None else 0)


async def _snapshot(
    ctx: GitApprovalContext,
    *,
    command: str,
    risk: str,
) -> dict[str, Any]:
    repo = shlex.quote(ctx.repo_dir.rstrip("/"))
    clean_preview = ""
    if risk == "delete_untracked":
        clean_preview, code = await _exec(
            ctx.backend,
            f"git -C {repo} clean -ndx",
        )
        if code != 0:
            raise ValueError("无法生成 git clean dry-run 清单")
    script = f"""set -eu
repo={repo}
cd "$repo"
recovery_dir=.git/harness-recovery
mkdir -p "$recovery_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
git status --porcelain=v2 > "$recovery_dir/$stamp.status"
git diff --binary > "$recovery_dir/$stamp.unstaged.patch"
git diff --cached --binary > "$recovery_dir/$stamp.staged.patch"
git rev-parse HEAD > "$recovery_dir/$stamp.head"
git branch --show-current > "$recovery_dir/$stamp.branch"
git ls-remote --heads origin > "$recovery_dir/$stamp.remote" || true
printf '__HARNESS_GIT_APPROVAL__%s\\n' "$recovery_dir/$stamp"
"""
    output, code = await _exec(ctx.backend, script)
    if code != 0:
        raise ValueError(output[:500] or "无法保存 Git 恢复快照")
    marker = next(
        (
            line.split("__HARNESS_GIT_APPROVAL__", 1)[1].strip()
            for line in output.splitlines()
            if "__HARNESS_GIT_APPROVAL__" in line
        ),
        "",
    )
    if not marker:
        raise ValueError("恢复快照未返回落点")
    branch, code = await _exec(
        ctx.backend, f"git -C {repo} branch --show-current"
    )
    head, head_code = await _exec(ctx.backend, f"git -C {repo} rev-parse HEAD")
    if code != 0 or head_code != 0:
        raise ValueError("无法读取当前分支或 HEAD")
    status, status_code = await _exec(
        ctx.backend, f"git -C {repo} status --porcelain=v2"
    )
    if status_code != 0:
        raise ValueError("无法读取工作区状态")
    return {
        "branch": branch.splitlines()[0].strip() if branch else "",
        "head_sha": head.splitlines()[0].strip() if head else "",
        "status": status.splitlines()[:80],
        "status_truncated": max(0, len(status.splitlines()) - 80),
        "recovery_path": marker,
        "clean_preview": clean_preview.splitlines()[:80],
        "clean_preview_truncated": max(
            0, len(clean_preview.splitlines()) - 80
        ),
        "command": command,
    }


async def create_git_approval(
    ctx: GitApprovalContext,
    *,
    command: str,
    tool_call_id: str,
    thread_id: str,
    decision: GitPolicyDecision,
    ttl: timedelta = timedelta(minutes=10),
) -> GitApproval:
    details = await _snapshot(ctx, command=command, risk=decision.risk)
    details.update(
        {
            "reason": decision.reason,
            "targets": list(decision.targets),
            "preview_commands": list(decision.preview_commands),
            "thread_id": thread_id,
        }
    )
    request_hash = hashlib.sha256(
        "\n".join(
            (
                str(ctx.session_id),
                thread_id,
                tool_call_id,
                command,
                str(details.get("branch") or ""),
                str(details.get("head_sha") or ""),
            )
        ).encode()
    ).hexdigest()
    async with ctx.database.session() as db:
        row = await dao.create_or_get_approval(
            db,
            session_id=ctx.session_id,
            request_hash=request_hash,
            tool_call_id=tool_call_id,
            command=command,
            action=decision.action,
            risk=decision.risk,
            options=APPROVAL_OPTIONS,
            details=details,
            expires_at=datetime.now(UTC) + ttl,
        )
        await db.refresh(row)
        return row


def interrupt_payload(row: GitApproval) -> dict[str, Any]:
    payload = dao.approval_payload(row)
    payload["kind"] = "git_approval"
    return payload


async def wait_for_decision(
    ctx: GitApprovalContext,
    approval_id: str,
) -> dict[str, Any]:
    """Wait while the current model run remains paused on the execute call."""
    while True:
        async with ctx.database.session() as db:
            row = await dao.get_approval(
                db,
                session_id=ctx.session_id,
                approval_id=approval_id,
            )
            if row is None:
                return {"status": "missing", "choice": "deny"}
            if row.status == "pending" and row.expires_at <= datetime.now(UTC):
                row.status = "expired"
                row.resolved_at = datetime.now(UTC)
                await db.flush()
            if row.status != "pending":
                return dao.approval_payload(row)
        await asyncio.sleep(0.5)


__all__ = [
    "APPROVAL_OPTIONS",
    "GitApprovalContext",
    "create_git_approval",
    "interrupt_payload",
    "wait_for_decision",
]
