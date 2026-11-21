"""仓库记忆最终工具：读取一份文档、写入一份语义文档。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_coding_harness.agent.profiles import ORCHESTRATE_FORM, PLAN_FORM, QA_FORM
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.config import Settings
from ai_coding_harness.tools import memory as memory_mod
from ai_coding_harness.tools.git import GiteeRepo
from ai_coding_harness.tools.register import build_main_tools

REPO = GiteeRepo(owner="acme", repo="demo", token="test-token")
INDEX = "/memories/gitee/acme/demo/index.md"
ROOT = "/memories/gitee/acme/demo"
DIRECTORY = f"{ROOT}/architecture/directory.md"
OVERVIEW = f"{ROOT}/architecture/overview.md"
FEATURE = f"{ROOT}/features/payment-refund.md"
DOMAIN = f"{ROOT}/domains/payment.md"
RELATED = f"{ROOT}/domains/notification.md"
GAPS = f"{ROOT}/working/gaps.md"
RISKS = f"{ROOT}/working/risks.md"
STAMP = "2026-11-19 10:30"


def test_default_index_path_is_nested_under_the_repository() -> None:
    assert memory_path("gitee", "acme", "demo") == (
        "/memories/gitee/acme/demo/index.md"
    )


class _FakeBackend:
    """StoreBackend 语义替身：awrite 只新建，aedit 才更新。"""

    def __init__(self, *, write_error: str | None = None):
        self.files: dict[str, str] = {}
        self.writes: list[str] = []
        self.edits: list[str] = []
        self.write_error = write_error

    async def aread(self, path: str) -> SimpleNamespace:
        if path not in self.files:
            return SimpleNamespace(error="not found", file_data=None)
        return SimpleNamespace(error=None, file_data={"content": self.files[path]})

    async def awrite(self, path: str, content: str) -> SimpleNamespace:
        self.writes.append(path)
        if self.write_error:
            return SimpleNamespace(error=self.write_error, path=None)
        if path in self.files:
            return SimpleNamespace(error="already exists", path=None)
        self.files[path] = content
        return SimpleNamespace(error=None, path=path)

    async def aedit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> SimpleNamespace:
        del replace_all
        self.edits.append(path)
        if self.files.get(path) != old_string:
            return SimpleNamespace(error="old_string not found", path=None)
        self.files[path] = new_string
        return SimpleNamespace(error=None, path=path)


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> str:
    monkeypatch.setattr(memory_mod, "now_stamp", lambda: STAMP)
    return STAMP


def _tools(backend: _FakeBackend, form: str = ORCHESTRATE_FORM):
    return build_main_tools(
        Settings(_env_file=None),
        SimpleNamespace(name="web_search_tool"),  # type: ignore[arg-type]
        SimpleNamespace(name="fetch_url_tool"),  # type: ignore[arg-type]
        REPO,
        backend,  # type: ignore[arg-type]
        form=form,
    )


def _tool(backend: _FakeBackend, name: str, form: str = ORCHESTRATE_FORM):
    return next(tool for tool in _tools(backend, form) if tool.name == name)


async def _save(backend: _FakeBackend, form: str = ORCHESTRATE_FORM, **payload) -> str:
    return await _tool(backend, "save_repo_memory", form).ainvoke(payload)


async def test_profile_updates_the_small_main_index(clock):
    backend = _FakeBackend()

    out = await _save(
        backend,
        kind="profile",
        content=(
            "- **项目地址**：https://gitee.com/acme/demo.git\n"
            "- **技术栈**：FastAPI + Vue 3\n"
            "- **启动方式**：docker compose up"
        ),
    )

    assert "仓库基本信息已更新" in out
    assert f"> 最后更新：{STAMP}" in backend.files[INDEX]
    assert "**技术栈**：FastAPI + Vue 3" in backend.files[INDEX]
    assert "## 二、目录与模块" in backend.files[INDEX]
    assert "## 三、架构概览" in backend.files[INDEX]
    assert "## 四、记忆目录" in backend.files[INDEX]


async def test_directory_is_a_document_and_is_mirrored_into_the_index():
    backend = _FakeBackend()
    content = (
        "## 目录导航\n"
        "- **入口**：`src/main.py` — 应用启动\n"
        "- **支付模块**：`src/payment` — 支付与退款\n"
    )

    out = await _save(
        backend,
        kind="directory",
        title="目录与模块",
        content=content,
        summary="应用入口、支付模块与测试目录",
    )

    assert "`architecture/directory.md`" in out
    assert content.strip() in backend.files[DIRECTORY]
    assert "`src/payment`" in backend.files[INDEX]
    assert "`architecture/directory.md`" in backend.files[INDEX]


async def test_architecture_overview_updates_its_own_document_and_index():
    backend = _FakeBackend()
    graph = "```mermaid\nflowchart LR\n  UI --> API --> DB\n```"

    await _save(
        backend,
        kind="architecture",
        slug="overview",
        title="整体架构",
        content=graph,
        summary="前端、API 与数据库",
        keywords=["architecture", "架构"],
    )

    assert "flowchart LR" in backend.files[OVERVIEW]
    assert "flowchart LR" in backend.files[INDEX]
    assert "`architecture/overview.md`" in backend.files[INDEX]


async def test_feature_requires_domain_and_updates_domain_index():
    backend = _FakeBackend()
    content = (
        "## 功能职责\n处理支付退款。\n\n"
        "## 主流程图\n```mermaid\nflowchart LR\n  Refund --> Provider\n```"
    )

    missing = await _save(
        backend,
        kind="feature",
        slug="payment-refund",
        title="支付退款",
        content=content,
    )
    assert "必须提供主要 domain" in missing

    out = await _save(
        backend,
        kind="feature",
        slug="payment-refund",
        title="支付退款",
        content=content,
        summary="退款申请、渠道调用与补偿",
        domain="payment",
        related_domains=["notification"],
        keywords=["refund", "退款"],
        source_paths=["src/payment/refund.py", "tests/test_refund.py"],
        verified_commit="abc123",
    )

    assert "`features/payment-refund.md`" in out
    feature = backend.files[FEATURE]
    assert "flowchart LR" in feature
    assert "abc123" in feature
    assert "主要领域：payment" in feature
    assert "`features/payment-refund.md`" in backend.files[DOMAIN]
    assert "## 功能索引" in backend.files[DOMAIN]
    assert "`features/payment-refund.md`" in backend.files[RELATED]
    assert "## 关联功能" in backend.files[RELATED]
    assert "`domains/payment.md`" in backend.files[INDEX]
    assert "`features/payment-refund.md`" in backend.files[INDEX]


async def test_read_tool_loads_one_document_from_the_index():
    backend = _FakeBackend()
    await _save(
        backend,
        kind="feature",
        slug="payment-refund",
        title="支付退款",
        content="## 时序图\n```mermaid\nsequenceDiagram\n  User->>API: refund\n```",
        summary="退款",
        domain="payment",
    )

    text = await _tool(backend, "read_repo_memory").ainvoke({"path": "features/payment-refund.md"})
    missing = await _tool(backend, "read_repo_memory").ainvoke({"path": "features/not-found.md"})

    assert "sequenceDiagram" in text
    assert "文档不存在" in missing


async def test_new_memory_pack_creates_gaps_and_risks_skeletons(clock):
    backend = _FakeBackend()

    index = await _tool(backend, "read_repo_memory").ainvoke({"path": "index"})

    assert "`working/gaps.md`" in index
    assert "`working/risks.md`" in index
    assert GAPS in backend.files
    assert RISKS in backend.files
    assert "## 风险登记" in backend.files[RISKS]


async def test_qa_can_write_verified_gaps_and_risks_but_not_decisions():
    backend = _FakeBackend()

    feature = await _save(
        backend,
        form=QA_FORM,
        kind="feature",
        slug="payment-refund",
        title="支付退款",
        content="## 功能职责\n退款分析。",
        domain="payment",
    )
    gaps = await _save(
        backend,
        form=QA_FORM,
        kind="gaps",
        content=(
            "## 缺口登记\n"
            "- **支付**：退款回调尚未实现。\n"
            "  - **状态**：未开始\n"
        ),
        source_paths=["src/payment/refund.py"],
    )
    risks = await _save(
        backend,
        form=QA_FORM,
        kind="risks",
        content=(
            "## 风险登记\n"
            "- **重复退款**：回调重试可能重复入账。\n"
            "  - **严重度**：高\n"
        ),
        verified_commit="abc123",
    )
    decision = await _save(
        backend,
        form=QA_FORM,
        kind="decision",
        slug="no-refund-retry",
        content="## 约束\n禁止无幂等键重试。",
    )

    assert "features/payment-refund.md" in feature
    assert "`working/gaps.md`" in gaps
    assert "`working/risks.md`" in risks
    assert "退款回调尚未实现" in backend.files[GAPS]
    assert "重复退款" in backend.files[RISKS]
    assert "当前角色不能写 kind=decision" in decision


async def test_plan_has_only_the_read_tool_and_does_not_migrate(clock):
    backend = _FakeBackend()
    legacy = """# 仓库记忆：acme/demo

