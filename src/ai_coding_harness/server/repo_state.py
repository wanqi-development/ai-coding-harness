"""每轮一次性 PR 快照：意图候选与仓库记忆同步共用同一份列表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_coding_harness.tools.git import build_git_client
from ai_coding_harness.tools.pr_client import PULL_OPEN, pull_state


@dataclass(frozen=True)
class PullSnapshot:
    """一次完整 PR 列表查询结果；不做跨轮缓存。"""

    pulls: tuple[dict[str, Any], ...]

    @property
    def open_prs(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            pull for pull in self.pulls if pull_state(pull) == PULL_OPEN
        )

    @property
    def has_open_prs(self) -> bool:
        return bool(self.open_prs)


async def load_pull_snapshot(settings: Any, repo: Any) -> PullSnapshot:
    """每轮调用一次代码平台列表接口，返回平台分页范围内的全量 PR。"""
    pulls = await build_git_client(settings, repo).all_pulls()
    return PullSnapshot(tuple(pulls))


def intent_candidates(snapshot: PullSnapshot | None) -> tuple[str, ...]:
    """PR 查询失败时只允许 qa；正常时按 open PR 收口写侧候选。"""
    if snapshot is None:
        return ("qa",)
    if snapshot.has_open_prs:
        return ("qa", "rework")
    return ("qa", "plan", "coding")


def describe_snapshot(snapshot: PullSnapshot | None) -> str:
    """给分类器与主控看的短状态。"""
    if snapshot is None:
        return "无法确认（PR 状态查询失败）"
    open_prs = snapshot.open_prs
    if not open_prs:
        return "没有 open PR（本轮实时查询）"
    names = []
    for pull in open_prs[:5]:
        number = str(pull.get("number") or "").strip() or "?"
        title = str(pull.get("title") or "").strip()
        names.append(f"#{number} {title}".strip())
    hidden = len(open_prs) - len(names)
    suffix = f"，另有 {hidden} 个" if hidden > 0 else ""
    return f"有 {len(open_prs)} 个 open PR：{'；'.join(names)}{suffix}（本轮实时查询）"
