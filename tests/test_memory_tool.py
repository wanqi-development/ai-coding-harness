"""``save_repo_memory`` 的行为守卫（2026-10-20 起是**按节增量写**）。

三条最容易出错的线：

1. **入参**：模型只能提交「哪一节 + 那一节的内容」，不能整篇重写；写错节（比如把本次改动
   塞进「仓库基本信息」）要被当场拒掉。
2. **落盘**：``StoreBackend.awrite`` 只新建，已存在的文件必须走 ``aedit`` 全文替换；
   内容没变就不写。
3. **职责边界**：PR 查询和新 PR / 状态同步在轮首拦截点与 coding/rework 轮末完成；
   `save_repo_memory` 只负责合并正文并落盘。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.config import Settings
from ai_coding_harness.tools import memory as memory_mod
from ai_coding_harness.tools.git import GiteeRepo
from ai_coding_harness.tools.register import build_main_tools

REPO = GiteeRepo(owner="acme", repo="demo", token="test-token")
PATH = "/memories/gitee/acme/demo.md"

#: 钉住的写盘时刻：落盘时的时间戳由平台现取，不钉住就断言不了。
STAMP = "2026-10-20 09:41"

#: 一份正常的记忆（旧口径的「关键决策」也会被认成「长期约束」）。
BASE = """# 仓库记忆：acme/demo

> 最后更新：2026-10-19 08:00　｜　最近 PR：—

## 一、仓库基本信息
- **技术栈**：Java 21

## 二、变更记录（最新在上）
### 2026-10-19 · 新增 · 商品列表分页
- **PR**：#12　｜　**状态**：待合并

## 三、当前缺口（按模块）
- **订单**：退款未实现

