"""``save_repo_memory`` 的行为守卫。

三件最容易出错的事：

1. 路径口径 —— 路径由 ``backends.composite.memory_path`` 统一算（读写两侧同一口径）。
   历史上两侧各拼过一份，下划线 vs 斜杠对不上时写入成功但没人读得到。
2. 覆盖写 —— 后端 ``StoreBackend.awrite`` 只在文件不存在时新建，已存在一律报
   ``already exists`` 拒绝。工具若只调 ``awrite``，记忆写一次就再也改不动（实测：
   一轮里 4 次写入只有第 1 次落库）。故已存在的文件必须走 ``aedit`` 全文替换。
3. 落盘前的 PR 检查与打底 —— **有要核对的 PR 时**查不到就**整篇不写**（既不猜「没有」，
   也不猜「有」）；查到了就按它重算文件头的「最近 PR」、把「变更记录」里写着「待合并」的
   状态改成实际结论，并摘掉老模板遗留的两张表（需求列表 / 功能列表）。**没有要核对的 PR
   时（都写着已合并 / 已关闭，或压根没有 PR 行）这一次查询整段省掉** —— 那时要写的本来
   就是与 PR 状态无关的内容（2026-09-26 用户口径）。

还有一条与 (3) 同源、但作用在写盘的最后一刻：**文件头的时间戳由平台盖**。模型提交的
正文里必然带着头行（模版里就有），填的是它自己猜的时刻 —— 那个不进盘；而「只差这一格」
也不算内容变，否则「没变就不写盘」会被这一次盖章毁掉（见 ``TestTheWriteStampsItsOwnTime``）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_coding_harness.agent.memory_reconcile import restamp
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.config import Settings
from ai_coding_harness.tools import memory as memory_mod
from ai_coding_harness.tools import register as register_mod
from ai_coding_harness.tools.git import GiteeRepo, PrLookupError
from ai_coding_harness.tools.register import build_main_tools

REPO = GiteeRepo(owner="acme", repo="demo", token="test-token")
PATH = "/memories/acme/demo.md"

#: 钉住的写盘时刻。落盘时的时间戳是平台现取的，测试要能预期它 —— 否则断言里只剩
#: 「除了时间大概都对」这种废话。
STAMP = "2026-09-23 09:41"

# 一份**文件头已经对齐**的完整记忆（没有 PR 时「最近 PR」写 ``—``，与对账器算出来的一致）：
# 对账器对它是恒等变换（那条「待合并」的 PR 行在 PR 列表为空时也不会被改写）。写盘机制
# 那几条用例因此只测写盘本身；头行重算与状态回填另有一组用例。
MEMORY = """# 记忆

> 最后更新：2026-09-20 08:00　｜　最近 PR：—

## 一、项目概览
- **技术栈**：Java 21

