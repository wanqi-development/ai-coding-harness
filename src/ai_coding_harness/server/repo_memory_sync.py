"""每轮 PR → 仓库记忆的增量同步（真实落盘）。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from deepagents.backends.protocol import BackendProtocol

from ai_coding_harness.agent.middleware.memory_reconcile import (
    Reconciled,
    memory_pull_state,
    newest_recorded_pull,
    reconcile,
    strip_legacy,
)
from ai_coding_harness.agent.middleware.memory_sections import (
    add_change,
    empty_memory,
    parse,
    render_memory,
)
from ai_coding_harness.tools.pr_client import pull_summary

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrMemorySummary:
    kind: str
    summary: str


@dataclass(frozen=True)
class MemorySyncResult:
    text: str
    changed: bool
    created: bool
    new_prs: tuple[str, ...]
    restated: tuple[str, ...]
    pending: tuple[Any, ...]


PrSummaryFn = Callable[
    [tuple[dict[str, Any], ...]], Awaitable[Mapping[int, PrMemorySummary]]
]


def _number(pull: Mapping[str, Any]) -> int | None:
    raw = str(pull.get("number") or "").strip()
    return int(raw) if raw.isdigit() else None


def new_pulls(
    text: str, pulls: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    """比记忆水印更新的 PR；首次同步只取最新 8 个，避免一次吞整段历史。"""
    watermark = newest_recorded_pull(text)
    numbered = [
        (number, dict(pull))
        for pull in pulls
        if (number := _number(pull)) is not None
    ]
    numbered.sort(key=lambda item: item[0])
    if watermark is None:
        numbered = numbered[-8:]
    else:
        numbered = [(number, pull) for number, pull in numbered if number > watermark]
    return tuple(pull for _number_value, pull in numbered)


def _fallback_summaries(
    pulls: Sequence[Mapping[str, Any]],
) -> dict[int, PrMemorySummary]:
    out: dict[int, PrMemorySummary] = {}
    for pull in pulls:
        number = _number(pull)
        if number is None:
            continue
        title = str(pull.get("title") or "").strip() or f"PR #{number}"
        out[number] = PrMemorySummary("修改", title[:120])
    return out


async def _read_text(backend: BackendProtocol, path: str) -> str | None:
    stored = await backend.aread(path)
    return (getattr(stored, "file_data", None) or {}).get("content")


async def _write_text(
    backend: BackendProtocol, path: str, old: str | None, new: str
) -> None:
    if old is None:
        result = await backend.awrite(path, new)
    else:
        result = await backend.aedit(path, old, new)
    error = getattr(result, "error", None)
    if error:
        raise RuntimeError(str(error))


async def sync_repo_memory(
    backend: BackendProtocol,
    path: str,
    *,
    owner: str,
    repo: str,
    pulls: Sequence[Mapping[str, Any]],
    now: str,
    summarize: PrSummaryFn | None = None,
) -> MemorySyncResult:
    """把新 PR 摘要与状态差异写入记忆；无差异时不写盘。"""
    old = await _read_text(backend, path)
    base = old if old is not None else empty_memory(owner, repo)
    fresh = new_pulls(base, pulls)

    summaries: Mapping[int, PrMemorySummary] = {}
    if fresh and summarize is not None:
        try:
            summaries = await summarize(fresh)
        except Exception:
            logger.exception("新 PR 摘要失败，退回使用 PR 标题")
    if fresh and not summaries:
        summaries = _fallback_summaries(fresh)

    doc = parse(strip_legacy(base))
    added: list[str] = []
    for pull in fresh:
        number = _number(pull)
        if number is None:
            continue
        info = summaries.get(number) or _fallback_summaries([pull])[number]
        summary = pull_summary(pull)
        add_change(
            doc,
            date=now[:10],
            kind=info.kind,
            summary=info.summary,
            pr=number,
            status=memory_pull_state(pull) or "待合并",
            url=summary["url"],
        )
        added.append(str(number))

    rendered = render_memory(doc)
    result: Reconciled = reconcile(rendered, pulls, now=now)
    changed = result.changed or bool(added) or old is None
    if changed:
        await _write_text(backend, path, old, result.text)
    return MemorySyncResult(
        text=result.text,
        changed=changed,
        created=old is None,
        new_prs=tuple(added),
        restated=result.restated,
        pending=result.pending,
    )
