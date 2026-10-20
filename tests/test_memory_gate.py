"""仓库记忆中间件：``before_agent`` 里查 PR、刷新记忆（有未合入的就拦下本轮），
并且把记忆全文注入系统提示。

五件必须钉住的事：

1. **拦得住。** 有未合入的 PR 就抛 ``TurnStop``，且 marker 自己就说得清（它原样进 SSE
   的 ``ai`` 帧与记录正文，用户看不到别的上下文）。
2. **拦之前先刷记忆。** 刷新与拦不拦是两个决定：记忆按事实刷新了，用户看到的 marker
   才和盘上的文件一致；先抛后刷就会出现「说要合入 #31，可记忆里 #31 还是已合入」。
3. **查不到不是没有。** ``PrLookupError`` 一样拦，但**一个字节都不写**。
4. **记忆进系统提示，不用模型自己读。** 每个形态、每个智能体都挂：闸门那条路注入的是
   **刷新后**的文本（写盘失败时盘上是旧的、注入的仍是真的），只读那条路只读一次盘；
   仓库从没写过记忆就是不注入（而不是注入一个空块）。
5. **抛得出图。** 这是第一个从 ``before_agent`` 抛的 ``TurnStop``（此前只从
   ``wrap_tool_call`` 抛过）。``service.py`` 的 ``except TurnStop`` 包的是
   ``astream_events``，所以异常必须**原样**穿出来 —— 被 LangGraph 包一层就变成
   「本轮故障」而不是「本轮闸门」，用户看到的文案也就全错了。最后两条用例拿真图跑。
6. **没事就不查**（2026-09-26 用户口径）。记忆里没有要核对的 PR（都写着已合并 / 已关闭，
   或压根没有 PR 行）时，``all_prs`` 一次都不该被调用；写着「待合并」而线上已经合入的，
   那一格要按实况改掉，且这一轮**不拦**。只有从没写过记忆的仓库仍然查一次。

全部用合成 PR 数据；不碰网络（``all_prs`` 是注入的假函数）。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from pydantic import Field

from ai_coding_harness.agent.graph import build_agent
from ai_coding_harness.agent.middleware import (
    BLIND_MARKER,
    BLOCKING_FORMS,
    GATE_ABORT_REASON,
    GATE_MARKER,
    MEMORY_BLOCK_TITLE,
    NO_TARGET_ABORT_REASON,
    NOTHING_TO_FIX_MARKER,
    REFRESH_FORMS,
    MemoryMiddleware,
    TurnStop,
    build_memory_middleware,
)
from ai_coding_harness.agent.profiles import (
    ORCHESTRATE_FORM,
    PLAN_FORM,
)
from ai_coding_harness.agent.subagents import build_subagents
from ai_coding_harness.backends.composite import memory_path
from ai_coding_harness.config import Settings
from ai_coding_harness.core.codeplatform import Platform, RepoSpec
from ai_coding_harness.safety.policy import policy_for
from ai_coding_harness.server import service as service_mod
from ai_coding_harness.server.intent import IntentDecision
from ai_coding_harness.server.service import HarnessService
from ai_coding_harness.tools.git import GiteeRepo, PrLookupError
from tests.v3_events import FakeAgent

REPO = GiteeRepo(owner="acme", repo="demo", token="test-token")
PATH = "/memories/acme/demo.md"

# 一份「文件头过期」的记忆：头行里没记过 PR（``最近 PR：—``），而 Gitee 上 #12 还开着 ——
# 对账器该把最近 PR 补上，闸门该因此拦下本轮。内容里**没有表**（两张老表都被摘掉了）。
STALE = """# 仓库记忆：acme/demo

> 最后更新：2026-09-20 08:00　｜　最近 PR：—

## 一、项目概览
- **技术栈**：Java 21