> 最后更新：2026-11-18 08:00　｜　最近 PR：—

## 一、仓库基本信息
- **技术栈**：Python

## 二、变更记录（最新在上）
### 2026-11-18 · 修改 · 旧改动
- **PR**：#1　｜　**状态**：已合并

## 三、当前缺口（按模块）

## 四、长期约束
"""
    backend.files[INDEX] = legacy
    names = [tool.name for tool in _tools(backend, PLAN_FORM)]
    assert "read_repo_memory" in names
    assert "save_repo_memory" not in names

    text = await _tool(backend, "read_repo_memory", PLAN_FORM).ainvoke({"path": "index"})

    assert "## 二、目录与模块" in text
    assert backend.files[INDEX] == legacy
    assert backend.writes == []
    assert backend.edits == []


async def test_gaps_and_risks_use_separate_documents_and_enter_the_catalog():
    backend = _FakeBackend()
    gap_out = await _save(
        backend,
        kind="gaps",
        content="- **退款**：渠道补偿尚未实现\n- **对账**：缺少定时任务",
        summary="退款补偿与对账缺口",
        source_paths=["src/payment/refund.py"],
    )
    risk_out = await _save(
        backend,
        kind="risks",
        content=(
            "## 风险登记\n"
            "- **并发覆盖**：同一退款单可能被两个 worker 同时处理。\n"
            "  - **严重度**：高\n"
            "  - **触发条件**：重复消费消息\n"
            "  - **影响**：重复打款\n"
            "  - **状态**：待处理\n"
            "  - **证据**：`src/payment/refund.py`"
        ),
        summary="退款并发风险",
        keywords=["refund", "concurrency"],
        source_paths=["src/payment/refund.py"],
        verified_commit="abc123",
    )

    assert "`working/gaps.md`" in gap_out
    assert "`working/risks.md`" in risk_out
    assert "退款" in backend.files[GAPS]
    assert "对账" in backend.files[GAPS]
    assert "并发覆盖" in backend.files[RISKS]
    assert "严重度**：高" in backend.files[RISKS]
    assert "触发条件**：重复消费消息" in backend.files[RISKS]
    assert "abc123" in backend.files[RISKS]
    assert "`working/gaps.md`" in backend.files[INDEX]
    assert "`working/risks.md`" in backend.files[INDEX]
    assert "退款并发风险" in backend.files[INDEX]


async def test_gaps_and_risks_require_code_evidence():
    backend = _FakeBackend()

    gap = await _save(
        backend,
        kind="gaps",
        content="- **退款**：补偿缺失",
    )
    risk = await _save(
        backend,
        kind="risks",
        content="- **并发**：可能重复退款",
    )

    assert "gaps 至少需要一项代码证据" in gap
    assert "risks 至少需要一项代码证据" in risk


async def test_invalid_profile_field_and_storage_error_are_returned_as_text():
    backend = _FakeBackend()
    invalid = await _save(
        backend,
        kind="profile",
        content="- **模块列表**：不允许",
    )
    assert "profile 不支持的字段" in invalid

    failing = _FakeBackend(write_error="存储挂了")
    error = await _save(
        failing,
        kind="gaps",
        content="- 退款未完成",
        source_paths=["src/payment/refund.py"],
    )
    assert "写入仓库记忆失败" in error
    assert "存储挂了" in error


async def test_no_search_tool_is_exposed():
    names = [tool.name for tool in _tools(_FakeBackend())]
    assert "search_repo_memory" not in names
    assert "save_repo_memory_document" not in names
