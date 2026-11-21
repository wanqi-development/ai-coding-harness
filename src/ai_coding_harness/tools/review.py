"""Independent, code-versioned reviews.

Reviews are advisory. They are persisted and shown to the user, but they do
not decide whether a PR may be created; task conflicts are handled by the
durable work-task tools.
"""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from typing import Any

from deepagents.backends.protocol import BackendProtocol
from langchain_core.tools import BaseTool, tool

from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.db import Database, dao
from ai_coding_harness.tools.context import get_configurable

SAVE_REVIEW_TOOL = "save_review"


@dataclass(frozen=True)
class ReviewContext:
    database: Database
    session_id: int
    platform: str
    owner: str
    repo: str
    repo_dir: str
    backend: BackendProtocol
    plan_id: int | None = None


@dataclass(frozen=True)
class CodeVersion:
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    code_revision: str
    dirty: bool


async def _exec(backend: BackendProtocol, command: str) -> tuple[str, int]:
    execute = getattr(backend, "aexecute", None)
    if not callable(execute):
        raise TypeError("当前后端不支持代码版本检查")
    response = await execute(command, timeout=30)
    output = str(getattr(response, "output", "") or "").strip()
    code = getattr(response, "exit_code", None)
    return output, int(code if code is not None else 0)


def _first_line(text: str) -> str:
    return text.splitlines()[0].strip() if text.strip() else ""


async def current_code_version(
    backend: BackendProtocol,
    repo_dir: str,
    *,
    base_ref: str = "",
    head_ref: str = "",
) -> CodeVersion:
    """Compute a revision that survives the local-edit → commit transition.

    The canonical fingerprint is ``base_sha + diff(base_sha..worktree)`` plus
    untracked file hashes. A local review therefore remains valid after those
    same edits are committed, but becomes stale as soon as content changes.
    """
    root = shlex.quote(repo_dir.rstrip("/"))
    requested_base = base_ref.strip()
    if requested_base:
        base_cmd = (
            f"git -C {root} rev-parse --verify {shlex.quote('origin/' + requested_base)} "
            f"2>/dev/null || git -C {root} rev-parse --verify {shlex.quote(requested_base)}"
        )
        base_out, code = await _exec(backend, base_cmd)
        if code != 0 or not base_out:
            base_cmd = f"git -C {root} rev-parse --verify HEAD"
            base_out, code = await _exec(backend, base_cmd)
    else:
        base_cmd = (
            f"git -C {root} symbolic-ref --quiet --short refs/remotes/origin/HEAD "
            "2>/dev/null | sed 's#^origin/##'"
        )
        base_name, _ = await _exec(backend, base_cmd)
        requested_base = _first_line(base_name) or "master"
        base_cmd = (
            f"git -C {root} rev-parse --verify {shlex.quote('origin/' + requested_base)} "
            f"2>/dev/null || git -C {root} rev-parse --verify {shlex.quote(requested_base)}"
        )
        base_out, code = await _exec(backend, base_cmd)
        if code != 0 or not base_out:
            base_cmd = f"git -C {root} rev-parse --verify HEAD"
            base_out, code = await _exec(backend, base_cmd)
    if code != 0 or not base_out:
        raise ValueError("无法解析目标分支 SHA")
    base_sha = _first_line(base_out)

    head_out, head_code = await _exec(backend, f"git -C {root} rev-parse HEAD")
    if head_code != 0 or not head_out:
        raise ValueError("无法解析当前 HEAD SHA")
    head_sha = _first_line(head_out)

    diff_cmd = (
        f"git -C {root} diff --binary {shlex.quote(base_sha)} -- . | sha256sum"
    )
    diff_out, diff_code = await _exec(backend, diff_cmd)
    if diff_code != 0:
        raise ValueError("无法计算工作区差异指纹")
    diff_hash = _first_line(diff_out).split()[0] if diff_out else ""

    untracked_cmd = (
        f"cd {root} && "
        "git ls-files --others --exclude-standard -z | sort -z | "
        "xargs -0 -r sha256sum | sort | sha256sum"
    )
    untracked_out, untracked_code = await _exec(backend, untracked_cmd)
    if untracked_code != 0:
        raise ValueError("无法计算未跟踪文件指纹")
    untracked_hash = (
        _first_line(untracked_out).split()[0] if untracked_out else ""
    )

    status_out, status_code = await _exec(
        backend, f"git -C {root} status --porcelain"
    )
    if status_code != 0:
        raise ValueError("无法读取 Git 工作区状态")
    revision = hashlib.sha256(
        f"{base_sha}\n{diff_hash}\n{untracked_hash}".encode()
    ).hexdigest()
    current_head_ref, _ = await _exec(
        backend, f"git -C {root} branch --show-current"
    )
    return CodeVersion(
        base_ref=requested_base,
        base_sha=base_sha,
        head_ref=head_ref.strip() or _first_line(current_head_ref),
        head_sha=head_sha,
        code_revision=revision,
        dirty=bool(status_out.strip()),
    )


