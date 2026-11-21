"""Code-versioned review records and findings."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import (
    Review,
    ReviewFinding,
    SessionRecord,
    SessionRecordDetail,
)

SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
FINDING_STATUSES = frozenset({"open", "accepted", "fixed", "wont_fix", "stale"})
REVIEW_STATUSES = frozenset({"running", "completed", "failed", "stale", "cancelled"})


def normalize_findings(raw: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalize findings supplied by the reviewer."""
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise TypeError(f"第 {index} 条 finding 不是对象")
        severity = str(item.get("severity") or "").upper()
        if severity not in SEVERITIES:
            raise ValueError(f"第 {index} 条 finding 的 severity 必须是 P0/P1/P2/P3")
        status = str(item.get("status") or "open")
        if status not in FINDING_STATUSES:
            raise ValueError(f"第 {index} 条 finding 的 status 无效：{status}")
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        if not title and not description:
            raise ValueError(f"第 {index} 条 finding 至少要有 title 或 description")
        line = item.get("line")
        try:
            line_number = int(line) if line not in (None, "") else None
        except (TypeError, ValueError):
            line_number = None
        out.append(
            {
                "severity": severity,
                "file_path": str(item.get("file") or item.get("file_path") or "").strip(),
                "line_number": line_number,
                "title": title,
                "description": description,
                "status": status,
            }
        )
    return out


def review_payload(review: Review, findings: Sequence[ReviewFinding]) -> dict[str, Any]:
    """JSON shape shared by session records, SSE and frontend."""
    return {
        "review_id": review.id,
        "session_id": review.session_id,
        "plan_id": review.plan_id,
        "pull_request_id": review.pull_request_id,
        "stage": review.stage,
        "status": review.status,
        "base_ref": review.base_ref,
        "base_sha": review.base_sha,
        "head_ref": review.head_ref,
        "head_sha": review.head_sha,
        "code_revision": review.code_revision,
        "summary": review.summary,
        "report_markdown": review.report_markdown,
        "reviewer_agent": review.reviewer_agent,
        "provider": review.provider,
        "model": review.model_name,
        "rules_version": review.rules_version,
        "created_at": review.created_at.isoformat(),
        "updated_at": review.updated_at.isoformat(),
        "findings": [
            {
                "finding_id": item.id,
                "severity": item.severity,
                "file": item.file_path,
                "line": item.line_number,
                "title": item.title,
                "description": item.description,
                "status": item.status,
            }
            for item in findings
        ],
    }


async def findings_for(
    db: AsyncSession, review_id: int
) -> list[ReviewFinding]:
    rows = await db.scalars(
        select(ReviewFinding)
        .where(ReviewFinding.review_id == review_id)
        .order_by(ReviewFinding.id)
    )
    return list(rows)


async def get_review(
    db: AsyncSession, *, session_id: int, review_id: int
) -> tuple[Review, list[ReviewFinding]] | None:
    review = await db.scalar(
        select(Review).where(
            Review.id == review_id,
            Review.session_id == session_id,
        )
    )
    if review is None:
        return None
    return review, await findings_for(db, review.id)


async def latest_review(
    db: AsyncSession,
    *,
    session_id: int,
    stage: str | None = None,
) -> tuple[Review, list[ReviewFinding]] | None:
    query = select(Review).where(Review.session_id == session_id)
    if stage:
        query = query.where(Review.stage == stage)
    review = await db.scalar(query.order_by(Review.id.desc()).limit(1))
    if review is None:
        return None
    return review, await findings_for(db, review.id)


async def list_reviews(
    db: AsyncSession, *, session_id: int, limit: int = 20
) -> list[tuple[Review, list[ReviewFinding]]]:
    rows = list(
        await db.scalars(
            select(Review)
            .where(Review.session_id == session_id)
            .order_by(Review.id.desc())
            .limit(max(1, limit))
        )
    )
    return [(review, await findings_for(db, review.id)) for review in rows]


async def mark_code_revision_stale(
    db: AsyncSession,
    *,
    session_id: int,
    current_revision: str,
    stage: str | None = None,
) -> int:
    """Invalidate completed reviews for the same session at another revision."""
    stmt = (
        update(Review)
        .where(
            Review.session_id == session_id,
            Review.status == "completed",
            Review.code_revision != current_revision,
        )
        .values(status="stale")
    )
    if stage:
        stmt = stmt.where(Review.stage == stage)
    result = await db.execute(stmt)
    stale_ids = select(Review.id).where(
        Review.session_id == session_id,
        Review.status == "stale",
    )
    await db.execute(
        update(ReviewFinding)
        .where(
            ReviewFinding.review_id.in_(stale_ids),
            ReviewFinding.status == "open",
        )
        .values(status="stale")
    )
    return int(result.rowcount or 0)


async def store_review(
    db: AsyncSession,
    *,
    session_id: int,
    plan_id: int | None,
    pull_request_id: int | None,
    stage: str,
    base_ref: str,
    base_sha: str,
    head_ref: str,
    head_sha: str,
    code_revision: str,
    summary: str,
    report_markdown: str,
    findings: Sequence[dict[str, Any]],
    reviewer_agent: str = "reviewer",
    provider: str = "",
    model_name: str = "",
    rules_version: str = "v1",
) -> tuple[Review, list[ReviewFinding]]:
    normalized = normalize_findings(findings)
    await mark_code_revision_stale(
        db,
        session_id=session_id,
        current_revision=code_revision,
    )
    review = Review(
        session_id=session_id,
        plan_id=plan_id,
        pull_request_id=pull_request_id,
        stage=stage,
        status="completed",
        base_ref=base_ref,
        base_sha=base_sha,
        head_ref=head_ref,
        head_sha=head_sha,
        code_revision=code_revision,
        summary=summary,
        report_markdown=report_markdown,
        reviewer_agent=reviewer_agent,
        provider=provider,
        model_name=model_name,
        rules_version=rules_version,
    )
    db.add(review)
    await db.flush()
    items = [ReviewFinding(review_id=review.id, **finding) for finding in normalized]
    db.add_all(items)
    await db.flush()

    counts = {level: 0 for level in ("P0", "P1", "P2", "P3")}
    for finding in normalized:
        counts[finding["severity"]] += 1
    payload = review_payload(review, items)
    record = SessionRecord(
        session_id=session_id,
        type="review",
        summary=summary[:200],
        ref_id=review.id,
    )
    db.add(record)
    await db.flush()
    next_action = (
        "PR 审查完成。下一步请检查结论并合并 PR；合并后本阶段任务完成。"
        if stage == "pr"
        else "本地审查完成。下一步由主控按当前任务目标决定继续修改或交付 PR；"
        "创建并合并 PR 后本阶段任务完成。"
    )
    db.add(
        SessionRecordDetail(
            record_id=record.id,
            content=report_markdown,
            extra={
                "review": payload,
                "counts": counts,
                "next_action": next_action,
            },
        )
    )
    await db.flush()
    return review, items
