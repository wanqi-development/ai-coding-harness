"""Main-agent tools for one durable code task and its coder submissions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.db import Database, dao
from ai_coding_harness.tools.context import get_configurable

TASK_RELATION = Literal["same", "new", "unclear"]


@dataclass(frozen=True)
class WorkTaskContext:
    database: Database
    session_id: int
    platform: str
    owner: str
    repo: str


def _state() -> TurnState | None:
    state = get_configurable("turn_state")
    return state if isinstance(state, TurnState) else None


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _current(ctx: WorkTaskContext):
    async with ctx.database.session() as db:
        return await dao.active_work_task(db, session_id=ctx.session_id)


def _task_payload(current) -> dict[str, Any]:
    task, submissions = current
    return dao.work_task_payload(task, submissions)


def build_work_task_tools(ctx: WorkTaskContext) -> list[BaseTool]:
    @tool
    async def getActiveWorkTask() -> str:
        """Read the current non-terminal code task and all coder submissions."""
        current = await _current(ctx)
        if current is None:
            return "当前没有进行中的代码任务。"
        return _json(_task_payload(current))

    @tool
    async def openWorkTask(
        requirement_summary: str,
        relation: TASK_RELATION,
        title: str = "",
        base_ref: str = "",
    ) -> str:
        """Bind this turn to a code task before dispatching coder.

        For an existing active task, relation must be ``same``. ``new`` is
        refused until the current task's PR is merged; ``unclear`` asks the
        user rather than guessing.
        """
        requirement = requirement_summary.strip()
        if not requirement:
            return "创建/绑定任务失败：requirement_summary 不能为空。"
        current = await _current(ctx)
        state = _state()

        if current is not None:
            payload = _task_payload(current)
            if relation == "same":
                current_branch = str(payload.get("branch") or "").strip()
                current_base = str(payload.get("base_ref") or "").strip()
                proposed_base = base_ref.strip()
                submissions = payload.get("submissions")
                latest_base = ""
                if isinstance(submissions, list) and submissions:
                    latest = submissions[-1]
                    if isinstance(latest, dict):
                        latest_base = str(latest.get("base_ref") or "").strip()
                repair_base = (
                    latest_base
                    if (
                        current_branch
                        and (not current_base or current_base == current_branch)
                        and latest_base
                        and latest_base != current_branch
                    )
                    else ""
                )
                if (
                    repair_base
                    and proposed_base
                    and proposed_base != repair_base
                ):
                    return (
                        f"任务 #{payload['task_id']} 的 base_ref 与最新 submission 冲突："
                        f"调用方给 {proposed_base!r}，submission 是 {repair_base!r}。"
                    )
                if repair_base:
                    # ``same`` 不再静默保留把头分支当成 base 的坏记录。
                    # 最新 submission 来自实际 push，用它修复任务根记录。
                    async with ctx.database.session() as db:
                        task = await dao.update_work_task(
                            db,
                            session_id=ctx.session_id,
                            task_id=int(payload["task_id"]),
                            base_ref=repair_base,
                        )
                        if task is not None:
                            payload = dao.work_task_payload(
                                task, await dao.submissions_for(db, task.id)
                            )
                if state is not None:
                    state.work_task_id = int(payload["task_id"])
                    state.work_task_payload = payload
                return _json(payload)
            if relation == "new":
                return (
                    f"当前任务 #{payload['task_id']} 尚未结束"
                    f"（状态 {payload['status']}，分支 {payload['branch'] or '-'}）。"
                    "当前工作区不能开始新的写代码任务；请先完成当前任务、创建并合并 PR。"
                    "问答或新任务方案仍可继续。"
                )
            return (
                f"当前已有任务 #{payload['task_id']}。先确认这次需求是继续当前任务，"
                "还是一个需要等当前任务合并后再开始的新任务。"
            )

        open_prs = tuple(getattr(state, "open_prs", ()) or ()) if state is not None else ()
        if open_prs and relation == "new":
            labels = "、".join(
                f"#{pull.get('number', '?')}" for pull in open_prs[:5]
            )
            return (
                f"当前仍有未合并 PR：{labels}。这是新任务，必须等当前任务完成并合并 PR 后"
                "再开始；问答或方案不受限制。"
            )
        if open_prs and relation == "unclear":
            return "当前已有未合并 PR。先确认这次是继续当前任务，还是另开新任务。"
        if relation == "same" and not open_prs:
            return (
                "当前没有可续接的 active task 或 open PR。"
                "如果这是要开始一个新的代码任务，请用 relation=\"new\"。"
            )

        pull_request_id = None
        branch = ""
        resolved_base = base_ref.strip()
        resolved_title = title.strip()
        status = "active"
        if open_prs:
            if len(open_prs) != 1:
                return "当前有多个未合并 PR，无法自动绑定任务；请点名要继续的 PR。"
            pull = open_prs[0]
            number = int(pull.get("number") or 0)
            if number <= 0:
                return "当前 PR 编号不可用，无法创建任务记录。"
            async with ctx.database.session() as db:
                row = await dao.get_pull_request(
                    db,
                    platform=ctx.platform,
                    owner=ctx.owner,
                    repo=ctx.repo,
                    number=number,
                )
            if row is None:
                return f"本地 PR 表没有 #{number}，请先刷新 PR 状态。"
            pull_request_id = row.id
            branch = str((pull.get("head") or {}).get("ref") or row.head_ref or "")
            # 已关联 PR 时，平台上的 base 是权威值；不能相信调用方可能传错的 ref。
            resolved_base = str((pull.get("base") or {}).get("ref") or row.base_ref or "")
            if not resolved_base:
                return f"PR #{number} 缺少目标分支，无法创建任务记录。"
            if branch == resolved_base:
                return (
                    f"PR #{number} 的 head 与 base 都是 {branch!r}，"
                    "任务记录未创建；请先修正平台 PR。"
                )
            resolved_title = resolved_title or str(pull.get("title") or "")
            status = "pr_open"

        async with ctx.database.session() as db:
            task = await dao.create_work_task(
                db,
                session_id=ctx.session_id,
                owner=ctx.owner,
                repo=ctx.repo,
                title=resolved_title or requirement[:80],
                requirement_summary=requirement,
                base_ref=resolved_base,
                branch=branch,
                pull_request_id=pull_request_id,
                status=status,
            )
            submissions = await dao.submissions_for(db, task.id)
            payload = dao.work_task_payload(task, submissions)
        if state is not None:
            state.work_task_id = int(payload["task_id"])
            state.work_task_payload = payload
        return _json(payload)

    @tool
    async def recordTaskSubmission(
        task_id: int,
        commit_sha: str,
        head_sha: str,
        branch: str,
        base_ref: str,
        summary: str,
        merged_summary: str,
        changed_files: list[str] | None = None,
        verification: list[str] | None = None,
    ) -> str:
        """Persist coder's commit/push result and update the cumulative task summary."""
        values = {
            "commit_sha": commit_sha.strip(),
            "head_sha": head_sha.strip(),
            "branch": branch.strip(),
            "summary": summary.strip(),
            "merged_summary": merged_summary.strip(),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            return "提交记录未保存：缺少 " + "、".join(missing)
        if values["commit_sha"] != values["head_sha"]:
            return (
                "提交记录未保存：commit_sha 与已推送 head_sha 不一致。"
                "请先确认 push 成功后再记录。"
            )
        try:
            async with ctx.database.session() as db:
                result = await dao.append_task_submission(
                    db,
                    session_id=ctx.session_id,
                    task_id=task_id,
                    commit_sha=values["commit_sha"],
                    head_sha=values["head_sha"],
                    branch=values["branch"],
                    base_ref=base_ref.strip(),
                    summary=values["summary"],
                    merged_summary=values["merged_summary"],
                    changed_files=changed_files or [],
                    verification=verification or [],
                )
                if result is None:
                    return f"提交记录未保存：当前会话没有任务 #{task_id}。"
                task, _submission = result
                payload = dao.work_task_payload(
                    task, await dao.submissions_for(db, task.id)
                )
        except ValueError as exc:
            return f"提交记录未保存：{exc}"
        state = _state()
        if state is not None:
            state.work_task_id = task.id
            state.work_task_payload = payload
        return _json(payload)

    @tool
    async def updateWorkTask(
        task_id: int,
        current_summary: str | None = None,
        title: str | None = None,
        status: str | None = None,
    ) -> str:
        """Update the main-owned task summary, title, or lifecycle status."""
        allowed = {"active", "pr_open", "merged", "closed", "cancelled"}
        if status is not None and status not in allowed:
            return "任务未更新：status 只能是 active / pr_open / merged / closed / cancelled。"
        async with ctx.database.session() as db:
            task = await dao.update_work_task(
                db,
                session_id=ctx.session_id,
                task_id=task_id,
                current_summary=current_summary,
                title=title,
                status=status,
            )
            if task is None:
                return f"任务未更新：当前会话没有任务 #{task_id}。"
            payload = dao.work_task_payload(
                task, await dao.submissions_for(db, task.id)
            )
        state = _state()
        if state is not None:
            state.work_task_id = task.id
            state.work_task_payload = payload
        return _json(payload)

    return [getActiveWorkTask, openWorkTask, recordTaskSubmission, updateWorkTask]


__all__ = ["WorkTaskContext", "build_work_task_tools"]