def _review_summary(
    findings: list[dict[str, Any]],
    *,
    stage: str = "local",
) -> tuple[str, dict[str, int], str]:
    counts = {level: 0 for level in ("P0", "P1", "P2", "P3")}
    for finding in findings:
        counts[finding["severity"]] += 1
    summary = (
        f"P0={counts['P0']} / P1={counts['P1']} / "
        f"P2={counts['P2']} / P3={counts['P3']}"
    )
    if stage == "pr":
        reminder = "PR 审查完成。下一步请检查结论并合并 PR；合并后本阶段任务完成。"
    else:
        reminder = (
            "本地审查完成。下一步由主控按当前任务目标决定继续修改或交付 PR；"
            "创建并合并 PR 后本阶段任务完成。"
        )
    return summary, counts, reminder


def build_review_tools(ctx: ReviewContext) -> list[BaseTool]:
    @tool
    async def save_review(
        report_markdown: str,
        findings: list[dict],
        base_ref: str = "",
        summary: str = "",
        stage: str = "local",
        pull_request_id: int | None = None,
        pull_request_number: int | None = None,
        head_ref: str = "",
        provider: str = "",
        model_name: str = "",
    ) -> str:
        """Persist a code review bound to the exact current code revision.

        Use this after reviewing a local candidate or a final PR diff. Do not
        report a review as complete unless the report and findings are ready.
        """
        if stage not in {"local", "pr"}:
            return "审查未保存：stage 必须是 local 或 pr。"
        if stage == "pr" and pull_request_id is None:
            if pull_request_number is None:
                return (
                    "审查未保存：PR 阶段审查必须提供 pull_request_id "
                    "或 pull_request_number。"
                )
            async with ctx.database.session() as db:
                pull = await dao.get_pull_request(
                    db,
                    platform=ctx.platform,
                    owner=ctx.owner,
                    repo=ctx.repo,
                    number=int(pull_request_number),
                )
            if pull is None:
                return (
                    f"审查未保存：本地 PR 表没有 #{pull_request_number}，"
                    "请先刷新 PR 状态。"
                )
            pull_request_id = pull.id
        if not report_markdown.strip():
            return "审查未保存：report_markdown 不能为空。"
        state = get_configurable("turn_state")
        plan_id = state.plan_id if isinstance(state, TurnState) else ctx.plan_id
        try:
            version = await current_code_version(
                ctx.backend,
                ctx.repo_dir,
                base_ref=base_ref,
                head_ref=head_ref,
            )
            async with ctx.database.session() as db:
                review, items = await dao.store_review(
                    db,
                    session_id=ctx.session_id,
                    plan_id=plan_id,
                    pull_request_id=pull_request_id,
                    stage=stage,
                    base_ref=version.base_ref,
                    base_sha=version.base_sha,
                    head_ref=version.head_ref,
                    head_sha=version.head_sha,
                    code_revision=version.code_revision,
                    summary=summary.strip(),
                    report_markdown=report_markdown,
                    findings=findings,
                    reviewer_agent="reviewer",
                    provider=provider,
                    model_name=model_name,
                )
                payload = dao.review_payload(review, items)
        except (TypeError, ValueError) as exc:
            return f"审查未保存：{exc}"
        if isinstance(state, TurnState):
            state.review_id = review.id
            state.review_payload = payload
        text, _, reminder = _review_summary(
            [
                {"severity": item.severity, "status": item.status}
                for item in items
            ],
            stage=stage,
        )
        return (
            f"审查已保存：review_id={review.id}，{text}，"
            f"代码版本 {version.code_revision[:12]}。{reminder}"
        )

    @tool
    async def latestReview() -> str:
        """Read the latest persisted review summary for this session."""
        async with ctx.database.session() as db:
            latest = await dao.latest_review(db, session_id=ctx.session_id)
        if latest is None:
            return "本会话还没有审查记录。"
        review, findings = latest
        summary, _, reminder = _review_summary(
            [
                {"severity": item.severity, "status": item.status}
                for item in findings
            ],
            stage=review.stage,
        )
        return (
            f"review_id={review.id} stage={review.stage} status={review.status} "
            f"{summary}；{reminder}\n{review.report_markdown}"
        )

    return [save_review, latestReview]


__all__ = [
    "CodeVersion",
    "ReviewContext",
    "build_review_tools",
    "current_code_version",
]
