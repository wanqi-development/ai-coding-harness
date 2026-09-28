"""复合后端：两类「溢出产物」必须进持久 store，而不是随沙箱一起消失。

deepagents 的摘要中间件写两个前缀（见 ``backends/composite.py`` 的模块注释）：
``/conversation_history/<thread_id>.md`` 与 ``/large_tool_results/…``。它们以前都落在
沙箱里，而沙箱会话结束就销毁、卷只挂 ``/ai_coding/project`` —— 于是摘要消息里那句
「完整历史已存到 <路径>，需要细节时可回看」在沙箱重建后是空话。本文件钉三件事：

1. 两条前缀确实路由到 StoreBackend（同库不同 namespace），别的路径照旧走默认后端；
2. 摘要中间件那套「读旧文 → 追加 → 写回」在 store 上成立（首次 `file_not_found` 不是异常，
   第二次 `aedit` 追加成功）—— 这是 `StoreBackend` 缺异步 `adownload_files` 时最容易踩的坑；
3. 换一个 composite 实例（等价于沙箱重建 / 进程重启）仍读得到 —— 持久性是真的。
"""

from __future__ import annotations

import asyncio

from deepagents.backends import StoreBackend
from deepagents.middleware.summarization import create_summarization_middleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.store.memory import InMemoryStore

from ai_coding_harness.backends.composite import (
    HISTORY_PREFIX,
    LARGE_TOOL_RESULTS_PREFIX,
    build_composite_backend,
)

THREAD = "3da409ae-1111-2222-3333-444455556666"
HISTORY_KEY = f"/{THREAD}.md"
HISTORY = f"{HISTORY_PREFIX}{THREAD}.md"
LARGE = f"{LARGE_TOOL_RESULTS_PREFIX}{THREAD}/tool-1.txt"
SECTION_1 = "## Summarized at 2026-09-25T01:00:00+00:00\n\nHuman: 一\n\n"
SECTION_2 = "## Summarized at 2026-09-25T02:00:00+00:00\n\nHuman: 二\n\n"


def _build(store: InMemoryStore):
    """假默认后端用另一个 namespace 的 StoreBackend —— 「落在默认后端上」即可断言到。"""
    sandbox_stand_in = StoreBackend(store=store, namespace=lambda rt: ("sandbox",))
    return build_composite_backend(sandbox_stand_in, store)


class TestTheTwoOverflowPrefixesArePersistent:
    def test_both_prefixes_route_to_the_store(self):
        store = InMemoryStore()
        backend = _build(store)

        writes = asyncio.run(_write_both(backend))

        assert writes == [None, None]  # 两个 awrite 都没报错（error 字段为空）
        assert store.get(("conversation_history",), HISTORY_KEY) is not None
        assert store.get(("large_tool_results",), f"/{THREAD}/tool-1.txt") is not None
        # 默认后端（真机是沙箱）一个字节都没收到
        assert store.get(("sandbox",), HISTORY_KEY) is None
        assert store.get(("sandbox",), f"/{THREAD}/tool-1.txt") is None

    def test_other_paths_still_go_to_the_default_backend(self):
        """路由是**按前缀**加的：工作区文件不能因为这次改动换了地方。"""
        store = InMemoryStore()
        backend = _build(store)

        asyncio.run(backend.awrite("/ai_coding/project/a.py", "print(1)"))

        assert store.get(("sandbox",), "/ai_coding/project/a.py") is not None
        assert store.get(("conversation_history",), "/ai_coding/project/a.py") is None

    def test_artifacts_survive_a_rebuilt_backend(self):
        """沙箱重建 / 进程重启的等价断言：同一份 store，换一个 composite 实例。"""
        store = InMemoryStore()
        asyncio.run(_write_both(_build(store)))

        rebuilt = _build(store)
        history = asyncio.run(rebuilt.adownload_files([HISTORY]))
        large = asyncio.run(rebuilt.adownload_files([LARGE]))

        assert history[0].error is None
        assert history[0].content.decode() == SECTION_1
        assert large[0].error is None
        assert large[0].content.decode() == "tool output"

    def test_the_artifacts_root_is_pinned(self):
        """前缀是按 ``artifacts_root`` 拼的：这一侧显式传 ``/``，不让上游默认值悄悄变。"""
        assert _build(InMemoryStore()).artifacts_root == "/"


class TestTheOffloadFlowWorksOnTheStore:
    """摘要中间件的真实调用序列：``adownload → awrite``，之后 ``adownload → aedit``。"""

    def test_first_read_reports_not_found_without_raising(self):
        backend = _build(InMemoryStore())

        first = asyncio.run(backend.adownload_files([HISTORY]))

        assert first[0].content is None
        assert first[0].error == "file_not_found"

    def test_the_second_event_appends_instead_of_overwriting(self):
        store = InMemoryStore()
        backend = _build(store)

        asyncio.run(backend.awrite(HISTORY, SECTION_1))
        existing = asyncio.run(backend.adownload_files([HISTORY]))[0]
        old = existing.content.decode()
        edited = asyncio.run(backend.aedit(HISTORY, old, old + SECTION_2))
        final = asyncio.run(backend.adownload_files([HISTORY]))[0]

        assert edited.error is None
        assert edited.occurrences == 1
        assert final.content.decode() == SECTION_1 + SECTION_2
        assert "一" in final.content.decode()
        assert "二" in final.content.decode()

    def test_the_agent_can_read_it_back(self):
        """摘要消息告诉模型「需要细节就回看」—— 那条路是 ``read_file``（``aread``）。"""
        store = InMemoryStore()
        asyncio.run(_write_both(_build(store)))
        backend = _build(store)

        read = asyncio.run(backend.aread(HISTORY))
        listed = asyncio.run(backend.als(HISTORY_PREFIX))

        assert read.error is None
        assert SECTION_1 in _read_text(read)
        assert [entry.get("path") for entry in (listed.entries or [])] == [HISTORY]


class TestThePrefixesMatchWhatDeepagentsComputes:
    """前缀的口径归 deepagents：这里直接拿中间件实例上算出来的值比对。

    上游若把默认 ``artifacts_root`` 改成别的值（或换前缀名），这条当场红 —— 比在注释里
    写一句「注意保持一致」可靠。
    """

    def test_both_prefixes_line_up(self):
        backend = _build(InMemoryStore())

        middleware = create_summarization_middleware(
            GenericFakeChatModel(messages=iter(["ok"])), backend
        )

        assert middleware._history_path_prefix == HISTORY_PREFIX.rstrip("/")
        assert middleware._large_tool_results_prefix == LARGE_TOOL_RESULTS_PREFIX.rstrip("/")


async def _write_both(backend) -> list[str | None]:
    history = await backend.awrite(HISTORY, SECTION_1)
    large = await backend.awrite(LARGE, "tool output")
    return [history.error, large.error]


def _read_text(read) -> str:
    content = read.file_data.get("content") if read.file_data else ""
    return content or ""