## 四、长期约束
- **约束**：不引入新依赖　｜　**原因**：沙箱离线　｜　**影响**：新增依赖要先问
"""


class _FakeBackend:
    """内存后端，语义对齐 ``StoreBackend``：``awrite`` 只新建、``aedit`` 才替换。"""

    def __init__(
        self,
        *,
        write_error: str | None = None,
        edit_error: str | None = None,
        read_error: str = "",
    ) -> None:
        self.files: dict[str, str] = {}
        self.areads: list[str] = []
        self.awrites: list[tuple[str, str]] = []
        self.aedits: list[tuple[str, str, str]] = []
        self._write_error = write_error
        self._edit_error = edit_error
        self._read_error = read_error

    async def aread(self, file_path: str) -> SimpleNamespace:
        self.areads.append(file_path)
        if self._read_error:
            return SimpleNamespace(error=self._read_error, file_data=None)
        if file_path not in self.files:
            return SimpleNamespace(error=f"File '{file_path}' not found", file_data=None)
        return SimpleNamespace(error=None, file_data={"content": self.files[file_path]})

    async def awrite(self, file_path: str, content: str) -> SimpleNamespace:
        self.awrites.append((file_path, content))
        if self._write_error:
            return SimpleNamespace(error=self._write_error, path=None)
        if file_path in self.files:
            return SimpleNamespace(
                error=f"Cannot write to {file_path} because it already exists.", path=None
            )
        self.files[file_path] = content
        return SimpleNamespace(error=None, path=file_path)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> SimpleNamespace:
        self.aedits.append((file_path, old_string, new_string))
        if self._edit_error:
            return SimpleNamespace(error=self._edit_error, path=None)
        if self.files.get(file_path) != old_string:
            return SimpleNamespace(error=f"old_string not found in {file_path}", path=None)
        self.files[file_path] = new_string
        return SimpleNamespace(error=None, path=file_path)


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> str:
    monkeypatch.setattr(memory_mod, "now_stamp", lambda: STAMP)
    return STAMP


def _tool(backend: _FakeBackend, settings: Settings | None = None):
    tools = build_main_tools(
        settings or Settings(),
        SimpleNamespace(name="web_search_tool"),  # type: ignore[arg-type]
        SimpleNamespace(name="fetch_url_tool"),  # type: ignore[arg-type]
        REPO,
        backend,  # type: ignore[arg-type]
    )
    return next(t for t in tools if t.name == "save_repo_memory")


async def _call(tool, section: str, payload: dict) -> str:
    return await tool.ainvoke({"section": section, "payload": payload})


# -- 落盘 ---------------------------------------------------------------------


async def test_first_write_creates_the_file(clock):
    backend = _FakeBackend()

    out = await _call(
        _tool(backend), "change", {"kind": "新增", "summary": "商品列表分页", "pr": 12}
    )

    assert "已创建" in out
    assert PATH in backend.files
    text = backend.files[PATH]
    assert f"> 最后更新：{STAMP}" in text, "文件头由平台盖章"
    assert "### 2026-10-20 · 新增 · 商品列表分页" in text
    assert "#12" in text and "待合并" in text
    assert backend.aedits == [], "首次建档走 awrite"


async def test_existing_memory_is_updated_through_edit():
    backend = _FakeBackend()
    backend.files[PATH] = BASE

    out = await _call(
        _tool(backend), "change", {"kind": "修改", "summary": "补分页参数", "pr": 13}
    )

    assert "已更新" in out
    assert backend.awrites == []
    assert backend.aedits, "已存在的文件必须走 aedit"
    text = backend.files[PATH]
    assert "#13" in text and "#12" in text


async def test_the_same_pr_is_not_recorded_twice():
    backend = _FakeBackend()
    backend.files[PATH] = BASE

    await _call(_tool(backend), "change", {"kind": "修改", "summary": "换个说法", "pr": 12})

    text = backend.files[PATH]
    assert text.count("#12") == 1, "同一个 PR 只该有一条"
    assert "换个说法" in text


async def test_the_change_window_keeps_only_eight():
    backend = _FakeBackend()
    backend.files[PATH] = BASE
    tool = _tool(backend)

    for number in range(20, 29):  # 9 条新的
        await _call(tool, "change", {"kind": "修改", "summary": f"第 {number} 个", "pr": number})

    text = backend.files[PATH]
    assert text.count("### ") == 8
    assert "#20" not in text, "最旧的滚出窗口"
    assert "#28" in text


async def test_identical_content_is_not_written_again():
    backend = _FakeBackend()
    backend.files[PATH] = BASE
    tool = _tool(backend)
    payload = {"kind": "修改", "summary": "补分页参数", "pr": 13}

    await _call(tool, "change", payload)
    before = len(backend.aedits)
    out = await _call(tool, "change", payload)

    assert "一致" in out
    assert len(backend.aedits) == before, "内容没变就不该再写一次"


async def test_create_failure_is_reported_not_swallowed():
    backend = _FakeBackend(write_error="存储挂了")

    out = await _call(_tool(backend), "gaps", {"items": [{"module": "订单", "text": "退款"}]})

    assert "写入仓库记忆失败" in out and "存储挂了" in out


async def test_edit_failure_is_reported_not_swallowed():
    backend = _FakeBackend(edit_error="改不动")
    backend.files[PATH] = BASE

    out = await _call(_tool(backend), "gaps", {"items": [{"module": "订单", "text": "退款"}]})

    assert "写入仓库记忆失败" in out and "改不动" in out


# -- 分节写入的限制 -------------------------------------------------------------


async def test_basic_only_accepts_the_whitelisted_fields():
    backend = _FakeBackend()
    backend.files[PATH] = BASE

    out = await _call(
        _tool(backend), "basic", {"fields": {"本次改动": "新增了分页"}}
    )

    assert "不是仓库基本信息的字段" in out
    assert backend.aedits == [] and backend.awrites == []


async def test_basic_updates_a_whitelisted_field():
    backend = _FakeBackend()
    backend.files[PATH] = BASE

    await _call(_tool(backend), "basic", {"fields": {"技术栈": "Java 17"}})

    text = backend.files[PATH]
    assert "**技术栈**：Java 17" in text
    assert "Java 21" not in text


async def test_basic_write_drops_fields_outside_the_whitelist():
    """存量里躺着的非白名单字段（「认证体系」这类）要在下一次 basic 写入时清掉。"""
    backend = _FakeBackend()
    backend.files[PATH] = BASE.replace(
        "- **技术栈**：Java 21",
        "- **技术栈**：Java 21\n- **认证体系**：网关集中验签\n- **服务间鉴权**：HMAC 签名",
    )

    out = await _call(_tool(backend), "basic", {"fields": {"技术栈": "Java 17"}})

    text = backend.files[PATH]
    assert "认证体系" not in text
    assert "服务间鉴权" not in text
    assert "清掉 2 条非白名单字段" in out


async def test_gaps_replace_the_whole_section():
    backend = _FakeBackend()
    backend.files[PATH] = BASE

    await _call(
        _tool(backend),
        "gaps",
        {"items": [{"module": "支付", "text": "对账未实现"}]},
    )

    text = backend.files[PATH]
    assert "**支付**：对账未实现" in text
    assert "退款未实现" not in text, "整节替换：过时的缺口要消失"


async def test_decisions_need_all_three_columns():
    backend = _FakeBackend()
    backend.files[PATH] = BASE

    out = await _call(
        _tool(backend),
        "decisions",
        {"items": [{"decision": "不引入新依赖", "reason": "离线"}]},
    )

    assert "impact" in out
    assert backend.aedits == []


async def test_too_many_decisions_is_rejected():
    backend = _FakeBackend()
    backend.files[PATH] = BASE
    items = [
        {"decision": f"约束{i}", "reason": "原因", "impact": "影响"} for i in range(9)
    ]

    out = await _call(_tool(backend), "decisions", {"items": items})

    assert "最多 8 条" in out
    assert backend.aedits == []


async def test_an_unknown_section_is_rejected():
    backend = _FakeBackend()

    out = await _call(_tool(backend), "full_text", {"content": "整篇"})

    assert "section 只能是" in out
    assert backend.awrites == []


async def test_an_overlong_field_is_rejected_instead_of_truncated():
    backend = _FakeBackend()
    backend.files[PATH] = BASE

    out = await _call(_tool(backend), "basic", {"fields": {"技术栈": "字" * 301}})

    assert "超过 300 字" in out
    assert backend.aedits == [], "超长就整次不写，不做静默截断"


async def test_an_oversized_section_is_rejected_with_its_size():
    backend = _FakeBackend()
    backend.files[PATH] = BASE
    items = [{"module": f"模块{i}", "text": "字" * 200} for i in range(10)]

    out = await _call(_tool(backend), "gaps", {"items": items})

    assert "超出上限" in out and "当前缺口" in out
    assert backend.aedits == []


async def test_legacy_tables_are_dropped_on_write():
    backend = _FakeBackend()
    backend.files[PATH] = BASE + "\n## 五、功能列表\n| 功能 | 状态 |\n"

    await _call(_tool(backend), "gaps", {"items": [{"module": "订单", "text": "退款"}]})

    assert "功能列表" not in backend.files[PATH]


# -- 描述与路径 ----------------------------------------------------------------


def test_path_comes_from_the_shared_helper():
    assert memory_path("gitee", "acme", "demo") == PATH


def test_the_tool_description_documents_the_sections():
    """schema 只有 section + payload，写法必须写在描述里，否则模型只能猜。"""
    tool = _tool(_FakeBackend())
    assert "按节" in tool.description
    for section in ("change", "gaps", "decisions", "basic"):
        assert f'section="{section}"' in tool.description
    assert "整篇" in tool.description
