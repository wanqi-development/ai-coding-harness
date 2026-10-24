"""每轮一次性 PR 快照：意图候选与仓库记忆同步共用同一份列表。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from ai_coding_harness.db import dao
from ai_coding_harness.tools.git import build_git_client
from ai_coding_harness.tools.pr_client import (
    PULL_OPEN,
    PrLookupError,
    commits_match_agent_identity,
    pull_state,
    pull_summary,
)


@dataclass(frozen=True)
class PullSnapshot:
    """一次轮首 PR 快照：最近 10 条用于记忆，open 全量用于工作闸门。"""

    pulls: tuple[dict[str, Any], ...]
    open_pulls: tuple[dict[str, Any], ...] | None = None
    blocked_reason: str | None = None

    @property
    def open_prs(self) -> tuple[dict[str, Any], ...]:
        if self.open_pulls is not None:
            return self.open_pulls
        return tuple(pull for pull in self.pulls if pull_state(pull) == PULL_OPEN)

    @property
    def owned_open_prs(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            pull
            for pull in self.open_prs
            if dao.is_owned_pull_source(str(pull.get("_source") or dao.PR_SOURCE_AGENT))
        )

    @property
    def has_open_prs(self) -> bool:
        return bool(self.owned_open_prs)

    @property
    def has_unreviewed_open_pr(self) -> bool:
        """存在 open PR 且至少一个还没有评论：允许 coding 继续收尾。"""
        return any(
            not bool(pull.get("_has_comments")) for pull in self.owned_open_prs
        )

    @property
    def has_reviewed_open_pr(self) -> bool:
        """任一 open PR 已有评论，说明审查链路已开始，后续只能 rework。"""
        return any(bool(pull.get("_has_comments")) for pull in self.owned_open_prs)


def _number(pull: dict[str, Any]) -> int:
    try:
        return int(str(pull.get("number") or "").strip())
    except ValueError:
        return 0


def _sha(pull: dict[str, Any]) -> str:
    head = pull.get("head")
    return str(head.get("sha") or "").strip() if isinstance(head, dict) else ""


def _timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _dedupe(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for group in groups:
        for pull in group:
            number = _number(pull)
            if number:
                out[number] = dict(pull)
    return [out[number] for number in sorted(out, reverse=True)]


async def _bootstrap_source(
    client: Any, pull: dict[str, Any], *, agent_name: str, agent_email: str
) -> str:
    number = _number(pull)
    if number <= 0:
        return dao.PR_SOURCE_UNKNOWN
    try:
        commits = await client.list_pull_commits(number=number, page=1, limit=100)
    except httpx.HTTPError:
        return dao.PR_SOURCE_UNKNOWN
    if isinstance(commits, dict):
        return dao.PR_SOURCE_UNKNOWN
    if not commits:
        return dao.PR_SOURCE_UNKNOWN
    return (
        dao.PR_SOURCE_LEGACY_AGENT
        if commits_match_agent_identity(
            commits,
            name=agent_name,
            email=agent_email,
        )
        else dao.PR_SOURCE_EXTERNAL
    )


def _blocked_message(open_pulls: tuple[dict[str, Any], ...]) -> str | None:
    foreign = [
        pull
        for pull in open_pulls
        if not dao.is_owned_pull_source(str(pull.get("_source") or ""))
    ]
    if not foreign:
        return None
    labels = []
    for pull in foreign[:5]:
        number = str(pull.get("number") or "?")
        title = str(pull.get("title") or "").strip()
        labels.append(f"#{number}" + (f"《{title}》" if title else ""))
    hidden = len(foreign) - len(labels)
    suffix = f"等 {len(foreign)} 个 PR" if hidden > 0 else ""
    return (
        f"检测到无法确认由当前 Agent 创建的 open PR：{'、'.join(labels)}{suffix}。"
        "为避免与外部改动冲突，本轮已停止工作；请先处理上述 PR 后再继续。"
    )


async def load_pull_snapshot(
    settings: Any,
    repo: Any,
    *,
    database: Any | None = None,
    session_id: int | None = None,
) -> PullSnapshot:
    """拉最近 10 条全部 PR 与完整 open PR，并同步 PR 归属表。

    coder 建完 PR、reviewer 失败时，PR 是 open 但没有评论；这时任务并未进入可返工
    阶段，coding 仍要能继续收尾。评论只对 open PR 查，closed / merged 不花请求。
    """
    client = build_git_client(settings, repo)
    newest_before: int | None = None
    if database is not None:
        async with database.session() as db:
            newest_before = await dao.newest_pull_number(
                db,
                platform=repo.platform.value,
                owner=repo.owner,
                repo=repo.repo,
            )
    recent_raw, open_raw = await asyncio.gather(
        client.all_pulls(limit=10, until_number=newest_before),
        client.open_pulls(),
    )
    recent = tuple(dict(pull) for pull in recent_raw)
    opened = tuple(dict(pull) for pull in open_raw)
    all_raw = _dedupe(list(opened), list(recent))

    sources: dict[int, str] = {}
    rows_by_number: dict[int, Any] = {}
    if database is not None:
        async with database.session() as db:
            numbers = [_number(pull) for pull in all_raw if _number(pull)]
            rows = await dao.list_pull_requests(
                db,
                platform=repo.platform.value,
                owner=repo.owner,
                repo=repo.repo,
                numbers=numbers,
            )
            rows_by_number = {int(row.number): row for row in rows}
            open_numbers = {_number(pull) for pull in opened}
            for pull in all_raw:
                number = _number(pull)
                if number <= 0:
                    continue
                existing = rows_by_number.get(number)
                if existing is not None and dao.is_owned_pull_source(existing.source):
                    sources[number] = existing.source
                    continue
                if number in open_numbers and (
                    existing is None or existing.source == dao.PR_SOURCE_UNKNOWN
                ):
                    sources[number] = await _bootstrap_source(
                        client,
                        pull,
                        agent_name=settings.sandbox_git_user_name,
                        agent_email=settings.sandbox_git_user_email,
                    )
                else:
                    sources[number] = (
                        existing.source if existing is not None else dao.PR_SOURCE_UNKNOWN
                    )

            for pull in all_raw:
                number = _number(pull)
                if number <= 0:
                    continue
                summary = pull_summary(pull)
                row = await dao.upsert_pull_request(
                    db,
                    platform=repo.platform.value,
                    owner=repo.owner,
                    repo=repo.repo,
                    number=number,
                    title=summary["title"],
                    state=pull_state(pull),
                    url=summary["url"],
                    author=summary["author"],
                    head_ref=summary["branch"],
                    head_sha=_sha(pull),
                    base_ref=str((pull.get("base") or {}).get("ref") or ""),
                    source=sources[number],
                    session_id=session_id,
                    remote_created_at=_timestamp(pull.get("created_at")),
                    remote_updated_at=_timestamp(pull.get("updated_at")),
                )
                rows_by_number[number] = row

    def base_annotation(pull: dict[str, Any]) -> dict[str, Any]:
        number = _number(pull)
        row = rows_by_number.get(number)
        source = (
            row.source
            if row is not None
            else sources.get(number, dao.PR_SOURCE_AGENT)
        )
        return {
            **pull,
            "_pull_id": getattr(row, "id", None),
            "_source": source,
            "_plan_id": getattr(row, "plan_id", None),
            "_tracked_head_sha": getattr(row, "head_sha", _sha(pull)),
            "_synced_head_sha": getattr(row, "synced_head_sha", None),
            "_head_changed": bool(
                getattr(row, "head_sha", "")
                and getattr(row, "synced_head_sha", None)
                and row.head_sha != row.synced_head_sha
            ),
        }

    async def annotate_open(pull: dict[str, Any]) -> dict[str, Any]:
        pull = base_annotation(pull)
        if pull_state(pull) != PULL_OPEN:
            return pull
        if not dao.is_owned_pull_source(str(pull.get("_source") or "")):
            return {**pull, "_has_comments": False}
        number = int(pull.get("number") or 0)
        comments = await client.list_pull_comments(number=number, page=1, limit=1)
        if isinstance(comments, dict):
            reason = comments.get("error")
            raise PrLookupError(str(reason or "PR 评论查询返回了非列表结果"))
        return {**pull, "_has_comments": bool(comments)}

    annotated_open = await asyncio.gather(*(annotate_open(pull) for pull in opened))
    open_by_number = {_number(pull): pull for pull in annotated_open}
    annotated_recent = tuple(
        open_by_number.get(_number(pull), base_annotation(pull))
        for pull in recent
    )
    open_tuple = tuple(annotated_open)
    return PullSnapshot(
        annotated_recent,
        open_pulls=open_tuple,
        blocked_reason=_blocked_message(open_tuple),
    )
def intent_candidates(snapshot: PullSnapshot | None) -> tuple[str, ...]:
    """PR 查询失败时只允许 qa；正常时按 open PR 收口写侧候选。"""
    if snapshot is None:
        return ("qa",)
    if snapshot.blocked_reason is not None:
        return ("qa",)
    if snapshot.has_open_prs:
        if snapshot.has_unreviewed_open_pr and not snapshot.has_reviewed_open_pr:
            # reviewer 还没产出评论时，没有可返工对象；只能继续 coding 收尾。
            return ("qa", "coding")
        return ("qa", "rework")
    return ("qa", "plan", "coding")


def describe_snapshot(snapshot: PullSnapshot | None) -> str:
    """给分类器与主控看的短状态。"""
    if snapshot is None:
        return "无法确认（PR 状态查询失败）"
    if snapshot.blocked_reason:
        return snapshot.blocked_reason
    open_prs = snapshot.open_prs
    if not open_prs:
        return "没有 open PR（本轮实时查询）"
    names = []
    for pull in open_prs[:5]:
        number = str(pull.get("number") or "").strip() or "?"
        title = str(pull.get("title") or "").strip()
        sha = str((pull.get("head") or {}).get("sha") or "").strip()[:8]
        review = "已有评论" if pull.get("_has_comments") else "暂无评论，coding 可继续"
        details = f"head {sha}；{review}" if sha else review
        names.append(f"#{number} {title}（{details}）".strip())
    hidden = len(open_prs) - len(names)
    suffix = f"，另有 {hidden} 个" if hidden > 0 else ""
    return f"有 {len(open_prs)} 个 open PR：{'；'.join(names)}{suffix}（本轮实时查询）"
