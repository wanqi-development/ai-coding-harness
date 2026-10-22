"""PR 列表 → 仓库记忆增量同步。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ai_coding_harness.server.repo_memory_sync import (
    PrMemorySummary,
    sync_repo_memory,
)

PATH = "/owner/repo.md"


class _Backend:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.writes = 0
        self.edits = 0

    async def aread(self, path: str) -> Any:
        if self.text is None:
            return SimpleNamespace(error="missing", file_data=None)
        return SimpleNamespace(
            error=None, file_data={"content": self.text}
        )

    async def awrite(self, path: str, content: str) -> Any:
        self.writes += 1
        self.text = content
        return SimpleNamespace(error=None, path=path)

    async def aedit(self, path: str, old: str, new: str) -> Any:
        self.edits += 1
        if self.text != old:
            return SimpleNamespace(error="old mismatch", path=None)
        self.text = new
        return SimpleNamespace(error=None, path=path)


def _pull(number: int, *, title: str, state: str) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "state": state,
        "html_url": f"https://gitee.com/owner/repo/pulls/{number}",
        "head": {"ref": f"feature/{number}"},
    }


MEMORY = """# 仓库记忆：owner/repo

> 最后更新：2026-10-20 10:00　｜　最近 PR：[#1](https://gitee.com/owner/repo/pulls/1)

## 一、仓库基本信息

## 二、变更记录（最新在上）

### 2026-10-20 · 修改 · 旧功能
- **PR**：[#1](https://gitee.com/owner/repo/pulls/1)　｜　**状态**：待合并

## 三、当前缺口（按模块）

## 四、长期约束
"""


class TestSync:
    async def test_new_prs_use_one_batch_summary_and_are_written(self) -> None:
        backend = _Backend(MEMORY)
        calls: list[tuple[dict[str, Any], ...]] = []

        async def summarize(pulls: tuple[dict[str, Any], ...]):
            calls.append(pulls)
            return {2: PrMemorySummary("新增", "增加导出接口")}

        result = await sync_repo_memory(
            backend,
            PATH,
            owner="owner",
            repo="repo",
            pulls=(
                _pull(1, title="旧功能", state="merged"),
                _pull(2, title="导出接口", state="open"),
            ),
            now="2026-10-22 12:00",
            summarize=summarize,
        )

        assert len(calls) == 1
        assert [pull["number"] for pull in calls[0]] == [2]
        assert result.new_prs == ("2",)
        assert result.restated == ("1",)
        assert "**状态**：已合并" in backend.text
        assert "#2" in backend.text and "增加导出接口" in backend.text
        assert backend.edits == 1

    async def test_status_only_change_does_not_call_the_model(self) -> None:
        backend = _Backend(MEMORY)

        async def summarize(pulls):
            raise AssertionError("should not summarize")

        result = await sync_repo_memory(
            backend,
            PATH,
            owner="owner",
            repo="repo",
            pulls=(_pull(1, title="旧功能", state="closed"),),
            now="2026-10-22 12:00",
            summarize=summarize,
        )

        assert result.new_prs == ()
        assert result.restated == ("1",)
        assert "**状态**：已关闭" in backend.text

    async def test_identical_snapshot_does_not_write(self) -> None:
        text = MEMORY.replace("**状态**：待合并", "**状态**：已合并")
        backend = _Backend(text)

        result = await sync_repo_memory(
            backend,
            PATH,
            owner="owner",
            repo="repo",
            pulls=(_pull(1, title="旧功能", state="merged"),),
            now="2026-10-22 12:00",
        )

        assert result.changed is False
        assert backend.edits == 0

    async def test_first_sync_creates_memory_and_summarizes_latest_eight(self) -> None:
        backend = _Backend()
        pulls = tuple(
            _pull(number, title=f"功能 {number}", state="merged")
            for number in range(1, 11)
        )

        async def summarize(fresh):
            return {
                int(pull["number"]): PrMemorySummary(
                    "修改", f"摘要 {pull['number']}"
                )
                for pull in fresh
            }

        result = await sync_repo_memory(
            backend,
            PATH,
            owner="owner",
            repo="repo",
            pulls=pulls,
            now="2026-10-22 12:00",
            summarize=summarize,
        )

        assert result.created is True
        assert result.new_prs == tuple(str(number) for number in range(3, 11))
        assert backend.writes == 1
        assert "摘要 10" in backend.text
        assert "摘要 2" not in backend.text
