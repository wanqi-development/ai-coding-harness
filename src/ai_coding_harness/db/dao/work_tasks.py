"""Work tasks and immutable coder submissions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import PullRequest, TaskSubmission, WorkTask

ACTIVE_TASK_STATUSES = frozenset({"active", "pr_open"})


def work_task_payload(
    task: WorkTask,
    submissions: list[TaskSubmission],
) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "session_id": task.session_id,
        "owner": task.owner,
        "repo": task.repo,
        "title": task.title,
        "requirement_summary": task.requirement_summary,
        "current_summary": task.current_summary,
        "status": task.status,
        "branch": task.branch,
        "base_ref": task.base_ref,
        "latest_commit_sha": task.latest_commit_sha,
        "latest_head_sha": task.latest_head_sha,
        "pull_request_id": task.pull_request_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "submissions": [
            {
                "submission_id": item.id,
                "commit_sha": item.commit_sha,
                "head_sha": item.head_sha,
                "branch": item.branch,
                "base_ref": item.base_ref,
                "summary": item.summary,
                "changed_files": item.changed_files or [],
                "verification": item.verification or [],
                "created_at": item.created_at.isoformat(),
            }
            for item in submissions
        ],
    }


async def submissions_for(
    db: AsyncSession,
    task_id: int,
) -> list[TaskSubmission]:
    rows = await db.scalars(
        select(TaskSubmission)
        .where(TaskSubmission.task_id == task_id)
        .order_by(TaskSubmission.id)
    )
    return list(rows)


async def get_work_task(
    db: AsyncSession,
    *,
    session_id: int,
    task_id: int,
) -> tuple[WorkTask, list[TaskSubmission]] | None:
    task = await db.scalar(
        select(WorkTask).where(
            WorkTask.id == task_id,
            WorkTask.session_id == session_id,
        )
    )
    if task is None:
        return None
    return task, await submissions_for(db, task.id)


async def active_work_task(
    db: AsyncSession,
    *,
    session_id: int,
) -> tuple[WorkTask, list[TaskSubmission]] | None:
    """Return the newest live task, refreshing stale PR-backed states."""
    tasks = list(
        await db.scalars(
            select(WorkTask)
            .where(
                WorkTask.session_id == session_id,
                WorkTask.status.in_(ACTIVE_TASK_STATUSES),
            )
            .order_by(WorkTask.id.desc())
        )
    )
    for task in tasks:
        if task.pull_request_id is None:
            return task, await submissions_for(db, task.id)
        pull = await db.get(PullRequest, task.pull_request_id)
        if pull is None or pull.state == "open":
            return task, await submissions_for(db, task.id)
        if pull.state in {"merged", "closed"}:
            task.status = "merged" if pull.state == "merged" else "closed"
            task.finished_at = datetime.now(UTC)
    return None


async def create_work_task(
    db: AsyncSession,
    *,
    session_id: int,
    owner: str,
    repo: str,
    title: str,
    requirement_summary: str,
    base_ref: str = "",
    branch: str = "",
    pull_request_id: int | None = None,
    status: str = "active",
) -> WorkTask:
    task = WorkTask(
        session_id=session_id,
        owner=owner,
        repo=repo,
        title=title,
        requirement_summary=requirement_summary,
        current_summary=requirement_summary,
        status=status,
        branch=branch,
        base_ref=base_ref,
        pull_request_id=pull_request_id,
    )
    db.add(task)
    await db.flush()
    return task


async def update_work_task(
    db: AsyncSession,
    *,
    session_id: int,
    task_id: int,
    current_summary: str | None = None,
    status: str | None = None,
    title: str | None = None,
    branch: str | None = None,
    base_ref: str | None = None,
    pull_request_id: int | None = None,
) -> WorkTask | None:
    task = await db.scalar(
        select(WorkTask).where(
            WorkTask.id == task_id,
            WorkTask.session_id == session_id,
        )
    )
    if task is None:
        return None
    if current_summary is not None:
        task.current_summary = current_summary
    if title is not None:
        task.title = title
    if branch is not None:
        task.branch = branch
    if base_ref is not None:
        task.base_ref = base_ref
    if pull_request_id is not None:
        task.pull_request_id = pull_request_id
    if status is not None:
        task.status = status
        task.finished_at = (
            datetime.now(UTC) if status in {"merged", "closed", "cancelled"} else None
        )
    await db.flush()
    await db.refresh(task)
    return task


async def append_task_submission(
    db: AsyncSession,
    *,
    session_id: int,
    task_id: int,
    commit_sha: str,
    head_sha: str,
    branch: str,
    base_ref: str,
    summary: str,
    merged_summary: str,
    changed_files: list[Any] | None = None,
    verification: list[Any] | None = None,
) -> tuple[WorkTask, TaskSubmission] | None:
    task = await db.scalar(
        select(WorkTask).where(
            WorkTask.id == task_id,
            WorkTask.session_id == session_id,
        )
    )
    if task is None:
        return None
    branch = branch.strip()
    base_ref = base_ref.strip()
    if not branch or not base_ref:
        raise ValueError("submission 的 branch/base_ref 不能为空")
    if branch == base_ref:
        raise ValueError("submission 的 branch 与 base_ref 不能相同")
    if task.branch and task.branch != branch:
        raise ValueError(
            f"任务分支是 {task.branch!r}，不能写入来自 {branch!r} 的提交"
        )
    task_branch = task.branch or branch
    task_base = task.base_ref.strip()
    if task_base and task_base == task_branch:
        # 历史数据或调用顺序错误可能把 head 写成了 base。这里已有
        # coder 实际推送的目标分支，允许用 submission 修复这条无效值。
        task_base = ""
    if task_base and task_base != base_ref:
        raise ValueError(
            f"任务 base_ref 是 {task_base!r}，不能写入来自 {base_ref!r} 的提交"
        )
    submission = TaskSubmission(
        task_id=task.id,
        commit_sha=commit_sha,
        head_sha=head_sha,
        branch=branch,
        base_ref=base_ref,
        summary=summary,
        changed_files=changed_files or [],
        verification=verification or [],
    )
    db.add(submission)
    task.branch = task_branch
    task.base_ref = base_ref
    task.latest_commit_sha = commit_sha
    task.latest_head_sha = head_sha
    task.current_summary = merged_summary or summary
    await db.flush()
    # ``updated_at`` uses a server-side onupdate. Refresh while still inside
    # the async session so serializing the payload never triggers lazy I/O.
    await db.refresh(task)
    return task, submission


async def link_work_task_pull(
    db: AsyncSession,
    *,
    session_id: int,
    task_id: int,
    pull_request_id: int,
) -> WorkTask | None:
    return await update_work_task(
        db,
        session_id=session_id,
        task_id=task_id,
        pull_request_id=pull_request_id,
        status="pr_open",
    )


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "active_work_task",
    "append_task_submission",
    "create_work_task",
    "get_work_task",
    "link_work_task_pull",
    "submissions_for",
    "update_work_task",
    "work_task_payload",
]