## 二、变更记录（最新在上）
### 2026-09-20 · 新增 · 商品列表分页
- **涉及文件**：`api/goods.py`
- **PR**：[#12](https://gitee.com/acme/demo/pulls/12)　｜　**状态**：待合并
"""


def _pr(
    number: int, *, state: str = "open", title: str = "改点东西", branch: str = "feat"
) -> dict:
    return {
        "number": number,
        "state": state,
        "title": title,
        "html_url": f"https://gitee.com/acme/demo/pulls/{number}",
        "user": {"login": "alice"},
        "head": {"ref": branch},
        "base": {"ref": "master"},
    }

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


class _FakeGitee:
    """假 Gitee 客户端：把「查本仓库 PR」这一步从网络里摘出来。"""

    def __init__(self) -> None:
        self.pulls: list[dict] = []
        self.error: PrLookupError | None = None
        self.calls = 0

    async def all_pulls(self) -> list[dict]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.pulls


@pytest.fixture(autouse=True)
def gitee(monkeypatch) -> _FakeGitee:
    """装配点建的 PR 客户端全部换成假的 —— 真客户端会让每个用例都去请求平台 API。"""
    fake = _FakeGitee()
    monkeypatch.setattr(register_mod, "build_git_client", lambda *_args, **_kwargs: fake)
    return fake


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> str:
    """钉住时钟：写盘时盖的那个时间戳由平台现取，不钉住就断言不了。"""
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


async def test_first_write_creates_the_file(clock):
    backend = _FakeBackend()

    out = await _tool(backend).ainvoke({"content": MEMORY})

    # 整篇写入，不做任何拼接 —— 合并是模型的活；唯一被动的只有文件头那个时间戳
    assert backend.awrites == [(PATH, restamp(MEMORY, now=clock))]
    assert backend.files[PATH] == restamp(MEMORY, now=clock)
    assert "已创建" in out and PATH in out


async def test_path_follows_the_backend_route_prefix():
    """前缀取自 ``settings.memories_prefix``，否则改了路由就写进黑洞。"""
    backend = _FakeBackend()

    out = await _tool(backend, Settings(memories_prefix="/memo/")).ainvoke(
        {"content": MEMORY}
    )

    assert backend.awrites[0][0] == "/memo/acme/demo.md"
    assert "/memo/acme/demo.md" in out


def test_path_comes_from_the_shared_helper():
    """工具不再自己拼路径 —— 与 server 读记忆共用 ``memory_path``。"""
    assert memory_path("acme", "demo") == PATH
    assert memory_path("acme", "demo", "/memo/") == "/memo/acme/demo.md"


async def test_existing_memory_is_replaced_through_edit(clock):
    """已有记忆走 ``aedit`` 全文替换，且 old_string 是当前全文。"""
    backend = _FakeBackend()
    backend.files[PATH] = "旧内容"

    out = await _tool(backend).ainvoke({"content": MEMORY})

    stamped = restamp(MEMORY, now=clock)
    assert backend.awrites == [], "对已存在的文件调 awrite 只会拿到 already exists"
    assert backend.aedits == [(PATH, "旧内容", stamped)]
    assert backend.files[PATH] == stamped
    assert "已更新" in out


async def test_memory_can_be_written_more_than_once():
    """回归守卫：记忆必须能逐轮覆盖（旧实现第二次起全部失败）。"""
    backend = _FakeBackend()
    tool = _tool(backend)

    await tool.ainvoke({"content": MEMORY.replace("Java 21", "Java 17")})
    await tool.ainvoke({"content": MEMORY.replace("Java 21", "Java 21")})
    out = await tool.ainvoke({"content": MEMORY.replace("Java 21", "Kotlin 2")})

    assert "Kotlin 2" in backend.files[PATH]
    assert "失败" not in out


async def test_identical_content_is_not_written_again(clock):
    """内容没变就别写盘 —— 也让模型收到「无需重试」的明确反馈。

    盘上那份带的是上一轮盖的时间戳，与本轮必然不同；「没变」是挖掉时间戳之后比的。
    """
    backend = _FakeBackend()
    backend.files[PATH] = restamp(MEMORY, now="2026-09-20 08:00")

    out = await _tool(backend).ainvoke({"content": MEMORY})

    assert backend.awrites == [] and backend.aedits == []
    assert "一致" in out


async def test_create_failure_is_reported_not_swallowed():
    backend = _FakeBackend(write_error="backend unavailable")

    out = await _tool(backend).ainvoke({"content": MEMORY})

    assert "失败" in out
    assert "backend unavailable" in out


async def test_edit_failure_is_reported_not_swallowed():
    backend = _FakeBackend(edit_error=f"old_string not found in {PATH}")
    backend.files[PATH] = "旧内容"

    out = await _tool(backend).ainvoke({"content": MEMORY})

    assert "失败" in out
    assert "old_string not found" in out
    assert backend.files[PATH] == "旧内容", "失败时不能悄悄改掉已有记忆"


async def test_unreadable_memory_is_not_overwritten_blindly():
    """读不到现有记忆时不猜：文件其实存在的话，新建这条路会失败并如实报告。"""
    backend = _FakeBackend(read_error="backend unavailable")
    backend.files[PATH] = "现有记忆"

    out = await _tool(backend).ainvoke({"content": MEMORY})

    assert "失败" in out
    assert backend.files[PATH] == "现有记忆"


# ---------------------------------------------------------------------------
# 落盘前的 PR 检查与打底
# ---------------------------------------------------------------------------


async def test_lookup_runs_once_before_writing(gitee):
    """一次查询同时喂给「能不能写」与「需求表回填什么」，不查两遍。"""
    backend = _FakeBackend()

    out = await _tool(backend).ainvoke({"content": MEMORY})

    assert gitee.calls == 1
    assert "已创建" in out


async def test_a_memory_with_nothing_to_verify_skips_the_lookup(gitee):
    """记忆里的 PR 都写着结局（已合并 / 已关闭）→ 这一轮连 PR 列表都不取。

    省下的是一次 Gitee 往返；代价是文件头的「最近 PR」保持原样 —— 没查就不知道新值，
    所以那一格不动，而不是写成「—」。
    """
    settled = MEMORY.replace("**状态**：待合并", "**状态**：已合并")
    backend = _FakeBackend()
    backend.files[PATH] = restamp(settled, now="2026-09-20 08:00")

    out = await _tool(backend).ainvoke({"content": settled})

    assert gitee.calls == 0, "没有要核对的 PR 就不该发这次查询"
    assert "一致" in out
    assert backend.files[PATH].startswith("# 记忆")


async def test_a_memory_without_pr_rows_skips_the_lookup_and_still_writes(gitee):
    """没有 PR 行的记忆：不查 Gitee，但照写、照盖时间戳。"""
    plain = MEMORY.replace(
        "- **PR**：[#12](https://gitee.com/acme/demo/pulls/12)　｜　**状态**：待合并\n", ""
    )
    backend = _FakeBackend()

    out = await _tool(backend).ainvoke({"content": plain})

    assert gitee.calls == 0
    assert "已创建" in out
    assert f"> 最后更新：{STAMP}" in backend.files[PATH]


@pytest.mark.parametrize(
    ("live", "expected"),
    [("merged", "已合并"), ("closed", "已关闭"), ("rejected", "已关闭")],
)
async def test_the_recorded_status_follows_the_live_pull(gitee, live, expected):
    """线上已经合入 / 已经关闭 → 记忆里那格从「待合并」改成实际结论（2026-09-26 用户口径）。"""
    gitee.pulls = [_pr(12, state=live, title="加个接口")]
    backend = _FakeBackend()

    out = await _tool(backend).ainvoke({"content": MEMORY})
    stored = backend.files[PATH]

    assert f"**状态**：{expected}" in stored
    assert "**状态**：待合并" not in stored
    assert "条变更记录的状态已按实际结果更新" in out, "改了这一格，回执要说一声"
    assert "涉及文件" in stored, "只动状态那一格，同行其余字段照留"


async def test_an_open_pull_keeps_the_recorded_status(gitee):
    """还开着的 PR：状态照旧写「待合并」，回执里也不该提「状态已更新」。"""
    gitee.pulls = [_pr(12, title="加个接口")]
    backend = _FakeBackend()

    out = await _tool(backend).ainvoke({"content": MEMORY})

    assert "**状态**：待合并" in backend.files[PATH]
    assert "条变更记录的状态" not in out


async def test_open_prs_do_not_block_the_write(gitee):
    """有未合入的 PR 不拒写：编码结束那一刻必然存在刚建好的 PR，拒写等于这一轮白干。"""
    gitee.pulls = [_pr(12, title="加个接口", branch="feat/x")]
    backend = _FakeBackend()

    out = await _tool(backend).ainvoke({"content": MEMORY})

    assert "已创建" in out
    assert backend.files[PATH] != ""


async def test_a_pull_never_adds_a_table_row(gitee):
    """平台不再往记忆里写任何表：刚建好的 PR 只体现在文件头的「最近 PR」上。"""
    gitee.pulls = [_pr(12, title="加个接口")]

    backend = _FakeBackend()
    await _tool(backend).ainvoke({"content": MEMORY})
    stored = backend.files[PATH]

    assert [line for line in stored.splitlines() if line.startswith("|")] == []
    assert "加个接口" not in stored, "PR 标题不再进记忆"
    assert "最近 PR：[#12](https://gitee.com/acme/demo/pulls/12)" in stored


async def test_the_legacy_tables_are_dropped_on_write(gitee):
    """模型循着老模版把两张表写回来 —— 平台摘掉，散文保留。"""
    gitee.pulls = [_pr(12, title="加个接口")]
    legacy = MEMORY.replace(
        "## 二、变更记录（最新在上）",
        "## 二、需求列表\n\n| 需求 | PR | 分支 | 状态 | 更新于 |\n|---|---|---|---|---|\n"
        "| 加个接口 | [#12](https://gitee.com/acme/demo/pulls/12) | feat | 待合入 | 2026-09-20 08:00 |\n\n"
        "## 三、功能列表\n\n| 功能 | 状态 | 相关 PR | 说明 |\n|---|---|---|---|\n"
        "| 列表分页 | 已具备 | #12 | 每页 20 条 |\n\n"
        "## 二、变更记录（最新在上）",
    )

    backend = _FakeBackend()
    out = await _tool(backend).ainvoke({"content": legacy})
    stored = backend.files[PATH]

    assert "需求列表" not in stored and "功能列表" not in stored
    assert [line for line in stored.splitlines() if line.startswith("|")] == []
    assert "- **技术栈**：Java 21" in stored, "散文要留住"
    assert "盖章" in out, "改动了就要说一声，模型才知道别照着旧版再写一遍"


async def test_lookup_failure_also_blocks_the_write(gitee):
    """查不出来同样不写，且**不许**把「没查成」说成「有 PR」—— 那是拿猜测冒充事实。"""
    gitee.error = PrLookupError("HTTP 401 {'message': 'token 未配置'}")
    backend = _FakeBackend()

    out = await _tool(backend).ainvoke({"content": MEMORY})

    assert "未能确认" in out
    assert "token 未配置" in out, "原因要原样透传，用户才知道修什么"
    assert "仓库有未关闭的 PR" not in out
    assert backend.awrites == [] and backend.aedits == []
    assert backend.areads == [], "检查在前：被拦下时连现有记忆都不该读"


class TestTheWriteStampsItsOwnTime:
    """「最后更新」由平台在写盘那一刻盖章 —— 模型自己写的时间不进盘。

    让它自己写就等于把「最后一次更新」交给一个没有钟（或者只有一个猜出来的钟）的角色；
    下一轮再看到这个文件时，那一行到底是这轮盖的还是幻觉里的时刻，谁也说不清。
    """

    async def test_a_model_written_time_does_not_land(self, clock):
        """盖的是「最后更新」那一格；模版里写着它、模型每轮都会带一句自己猜的时刻。"""
        forged = MEMORY.replace(
            "> 最后更新：2026-09-20 08:00", "> 最后更新：2026-02-12 03:07"
        )
        backend = _FakeBackend()

        await _tool(backend).ainvoke({"content": forged})
        stored = backend.files[PATH]

        assert "> 最后更新：2026-02-12 03:07" not in stored, "模型编的时刻不该落盘"
        assert f"> 最后更新：{clock}" in stored

    async def test_the_header_is_rebuilt_from_the_platform_lookup(self, clock, gitee):
        """盖章只换时间，不改别的：头行的「最近 PR」是上面按 PR 列表算的，模型那句覆盖不了。"""
        gitee.pulls = [_pr(12, title="加个接口")]
        backend = _FakeBackend()

        await _tool(backend).ainvoke({"content": MEMORY})
        head = next(
            line
            for line in backend.files[PATH].splitlines()
            if line.startswith("> 最后更新")
        )

        assert head == (
            f"> 最后更新：{clock}　｜　最近 PR：[#12](https://gitee.com/acme/demo/pulls/12)"
        )

    async def test_a_stale_latest_pr_label_is_recomputed(self, clock, gitee):
        """模型把头行的「最近 PR」留在上一轮的编号上 —— 权威列表里没有它，就落回 ``—``。"""
        stale = MEMORY.replace(
            "最近 PR：—", "最近 PR：[#9](https://gitee.com/acme/demo/pulls/9)"
        )
        backend = _FakeBackend()

        out = await _tool(backend).ainvoke({"content": stale})
        stored = backend.files[PATH]

        assert "最近 PR：—" in stored
        assert "#9" not in stored
        assert "盖章" in out, "头行被改了，回执要说一声"


async def test_the_stamping_also_applies_without_a_lookup(clock):
    """没接 PR 查询（脚本 / 单测直接建工具）时同样盖时间戳，但**不能说**回填过。"""
    from ai_coding_harness.tools.memory import MemoryContext, build_memory_tools

    backend = _FakeBackend()
    memory_tool = build_memory_tools(
        MemoryContext(owner="acme", repo="demo", backend=backend)
    )[0]

    assert memory_tool.name == "save_repo_memory"
    assert "盖章" in memory_tool.description

    out = await memory_tool.ainvoke({"content": MEMORY})

    assert backend.files[PATH] == restamp(MEMORY, now=clock)
    assert "盖章" not in out, "没查 Gitee 就没有盖章这回事（头行的 PR 栏也没动）"


def test_the_tool_description_documents_the_header_rule():
    """描述离模型最近：调用前就该知道头行归平台、两张老表会被摘掉。"""
    description = _tool(_FakeBackend()).description

    assert "盖章" in description
    assert "需求列表" in description and "功能列表" in description
    assert "查不到" in description
    assert "整篇都不会落盘" in description