## 二、变更记录（最新在上）
### 2026-09-20 · 新增 · 商品列表分页
- **PR**：[#12](https://gitee.com/acme/demo/pulls/12)
"""

LINK_12 = "[#12](https://gitee.com/acme/demo/pulls/12)"


def with_status(text: str, status: str) -> str:
    """把 STALE 里那条 PR 记录补上状态那一格（老格式里没有它）。"""
    return text.replace(
        f"- **PR**：{LINK_12}", f"- **PR**：{LINK_12}　｜　**状态**：{status}"
    )


def pull(number: int, *, state: str = "open", title: str = "改点东西") -> dict:
    return {
        "number": number,
        "state": state,
        "title": title,
        "html_url": f"https://gitee.com/acme/demo/pulls/{number}",
        "user": {"login": "alice"},
        "head": {"ref": f"feat/{number}"},
        "base": {"ref": "master"},
    }


class _Backend:
    """内存后端，语义对齐 ``StoreBackend``：``awrite`` 只新建、``aedit`` 才替换。"""

    def __init__(self, content: str | None = None) -> None:
        self.files: dict[str, str] = {} if content is None else {PATH: content}
        self.awrites: list[str] = []
        self.aedits: list[tuple[str, str]] = []

    async def aread(self, path: str) -> SimpleNamespace:
        if path not in self.files:
            return SimpleNamespace(error=f"File '{path}' not found", file_data=None)
        return SimpleNamespace(error=None, file_data={"content": self.files[path]})

    async def awrite(self, path: str, content: str) -> SimpleNamespace:
        self.awrites.append(path)
        if path in self.files:
            return SimpleNamespace(error=f"{path} already exists", path=None)
        self.files[path] = content
        return SimpleNamespace(error=None, path=path)

    async def aedit(self, path: str, old: str, new: str) -> SimpleNamespace:
        self.aedits.append((old, new))
        if self.files.get(path) != old:
            return SimpleNamespace(error="old_string not found", path=None)
        self.files[path] = new
        return SimpleNamespace(error=None, path=path)


def _prs(*pulls: dict, error: PrLookupError | None = None):
    """``all_prs`` 的桩：给出这批 PR，或一律抛 ``PrLookupError``。"""

    async def all_prs() -> list[dict]:
        if error is not None:
            raise error
        return list(pulls)

    return all_prs


def _gate(backend: Any, *pulls: dict, error: PrLookupError | None = None):
    """**会拦**的那条路（plan / orchestrate）：查 PR → 对账 → 有 open PR 就拦。"""
    return MemoryMiddleware(
        backend=backend, path=PATH, all_prs=_prs(*pulls, error=error), blocking=True
    )


def _gate_for(
    platform: Platform, backend: Any, *pulls: dict, error: PrLookupError | None = None
) -> MemoryMiddleware:
    """同上，但把仓库所在平台显式指定 —— 用来验文案里的平台名跟着仓库走。"""
    repo = RepoSpec(owner="acme", repo="demo", token="test-token", platform=platform)
    return MemoryMiddleware(
        backend=backend,
        path=PATH,
        all_prs=_prs(*pulls, error=error),
        blocking=True,
        platform=repo,
    )


def _refresh_only(backend: Any, *pulls: dict):
    """只刷新不拦的那条路（qa / analysis / review）：同一套对账，但不下闸。"""
    return MemoryMiddleware(
        backend=backend, path=PATH, all_prs=_prs(*pulls), blocking=False
    )


def _injector(backend: Any) -> MemoryMiddleware:
    """只读那条路（不带 ``all_prs``）：qa / analysis / review 主控与三个子智能体挂的就是它。"""
    return MemoryMiddleware(backend=backend, path=PATH)


class _Request:
    """``ModelRequest`` 的最小替身：只用到 ``system_message`` 与 ``override``。"""

    def __init__(self, system_message: SystemMessage) -> None:
        self.system_message = system_message

    def override(self, **kwargs: Any) -> _Request:
        return _Request(kwargs["system_message"])


async def _system_text(middleware: MemoryMiddleware, base: str = "SYS") -> str:
    """跑一遍 ``awrap_model_call``，返回真正交给模型的那段系统消息文本。"""
    seen: list[str] = []

    async def handler(request: Any) -> AIMessage:
        seen.append(request.system_message.text)
        return AIMessage(content="好")

    await middleware.awrap_model_call(_Request(SystemMessage(content=base)), handler)
    return seen[0]


class TestTheDecision:
    async def test_a_pending_pull_stops_the_round(self):
        backend = _Backend(STALE)

        with pytest.raises(TurnStop) as caught:
            await _gate(backend, pull(12, title="商品列表分页", state="open")).abefore_agent(
                None, None
            )

        marker = caught.value.marker
        assert marker.startswith(GATE_MARKER)
        # marker 得自己说清楚：编号、标题、分支、链接，以及「谁来合并」
        assert "#12" in marker and "商品列表分页" in marker
        assert "（分支 feat/12）" in marker, "分支取 PR 的 head.ref（表已经不在了）"
        assert "https://gitee.com/acme/demo/pulls/12" in marker
        assert "合并由人工在 Gitee 上完成" in marker
        assert caught.value.extra["aborted"] == GATE_ABORT_REASON
        assert caught.value.extra["pending"] == ["12"]

    async def test_the_marker_names_the_repo_platform_not_a_hardcoded_one(self):
        """``marker`` 里的平台名跟着仓库走。

        回归：这三处文案以前写死「Gitee」，GitHub 仓库的 PR 闸门会告诉用户
        「合并由人工在 Gitee 上完成」—— 让人去错的地方找 PR。
        """
        for platform, label, other in (
            (Platform.GITHUB, "GitHub", "Gitee"),
            (Platform.GITEE, "Gitee", "GitHub"),
        ):
            with pytest.raises(TurnStop) as caught:
                await _gate_for(
                    platform, _Backend(STALE), pull(12, state="open")
                ).abefore_agent(None, None)

            marker = caught.value.marker
            assert f"已按 {label} 的实际状态刷新" in marker
            assert f"合并由人工在 {label} 上完成" in marker
            assert other not in marker, marker

    async def test_a_merged_pull_is_not_pending(self):
        backend = _Backend(STALE)

        result = await _gate(backend, pull(12, state="merged")).abefore_agent(None, None)

        assert result is None

    async def test_a_closed_unmerged_pull_is_not_pending_either(self):
        """已废弃不算待合入：它不会合并了，拦着只会把这一轮永久锁死。"""
        backend = _Backend(STALE)

        result = await _gate(backend, pull(12, state="closed")).abefore_agent(None, None)

        assert result is None

    async def test_a_refresh_only_form_refreshes_without_blocking(self):
        """qa / analysis / review 也刷新记忆，但**不拦**本轮 —— review 要审的那个 PR
        本身就是 open 的，按「有 open PR 就拦」判会把自己永久挡在门外。"""
        backend = _Backend(STALE)
        middleware = _refresh_only(backend, pull(12))

        assert await middleware.abefore_agent(None, None) is None
        assert f"最近 PR：{LINK_12}" in backend.files[PATH], "刷新照做"


class TestTheRefresh:
    async def test_the_memory_is_refreshed_before_the_stop(self):
        """刷新与拦不拦是两个决定，但顺序是定的：先按事实改盘上的文件，再抛。"""
        backend = _Backend(STALE)

        with pytest.raises(TurnStop):
            await _gate(backend, pull(12, title="商品列表分页")).abefore_agent(None, None)

        stored = backend.files[PATH]
        assert backend.aedits and backend.aedits[0][0] == STALE, "old_string 必须是当时全文"
        # 文件头归平台：时间戳推进到本轮，最近 PR 指向刚查到的那个
        assert "> 最后更新：2026-09-20 08:00" not in stored
        assert f"最近 PR：{LINK_12}" in stored
        assert "需求列表" not in stored and "\n|" not in stored, "表不该再出现"

    async def test_an_unchanged_memory_is_not_written(self):
        """对账没改出东西就别写盘 —— 每轮进闸门都写一次是白白的存储往返。"""
        settled = STALE.replace("最近 PR：—", f"最近 PR：{LINK_12}")
        backend = _Backend(settled)

        with pytest.raises(TurnStop):
            await _gate(backend, pull(12, title="商品列表分页")).abefore_agent(None, None)

        assert backend.aedits == []

    async def test_a_repo_without_memory_is_gated_but_never_created(self):
        """没记忆也拦：仓库有未合入的 PR 就该拦，与有没有写过记忆无关。
        但**不建文件** —— 建档是 ``save_repo_memory`` + 模板的事。"""
        backend = _Backend()

        with pytest.raises(TurnStop) as caught:
            await _gate(backend, pull(31, title="订单导出 CSV")).abefore_agent(None, None)

        assert "#31" in caught.value.marker
        # 补进来的行（表里没有、PR 开着）分支取 PR 的 head.ref
        assert "订单导出 CSV" in caught.value.marker
        assert backend.awrites == [] and backend.aedits == []
        assert backend.files == {}

    async def test_a_write_failure_does_not_change_the_verdict(self):
        """写盘失败只记日志：拦不拦取决于 PR 状态。"""

        class _Broken(_Backend):
            async def aedit(self, path: str, old: str, new: str) -> SimpleNamespace:
                return SimpleNamespace(error="backend unavailable", path=None)

        backend = _Broken(STALE)

        with pytest.raises(TurnStop) as caught:
            await _gate(backend, pull(12)).abefore_agent(None, None)

        assert caught.value.extra["aborted"] == GATE_ABORT_REASON


class TestNothingToVerify:
    """记忆里没有要核对的 PR 时：不查 Gitee、不写盘、不拦（2026-09-26 用户口径）。"""

    async def test_a_settled_memory_never_touches_gitee(self):
        """PR 那行写着「已合并」= 已经说清结局，这一轮连列表都不取。

        桩故意抛 AssertionError：只要 ``all_prs`` 被调用，这条当场红 —— 比数调用次数更硬。
        """
        settled = with_status(STALE, "已合并")
        backend = _Backend(settled)

        async def boom() -> list[dict]:
            raise AssertionError("没有要核对的 PR，不该查 Gitee")

        result = await MemoryMiddleware(
            backend=backend, path=PATH, all_prs=boom, blocking=True
        ).abefore_agent(None, None)

        assert result is None
        assert backend.aedits == [], "这一轮什么都没核，也就不该写盘"
        assert backend.files[PATH] == settled

    async def test_a_merged_pull_is_restated_and_the_round_goes_on(self):
        """写着「待合并」而线上已经合入：把那一格改掉，且**不拦** —— 闸门跟实况走。"""
        backend = _Backend(with_status(STALE, "待合并"))

        result = await _gate(backend, pull(12, state="merged")).abefore_agent(None, None)

        assert result is None, "线上已经合入了，没有「还没落地的改动」可拦"
        stored = backend.files[PATH]
        assert "**状态**：已合并" in stored
        assert "**状态**：待合并" not in stored
        assert backend.aedits and backend.aedits[0][0] == with_status(STALE, "待合并")

    async def test_a_closed_pull_is_restated_too(self):
        backend = _Backend(with_status(STALE, "待合并"))

        result = await _gate(backend, pull(12, state="closed")).abefore_agent(None, None)

        assert result is None
        assert "**状态**：已关闭" in backend.files[PATH]

    async def test_a_still_open_pull_keeps_the_row_and_blocks(self):
        """还开着：那一格不动，闸门照旧拦。"""
        text = with_status(STALE, "待合并")
        backend = _Backend(text)

        with pytest.raises(TurnStop):
            await _gate(backend, pull(12, title="商品列表分页")).abefore_agent(None, None)

        assert "**状态**：待合并" in backend.files[PATH]

    async def test_a_repo_without_memory_still_looks(self):
        """**唯一还查的一档**：从没写过记忆的仓库没有任何记忆可依据，闸门只能自己去问。"""
        backend = _Backend()
        calls: list[int] = []

        async def counted() -> list[dict]:
            calls.append(1)
            return [pull(31, title="订单导出 CSV")]

        with pytest.raises(TurnStop):
            await MemoryMiddleware(
                backend=backend, path=PATH, all_prs=counted, blocking=True
            ).abefore_agent(None, None)

        assert calls == [1]


class TestTheBlindPath:
    async def test_the_blind_marker_names_the_repo_platform(self):
        """「查不到」那条文案里的平台名也跟仓库走（让人知道该去修哪边的凭据）。"""
        backend = _Backend(STALE)
        boom = PrLookupError("HTTP 401 token 未配置")

        for platform, label, other in (
            (Platform.GITHUB, "GitHub", "Gitee"),
            (Platform.GITEE, "Gitee", "GitHub"),
        ):
            with pytest.raises(TurnStop) as caught:
                await _gate_for(
                    platform, backend, error=boom
                ).abefore_agent(None, None)

            marker = caught.value.marker
            assert f"请先确认凭据 / 网络 / {label} 可用性" in marker
            assert other not in marker, marker

    async def test_a_lookup_failure_stops_the_round_without_writing(self):
        """「查不到」既不能当成有、也不能当成没有：拦下本轮，且一个字节都不写。"""
        backend = _Backend(STALE)

        with pytest.raises(TurnStop) as caught:
            await _gate(
                backend, error=PrLookupError("HTTP 401 token 未配置")
            ).abefore_agent(None, None)

        marker = caught.value.marker
        assert marker.startswith(BLIND_MARKER)
        assert "HTTP 401 token 未配置" in marker, "原因要原样透传，用户才知道修什么"
        assert backend.aedits == [] and backend.awrites == []
        assert backend.files[PATH] == STALE


class TestTheInjection:
    async def test_the_whole_memory_goes_into_the_system_prompt(self):
        """贴的是**全文**，并明说「别再读一遍」—— 否则模型照旧多一次 `read_file`。"""
        middleware = _injector(_Backend(STALE))

        await middleware.abefore_agent(None, None)
        text = await _system_text(middleware)

        assert MEMORY_BLOCK_TITLE in text
        assert "## 一、项目概览" in text and "Java 21" in text
        assert "read_file" in text

    async def test_the_block_sits_after_the_original_prompt(self):
        """追加而不是替换：原有系统提示一个字都不能少。"""
        middleware = _injector(_Backend(STALE))

        await middleware.abefore_agent(None, None)
        text = await _system_text(middleware)

        assert text.startswith("SYS")
        assert text.index("SYS") < text.index(MEMORY_BLOCK_TITLE)

    async def test_a_repo_without_memory_injects_nothing(self):
        """没写过记忆就不注入 —— 空块会被当成「这仓库没记忆」，还不如干脆没有。"""
        middleware = _injector(_Backend())

        await middleware.abefore_agent(None, None)

        assert await _system_text(middleware) == "SYS"

    async def test_the_readonly_path_writes_nothing(self):
        """只读那条路只读盘：不查 PR、不写回，也不拦本轮。"""
        backend = _Backend(STALE)
        middleware = _injector(backend)

        assert await middleware.abefore_agent(None, None) is None
        assert backend.awrites == [] and backend.aedits == []

    async def test_the_injected_text_is_the_refreshed_one(self):
        """注入的是**对账后**那份：写盘失败时盘上还是旧的，注入旧事实比不注入更糟。"""

        class _Broken(_Backend):
            async def aedit(self, path: str, old: str, new: str) -> SimpleNamespace:
                return SimpleNamespace(error="backend unavailable", path=None)

        backend = _Broken(STALE)
        middleware = _gate(backend, pull(12, state="merged"))

        await middleware.abefore_agent(None, None)
        text = await _system_text(middleware)

        assert f"最近 PR：{LINK_12}" in text, "注入的该是本轮对账后的头行"
        assert backend.files[PATH] == STALE, "这条要的正是「写盘失败」这个前提"


class TestTheAssemblyPoint:
    def test_every_form_refreshes_but_only_two_block(self):
        """每个形态都挂、都刷新记忆；**拦本轮**只给 plan / orchestrate（2026-09-25 起）。

        review 刻意不拦：它要审的那个 PR 本身就是 open 的，拦了就把自己永久挡在门外。
        """
        settings = Settings()
        backend = _Backend()
        for form in REFRESH_FORMS:
            middleware = build_memory_middleware(settings, REPO, backend, form)
            assert middleware._all_prs is not None, form
            assert middleware._blocking is (form in BLOCKING_FORMS), form
        assert BLOCKING_FORMS == {PLAN_FORM, ORCHESTRATE_FORM}

    def test_the_gate_reads_the_path_the_writer_writes(self):
        """装配点自己拼路径就会写到没人读的 key 上 —— 两边必须同一个口径。"""
        middleware = build_memory_middleware(Settings(), REPO, _Backend(), PLAN_FORM)

        assert middleware._path == memory_path("acme", "demo")
        assert middleware._path == PATH

    def test_every_subagent_gets_its_own_instance(self):
        """子智能体各拿一份新实例：同一回合它们可能并发跑，共用一个会互相覆盖注入块。"""
        made: list[MemoryMiddleware] = []

        def factory() -> MemoryMiddleware:
            made.append(_injector(_Backend(STALE)))
            return made[-1]

        specs = build_subagents(
            tools_by_role={"planner": [], "coder": []},
            model_for=lambda role: _Quiet(),
            policies={role: policy_for(role) for role in ("planner", "coder")},
            memory_factory=factory,
        )

        memories = [
            mw
            for spec in specs
            for mw in spec["middleware"]
            if isinstance(mw, MemoryMiddleware)
        ]
        assert len(memories) == 2, "两个角色各挂一个"
        assert memories[0] is not memories[1], "不能共用同一个实例"
        assert memories[0]._all_prs is None, "子智能体这份不查 PR"


# -- 穿出图 ----------------------------------------------------------------


class _Quiet(BaseChatModel):
    """只回一句正文，不调工具；记下被调用过几次、每次拿到了哪些消息。"""

    calls: int = 0
    seen: list[list[Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "quiet"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _Quiet:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        self.calls += 1
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="好"))])


async def _drain(agent: Any, *, intent: str | None = None) -> None:
    """把 ``astream_events`` 抽干（``service.py`` 的消费端就是这个形状）。

    ``intent`` 就是 server 层逐轮塞进 ``configurable`` 的那个键 —— 闸门按它判（形态只决定
    挂不挂闸门）。不给就与「没经 server 派发」等价：一律按正向判。
    """
    configurable: dict[str, Any] = {"thread_id": "t1"}
    if intent is not None:
        configurable["intent"] = intent
    stream = await agent.astream_events(
        {"messages": [{"role": "user", "content": "干点活"}]},
        config=RunnableConfig(configurable=configurable),
        version="v3",
    )
    async for _event in stream:
        pass


def _graph(
    backend: Any, model: Any, *pulls: dict, platform: Platform | None = None
):
    """``platform`` 留空＝按既有的 Gitee 会话（大多数用例只要那个）。"""
    return build_agent(
        model=model,
        backend=backend,  # type: ignore[arg-type]
        tools=[],
        store=None,  # type: ignore[arg-type]
        checkpointer=None,  # type: ignore[arg-type]
        system_prompt="SYS",
        policy=policy_for(ORCHESTRATE_FORM),
        subagents=None,
        skills=None,
        memory=(
            _gate(backend, *pulls)
            if platform is None
            else _gate_for(platform, backend, *pulls)
        ),
    )


class TestThroughTheGraph:
    async def test_the_stop_survives_astream_events(self):
        """``service.py`` 的 ``except TurnStop`` 包的是 ``astream_events``：异常必须
        原样穿出来。被 LangGraph 包一层，闸门就变成了「本轮故障」。"""
        backend = _Backend(STALE)
        model = _Quiet()

        with pytest.raises(TurnStop) as caught:
            await _drain(_graph(backend, model, pull(12, title="商品列表分页")))

        assert caught.value.marker.startswith(GATE_MARKER)
        assert model.calls == 0, "闸门在 before_agent 生效：模型一次都不该被调用"

    async def test_a_clean_round_runs_normally(self):
        """没有未合入的 PR 时闸门完全透明：图照跑、模型照被调用。"""
        backend = _Backend(STALE)
        model = _Quiet()

        await _drain(
            _graph(backend, model, pull(12, state="merged"))  # type: ignore[arg-type]
        )

        assert model.calls >= 1

    async def test_the_block_reaches_the_model_through_the_graph(self):
        """拿真图跑一次：证明这块确实挂在链上、也确实进了模型看到的系统消息。"""
        backend = _Backend(STALE)
        model = _Quiet()

        await _drain(_graph(backend, model, pull(12, state="merged")))

        first = "\n".join(str(message.content) for message in model.seen[0])
        assert MEMORY_BLOCK_TITLE in first
        assert "## 一、项目概览" in first

    async def test_rework_passes_while_its_pull_is_open(self):
        """``rework``（按评审意见改）要改的就是那份还开着的 PR —— 正向判会把它永久挡在门外，
        所以这一档反过来：有未合入的 PR 就放行。"""
        backend = _Backend(STALE)
        model = _Quiet()

        await _drain(_graph(backend, model, pull(12, title="商品列表分页")), intent="rework")

        assert model.calls >= 1, "有那份 PR 就不该拦"
        assert len(backend.aedits) == 1, "记忆照旧按实况刷新"
        first = "\n".join(str(message.content) for message in model.seen[0])
        assert MEMORY_BLOCK_TITLE in first

    async def test_rework_is_stopped_when_there_is_nothing_to_fix(self):
        """没有待合并的 PR ＝ 没有可修的对象：拿不到审查原文的模型只会空转或凭记忆编一份
        「评审意见」出来改，所以这一轮直接拦下，模型一次都不该被调用。"""
        backend = _Backend(STALE)
        model = _Quiet()

        with pytest.raises(TurnStop) as caught:
            await _drain(
                _graph(backend, model, pull(12, state="merged")), intent="rework"
            )

        assert caught.value.marker.startswith(NOTHING_TO_FIX_MARKER)
        assert caught.value.extra["aborted"] == NO_TARGET_ABORT_REASON
        assert model.calls == 0, "闸门在 before_agent 生效：模型一次都不该被调用"
        assert len(backend.aedits) == 1, "判归判，记忆仍然按实况刷新"

    async def test_rework_is_stopped_when_the_repo_has_no_pull_at_all(self):
        """一个 PR 都没查到时同样是「没有可修的对象」。"""
        model = _Quiet()

        with pytest.raises(TurnStop) as caught:
            await _drain(_graph(_Backend(STALE), model), intent="rework")

        assert caught.value.marker.startswith(NOTHING_TO_FIX_MARKER)
        assert model.calls == 0

    async def test_the_nothing_to_fix_marker_names_the_repo_platform(self):
        """第三条文案（``rework`` 没有可修对象）里的平台名同样跟仓库走。"""
        model = _Quiet()

        with pytest.raises(TurnStop) as caught:
            await _drain(
                _graph(
                    _Backend(STALE),
                    model,
                    pull(12, state="merged"),
                    platform=Platform.GITHUB,
                ),
                intent="rework",
            )

        marker = caught.value.marker
        assert "已按 GitHub 的实际状态刷新" in marker
        assert "Gitee" not in marker, marker

    async def test_coding_is_still_blocked_by_the_open_pull(self):
        """同一份中间件、同一个形态：``coding`` 轮的口径没被 rework 带偏。"""
        model = _Quiet()

        with pytest.raises(TurnStop) as caught:
            await _drain(_graph(_Backend(STALE), model, pull(12)), intent="coding")

        assert caught.value.marker.startswith(GATE_MARKER)
        assert model.calls == 0

    async def test_a_missing_intent_keeps_the_conservative_verdict(self):
        """读不到意图（没经 server 派发）时按正向判：有 open PR 就拦，保守优先。"""
        model = _Quiet()

        with pytest.raises(TurnStop) as caught:
            await _drain(_graph(_Backend(STALE), model, pull(12)))

        assert caught.value.marker.startswith(GATE_MARKER)


# -- 轮末打底（``server/turn/records.py``）---------------------------------


class _FakeDb:
    @asynccontextmanager
    async def session(self):
        yield None


class _FakeManager:
    def __init__(self, backend: Any) -> None:
        self._backend = backend

    async def ensure_runtime(self, session):
        return SimpleNamespace(sandbox=None, backend=self._backend, agents={})

    def get_agent(self, runtime, session, profile):
        return FakeAgent([])

    def thread_id(self, session):
        return "thread-1"


def _patch_pr_client(monkeypatch, *, pulls=(), error: Exception | None = None) -> list[int]:
    """把 PR 客户端换成假的（``build_git_client`` 这个装配点），返回「查了几次」的计数。"""
    calls: list[int] = []

    class _Client:
        def __init__(self, base_url: str, repo: Any) -> None:
            pass

        async def all_pulls(self) -> list[dict]:
            calls.append(1)
            if error is not None:
                raise error
            return list(pulls)

    monkeypatch.setattr(
        service_mod.turn_records, "build_git_client", lambda *a, **k: _Client(*a, **k)
    )
    return calls


def _patch_dao(monkeypatch) -> None:
    """把这一路上会碰到的 dao 全打桩 —— 验的是接线，不是库。

    ``recent_user_summaries`` 要返回**列表**（它喂给 ``TurnState.history``），
    其余几个返回 None 就够了。
    """

    async def nothing(*args: Any, **kwargs: Any) -> None:
        return None

    async def no_summaries(*args: Any, **kwargs: Any) -> list[str]:
        return []

    for name in (
        "add_record",
        "get_latest_plan",
        "set_session_title_if_empty",
        # 判定会读「助手上一轮末尾」当上下文（2026-10-12 加）：验接线就别碰库
        "latest_assistant_tail",
    ):
        monkeypatch.setattr(service_mod.dao, name, nothing)
    monkeypatch.setattr(service_mod.dao, "recent_user_summaries", no_summaries)


def _service(backend: Any) -> HarnessService:
    return HarnessService(
        settings=Settings(), manager=_FakeManager(backend), database=_FakeDb()
    )


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        public_id=uuid.uuid4(),
        db_id=42,
        repo=REPO,
    )


def _runtime(backend: Any) -> SimpleNamespace:
    return SimpleNamespace(sandbox=None, backend=backend, agents={})


class TestTheRoundEndBackfill:
    async def test_a_coding_round_refreshes_the_memory(self, monkeypatch):
        _patch_pr_client(monkeypatch, pulls=[pull(12, title="商品列表分页")])
        backend = _Backend(STALE)

        await _service(backend)._refresh_repo_memory(_session(), _runtime(backend))

        assert f"最近 PR：{LINK_12}" in backend.files[PATH]
        assert "> 最后更新：2026-09-20 08:00" not in backend.files[PATH]

    async def test_a_lookup_failure_writes_nothing(self, monkeypatch):
        """与闸门相反：打底查不到就只记日志放行 —— 副产物不该翻掉本轮的结论。"""
        _patch_pr_client(monkeypatch, error=PrLookupError("HTTP 401"))
        backend = _Backend(STALE)

        await _service(backend)._refresh_repo_memory(_session(), _runtime(backend))

        assert backend.files[PATH] == STALE
        assert backend.aedits == []

    async def test_a_broken_backend_does_not_escape(self, monkeypatch):
        """后端整个抛异常（不是返回 error 对象）也不能冒到调用方。"""
        _patch_pr_client(monkeypatch, pulls=[pull(12)])

        class _Boom(_Backend):
            async def aread(self, path: str):
                raise RuntimeError("store 挂了")

        backend = _Boom(STALE)

        await _service(backend)._refresh_repo_memory(_session(), _runtime(backend))


class TestTheBackfillTrigger:
    """判据是**形态**：只有 coding 与 review 两轮有落地的事实要沉淀（2026-09-25 起）。"""

    async def _chat(self, monkeypatch, backend: Any, intent: str) -> list[SimpleNamespace]:
        _patch_dao(monkeypatch)
        touched: list[SimpleNamespace] = []

        async def spy(self, session, runtime):
            touched.append(session)

        monkeypatch.setattr(HarnessService, "_refresh_repo_memory", spy)
        svc = _service(backend)

        async def classify(
            message: str,
            history: list[str],
            previous_reply: str | None = None,
            previous_intent: str | None = None,
            active_intent: str | None = None,
            dialogue: list[dict[str, str]] | None = None,
        ) -> IntentDecision:
            return IntentDecision(intent=intent, source="fallback")

        svc._detect_intent = classify  # type: ignore[method-assign]
        frames = [frame async for frame in svc.chat(_session(), "做点事")]
        assert frames[-1] == {"type": "done"}, "打底不该改变本轮的下发形状"
        return touched

    @pytest.mark.parametrize("intent", ["coding", "review", "analysis"])
    async def test_the_writeback_rounds_backfill(self, monkeypatch, intent):
        """写回记忆的那几轮（coding / review / analysis）收尾都要按实况打底一次。"""
        touched = await self._chat(monkeypatch, _Backend(STALE), intent)
        assert len(touched) == 1, intent

    @pytest.mark.parametrize("intent", ["qa", "plan"])
    async def test_other_forms_do_not_backfill(self, monkeypatch, intent):
        """qa 只回答、plan 还没动代码：这两轮不写记忆，也就不用打底。"""
        touched = await self._chat(monkeypatch, _Backend(STALE), intent)
        assert touched == []
