"""凭据脱敏：模式匹配的边界，以及四条出口是否真的都过了一遍。

脱敏的价值全在「不漏」与「不误伤」的取舍上：漏了是红线破了（令牌落进 checkpoint、
日志、前端）；误伤则是模型看不懂 diff、干不了活 —— 而在编码场景里后者更常发生。
所以正例与「不该被打码」的反例两面都要钉住。

测试里一律用**合成令牌**（形状像真的、值是我们编的），不拿任何真凭据来测。
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents.middleware import (
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import Field

from ai_coding_harness.agent.graph import build_agent
from ai_coding_harness.agent.middleware import (
    ModelRedactionMiddleware,
    RedactionMiddleware,
    TurnState,
)
from ai_coding_harness.agent.profiles import ORCHESTRATE_FORM
from ai_coding_harness.agent.subagents import build_subagents
from ai_coding_harness.config import Settings
from ai_coding_harness.observability.run_log import AgentRunLog
from ai_coding_harness.safety.policy import policy_for
from ai_coding_harness.safety.redact import (
    CRED_TERM,
    REDACTED,
    detect_credential,
    redact,
    secret_terms,
)
from ai_coding_harness.server import service as service_mod
from ai_coding_harness.server.intent import IntentDecision
from ai_coding_harness.server.service import HarnessService
from ai_coding_harness.tools.git import GiteeRepo
from tests.v3_events import FakeAgent, RunLogFeed, reasoning, tool_finish, tool_start
from tests.v3_events import text as text_delta

# 合成凭据：不带任何真实前缀，所以只有「值匹配」能盖住它 —— 便于隔离两条腿
FAKE = "s3cr3tTOKENvalue123"
FAKE_PREFIXED = "glpat-abcdefghijklmnop1234"


# -- 纯函数 ----------------------------------------------------------------

class TestValueMatch:
    def test_known_value_is_replaced(self):
        out = redact(f"cat .secrets -> {FAKE}", [FAKE])
        assert FAKE not in out
        assert REDACTED in out

    def test_short_and_empty_values_are_dropped(self):
        """空串当词条会把整段文本切碎，短值则会误伤正常词。"""
        assert secret_terms("", None, "abc") == ()
        assert secret_terms(FAKE) == (FAKE,)

    def test_duplicates_collapse(self):
        assert secret_terms(FAKE, FAKE, FAKE) == (FAKE,)

    def test_no_terms_is_safe(self):
        assert redact("普通文本", ()) == "普通文本"
        assert redact("", (FAKE,)) == ""


class TestPatterns:
    def test_known_credential_prefixes(self):
        for text in ("token glpat-abcdefghijklmnop1234", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"):
            assert REDACTED in redact(text), text
            assert "glpat-" not in redact(text) and "ghp_" not in redact(text), text

    def test_cloud_keys(self):
        for text in ("AKIAIOSFODNN7EXAMPLE1", "LTAIabcdefghijklmn", "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"):
            assert REDACTED in redact(text), text

    def test_bearer_keeps_the_scheme(self):
        out = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6")
        assert out == f"Authorization: Bearer {REDACTED}"

    def test_url_userinfo_keeps_user_and_host(self):
        out = redact("https://oauth2:tok3nvalue12345@gitee.com/a/b.git")
        assert out == f"https://oauth2:{REDACTED}@gitee.com/a/b.git"

    def test_assignment_forms(self):
        assert redact('GITEE_TOKEN="abcdefgh12345678"') == f'GITEE_TOKEN="{REDACTED}"'
        assert redact("export GITEE_TOKEN=abcdefgh12345678") == f"export GITEE_TOKEN={REDACTED}"


class TestNoFalsePositives:
    """这些一个都不能被打码 —— 打花了模型就没法读代码。"""

    def test_bare_hashes_and_shas_survive(self):
        for text in (
            "commit 3f2a1b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a",
            "sha256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        ):
            assert redact(text) == text, text

    def test_code_reading_from_the_environment_survives(self):
        for text in (
            'api_key = os.getenv("DEEPSEEK_API_KEY")',
            "access_key = settings.oss_key",
            "password: Optional[str] = None",
            "const token = req.headers.authorization;",
            "+    self.token = token  # noqa",
        ):
            assert redact(text) == text, text

    def test_plain_prose_survives(self):
        assert redact("把 token 的读取逻辑挪到配置层") == "把 token 的读取逻辑挪到配置层"


# -- 入口拦截的判定 --------------------------------------------------------

class TestDetectCredential:
    """只回传类别名 —— 回执、日志、落库都不能出现命中的值。"""

    @pytest.mark.parametrize(
        ("text", "label"),
        [
            (f"用这个 {FAKE_PREFIXED} 拉代码", "已知凭据前缀"),
            ("AKIAIOSFODNN7EXAMPLE1", "云厂商密钥"),
            ("Authorization: Bearer abcdefgh12345678", "Bearer 令牌"),
            ("https://oauth2:tok3nvalue12345@gitee.com/a/b.git", "URL 内嵌口令"),
            ("GITEE_TOKEN=abcdefgh12345678", "赋值字面量"),
            ('password = "hunter2hunter2"', "赋值字面量"),
        ],
    )
    def test_label_per_kind(self, text, label):
        assert detect_credential(text) == label

    def test_known_value_matches_without_any_shape(self):
        """按值比对是零误判的那条腿：FAKE 不带前缀，只有词表认得出来。"""
        assert detect_credential(f"用这个 {FAKE} 就行", (FAKE,)) == CRED_TERM
        assert detect_credential(f"用这个 {FAKE} 就行", ()) is None

    @pytest.mark.parametrize(
        "text",
        [
            "commit 3f2a1b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a",
            'api_key = os.getenv("DEEPSEEK_API_KEY")',
            "password: Optional[str] = None",
            "把 token 的读取逻辑挪到配置层",
        ],
    )
    def test_clean_text_passes(self, text):
        assert detect_credential(text) is None, text


# -- 中间件两个分支 --------------------------------------------------------

def _request() -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "execute", "args": {}, "id": "call-1"},
        tool=None,
        state=None,
        runtime=None,
    )


async def _through_middleware(result: Any) -> Any:
    async def handler(_request: ToolCallRequest) -> Any:
        return result

    return await RedactionMiddleware().awrap_tool_call(_request(), handler)


class TestMiddlewareBranches:
    async def test_tool_message_is_redacted(self):
        message = ToolMessage(content=f"读到 {FAKE_PREFIXED}", tool_call_id="call-1")
        out = await _through_middleware(message)

        assert REDACTED in out.content
        assert "glpat-" not in out.content
        assert out.tool_call_id == "call-1", "丢了 tool_call_id 就没法跟 AI 的调用配对了"

    async def test_content_blocks_are_redacted(self):
        message = ToolMessage(
            content=[{"type": "text", "text": f"值 {FAKE_PREFIXED}"}],
            tool_call_id="call-1",
        )
        out = await _through_middleware(message)

        assert REDACTED in out.content[0]["text"]
        assert FAKE_PREFIXED not in out.content[0]["text"]

    async def test_command_messages_are_redacted(self):
        command = Command(
            update={"messages": [ToolMessage(content=FAKE_PREFIXED, tool_call_id="call-1")]},
            goto="model",
        )
        out = await _through_middleware(command)

        assert REDACTED in out.update["messages"][0].content
        assert out.goto == "model", "除消息外的字段要原样保留"

    async def test_untouched_result_is_returned_as_is(self):
        """没命中就别造新对象：状态无谓更新会连带下游一堆重算。"""
        message = ToolMessage(content="一切正常", tool_call_id="call-1")
        assert await _through_middleware(message) is message

        command = Command(update={"messages": [ToolMessage(content="一切正常", tool_call_id="c")]})
        assert await _through_middleware(command) is command

    async def test_non_message_result_passes_through(self):
        assert await _through_middleware("原样字符串") == "原样字符串"


def _model_request() -> ModelRequest:
    # 这个中间件只看 configurable，不碰 request —— 给个最小可构造的实例就够
    return ModelRequest(model=None, messages=[])  # type: ignore[arg-type]


async def _through_model_middleware(response: Any) -> Any:
    async def handler(_request: ModelRequest) -> Any:
        return response

    return await ModelRedactionMiddleware().awrap_model_call(_model_request(), handler)


class TestModelOutputRedaction:
    """模型正文是另一条出口：它被直接写进 state，进而落进 checkpoint。"""

    async def test_response_messages_are_redacted(self):
        response = ModelResponse(
            result=[AIMessage(content=f"我读到 {FAKE_PREFIXED}")],
            structured_response={"k": 1},
        )
        out = await _through_model_middleware(response)

        assert REDACTED in out.result[0].content
        assert FAKE_PREFIXED not in out.result[0].content
        assert out.structured_response == {"k": 1}, "结构化输出不能丢"

    async def test_extended_response_is_unwrapped_and_kept(self):
        """外层中间件会用 ExtendedModelResponse 捎带一条 Command —— 漏认就等于放行。"""
        extended = ExtendedModelResponse(
            model_response=ModelResponse(result=[AIMessage(content=FAKE_PREFIXED)]),
            command=Command(goto="model"),
        )
        out = await _through_model_middleware(extended)

        assert REDACTED in out.model_response.result[0].content
        assert out.command.goto == "model"

    async def test_bare_ai_message_is_redacted(self):
        out = await _through_model_middleware(AIMessage(content=FAKE_PREFIXED))
        assert REDACTED in out.content

    async def test_untouched_response_is_returned_as_is(self):
        response = ModelResponse(result=[AIMessage(content="好")])
        assert await _through_model_middleware(response) is response


# -- 图挂载 ----------------------------------------------------------------

class _ToolCaller(BaseChatModel):
    """第一轮发一个工具调用，第二轮收尾；记录每轮收到的消息。"""

    seen: list[list[BaseMessage]] = Field(default_factory=list)
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "tool-caller"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _ToolCaller:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="", tool_calls=[{"name": "read_secret", "args": {}, "id": "call-1"}]
            )
        else:
            message = AIMessage(content="好")
        return ChatResult(generations=[ChatGeneration(message=message)])


class _LeakyCaller(BaseChatModel):
    """只会把凭据复述一遍，不调工具 —— 用来单测模型正文那条出口。"""

    @property
    def _llm_type(self) -> str:
        return "leaky-caller"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _LeakyCaller:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        message = AIMessage(content=f"我复述一遍：{FAKE}")
        return ChatResult(generations=[ChatGeneration(message=message)])


@tool
def read_secret() -> str:
    """读一个装着凭据的文件（测试用）。"""
    return f"文件内容：{FAKE}"


class TestGraphWiring:
    # 这组用例只看脱敏，边界取最宽的那个形态（不隐藏工具）以免混入别的变量
    _POLICY = policy_for(ORCHESTRATE_FORM)

    async def test_terms_from_config_reach_the_state(self):
        """值匹配这条腿只能靠 config 传进来（智能体是长期存活对象）。"""
        model = _ToolCaller()
        agent = build_agent(
            model=model,
            backend=None,  # type: ignore[arg-type]
            tools=[read_secret],
            store=None,
            checkpointer=None,
            system_prompt="SYS",
            policy=self._POLICY,
            subagents=None,
            skills=None,
        )
        state = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "读一下"}]},
            config={"configurable": {"redact_terms": [FAKE]}},
        )

        tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
        assert tool_messages, "工具没被调用"
        assert FAKE not in tool_messages[0].content
        assert REDACTED in tool_messages[0].content

        # 关键是第二轮：模型看到的历史里已经没有令牌了
        assert FAKE not in "\n".join(str(m.content) for m in model.seen[-1])

    async def test_every_subagent_carries_the_middleware(self):
        """主智能体的自定义中间件不下传，三个子智能体必须各挂一份（两条出口都要）。"""
        specs = build_subagents(
            tools_by_role={"planner": [], "coder": [], "reviewer": []},
            model_for=lambda role: SimpleNamespace(),
            policies={role: policy_for(role) for role in ("planner", "coder", "reviewer")},
        )
        assert len(specs) == 3
        for spec in specs:
            # 按类型判（类下每个子智能体各建一份实例，不是同一个对象）
            kinds = [type(middleware) for middleware in spec["middleware"]]
            assert RedactionMiddleware in kinds, spec["name"]
            assert ModelRedactionMiddleware in kinds, spec["name"]

    async def test_model_output_lands_in_state_redacted(self):
        """模型自己写的正文同样进 state —— 只挡工具返回值时，这条出口是漏的。"""
        agent = build_agent(
            model=_LeakyCaller(),
            backend=None,  # type: ignore[arg-type]
            tools=[],
            store=None,
            checkpointer=None,
            system_prompt="SYS",
            policy=self._POLICY,
            subagents=None,
            skills=None,
        )
        state = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "读一下"}]},
            config={"configurable": {"redact_terms": [FAKE]}},
        )

        answer = state["messages"][-1]
        assert FAKE not in answer.content
        assert REDACTED in answer.content


# -- 日志落盘 --------------------------------------------------------------

_LOGGER = "ai_coding_harness.observability.run_log"


class TestRunLogRedaction:
    def test_both_args_and_results_are_masked(self, caplog):
        """入参可能是用户粘贴的令牌，返回值可能是刚从沙箱读出来的那份 —— 两个都记。"""
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=2000, terms=(FAKE,)))
        feed.start(tool_start("execute", call_id="e1", input={"command": f"echo {FAKE}"}))
        feed.end(tool_finish(call_id="e1", output=f"stdout: {FAKE}"))

        text = "\n".join(record.getMessage() for record in caplog.records)
        assert FAKE not in text
        assert text.count(REDACTED) >= 2

    def test_truncation_never_leaves_half_a_token(self, caplog):
        """先截断会让横跨截断点的令牌留半截：既躲过脱敏、又没了上下文。"""
        caplog.set_level(logging.INFO, logger=_LOGGER)
        feed = RunLogFeed(AgentRunLog(arg_limit=2000, result_limit=len(FAKE) + 2, terms=(FAKE,)))
        feed.start(tool_start("x", call_id="x1", input={}))
        feed.end(tool_finish(call_id="x1", output=FAKE))

        text = "\n".join(record.getMessage() for record in caplog.records)
        assert FAKE[:8] not in text
        assert REDACTED in text


# -- service 的四条出口 ----------------------------------------------------

class _FakeDb:
    @asynccontextmanager
    async def session(self):
        yield None


class _FakeManager:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def ensure_runtime(self, session: Any) -> SimpleNamespace:
        """工作区运行时（这里只当搬运工：把会话上挂的假沙箱交出去）。"""
        return SimpleNamespace(sandbox=session.sandbox, backend=None)

    def get_agent(self, runtime: Any, session: Any, profile: Any) -> FakeAgent:
        return FakeAgent(self._events)

    def thread_id(self, session: Any) -> str:
        return "thread-1"


class _FakeSandbox:
    def __init__(self, stdout: str) -> None:
        self._stdout = stdout

    async def run(self, command: str) -> SimpleNamespace:
        return SimpleNamespace(stdout=self._stdout, stderr="", exit_code=0)


class TestServiceOutputs:
    async def test_streamed_chunks_are_masked(self, monkeypatch):
        """正文与推理都在 _run_agent 里就先打码，SSE 与业务库记录一并干净。"""
        recorded: list[dict] = []

        async def fake_add_record(db: Any, **kwargs: Any) -> None:
            recorded.append(kwargs)

        monkeypatch.setattr(service_mod.dao, "add_record", fake_add_record)
        events = [
            reasoning(f"思考里带了 {FAKE}"),
            text_delta(f"读到 {FAKE}"),
            tool_start("execute", call_id="e1", input={}),
            tool_finish(call_id="e1", output=f"stdout {FAKE}"),
        ]
        svc = HarnessService(
            settings=Settings(), manager=_FakeManager(events), database=_FakeDb()
        )
        session = SimpleNamespace(public_id=uuid.uuid4(), db_id=1)

        out = [
            e
            async for e in svc._run_agent(
                session, SimpleNamespace(sandbox=None, backend=None),
                "做点事", "coding", (FAKE,),
                state=TurnState(message="做点事"),
            )
        ]

        assert FAKE not in "\n".join(e.get("content", "") for e in out)
        ai_events = [e for e in out if e["type"] == "ai"]
        assert ai_events and REDACTED in ai_events[0]["content"]
        thoughts = [e for e in out if e["type"] == "reasoning"]
        assert thoughts, "思考应当作为独立事件下发"
        assert FAKE not in thoughts[0]["content"]
        assert REDACTED in thoughts[0]["content"]
        assert FAKE not in recorded[-1]["content"]
        assert "思考里带了" not in str(recorded[-1].get("extra"))

    async def test_direct_intent_output_is_masked(self, monkeypatch):
        """sync 不经智能体，中间件够不着 —— 这条最容易被漏掉。"""
        recorded: list[dict] = []

        async def fake_add_record(db: Any, **kwargs: Any) -> None:
            recorded.append(kwargs)

        async def fake_title(db: Any, session_id: int, title: str) -> None:
            return None

        monkeypatch.setattr(service_mod.dao, "add_record", fake_add_record)
        monkeypatch.setattr(service_mod.dao, "set_session_title_if_empty", fake_title)

        svc = HarnessService(
            settings=Settings(intent_llm_enabled=False, intent_history_turns=0),
            manager=_FakeManager([]),
            database=_FakeDb(),
        )
        session = SimpleNamespace(
            public_id=uuid.uuid4(),
            db_id=1,
            repo=GiteeRepo(owner="o", repo="r", token=FAKE),
            repo_url="https://gitee.com/o/r.git",
            sandbox=_FakeSandbox(stdout=f"远端的 origin 带上了 {FAKE}"),
        )

        out = [e async for e in svc.chat(session, "同步仓库")]

        ai_events = [e for e in out if e["type"] == "ai"]
        assert ai_events, "直接执行没有产出"
        assert FAKE not in ai_events[0]["content"]
        assert REDACTED in ai_events[0]["content"]
        assert FAKE not in recorded[-1]["content"]

    async def test_error_text_is_masked(self, monkeypatch):
        """异常文本常把工具返回值整段带上，直接从 SSE 漏出去。"""
        monkeypatch.setattr(service_mod.dao, "add_record", _noop_record)
        monkeypatch.setattr(service_mod.dao, "set_session_title_if_empty", _noop_record)

        class _BoomManager(_FakeManager):
            def get_agent(self, runtime: Any, session: Any, profile: Any) -> Any:
                raise RuntimeError(f"调用失败：{FAKE}")

        svc = HarnessService(
            settings=Settings(intent_llm_enabled=False, intent_history_turns=0),
            manager=_BoomManager([]),
            database=_FakeDb(),
        )
        session = SimpleNamespace(
            public_id=uuid.uuid4(),
            db_id=1,
            repo=GiteeRepo(owner="o", repo="r", token=FAKE),
            repo_url="https://gitee.com/o/r.git",
            sandbox=_FakeSandbox(stdout=""),
        )

        out = [e async for e in svc.chat(session, "修复登录报错")]

        errors = [e for e in out if e["type"] == "error"]
        assert errors, "没有产出错误事件"
        assert FAKE not in errors[0]["error"]
        assert REDACTED in errors[0]["error"]


async def _noop_record(*args: Any, **kwargs: Any) -> None:
    return None


class _TrackingManager(_FakeManager):
    """额外记下「智能体 / 工作区到底有没有被动过」—— 拒收的消息一样都不该动。"""

    def __init__(self, events: list[dict]) -> None:
        super().__init__(events)
        self.requested = 0
        self.ensured = 0

    async def ensure_runtime(self, session: Any) -> SimpleNamespace:
        self.ensured += 1
        return await super().ensure_runtime(session)

    def get_agent(self, runtime: Any, session: Any, profile: Any) -> FakeAgent:
        self.requested += 1
        return super().get_agent(runtime, session, profile)


def _session_with_token(token: str) -> SimpleNamespace:
    return SimpleNamespace(
        public_id=uuid.uuid4(),
        db_id=1,
        repo=GiteeRepo(owner="o", repo="r", token=token),
        repo_url="https://gitee.com/o/r.git",
        sandbox=_FakeSandbox(stdout=""),
    )


@pytest.fixture
def spy(monkeypatch) -> SimpleNamespace:
    """收集落库记录与会话标题：底层 dao 的两次写入都落到这两个列表里。"""
    records: list[dict] = []
    titles: list[str] = []

    async def fake_add_record(db: Any, **kwargs: Any) -> None:
        records.append(kwargs)

    async def fake_title(db: Any, session_id: int, title: str) -> None:
        titles.append(title)

    monkeypatch.setattr(service_mod.dao, "add_record", fake_add_record)
    monkeypatch.setattr(service_mod.dao, "set_session_title_if_empty", fake_title)
    return SimpleNamespace(records=records, titles=titles)


def _service(manager: Any) -> HarnessService:
    return HarnessService(
        settings=Settings(intent_llm_enabled=False, intent_history_turns=0),
        manager=manager,
        database=_FakeDb(),
    )


class TestEntryRejection:
    """入口就拒：含凭据的消息整条不收，模型、落库、会话标题三个方向都不能留下它。"""

    async def test_credential_message_is_refused(self, spy):
        manager = _TrackingManager([])
        out = [
            e
            async for e in _service(manager).chat(
                _session_with_token(FAKE), f"帮我把 {FAKE_PREFIXED} 配上"
            )
        ]

        assert [e["type"] for e in out] == ["ai", "done"]
        assert "已知凭据前缀" in out[0]["content"]
        assert FAKE_PREFIXED not in out[0]["content"], "回执不能复述命中的值"
        assert manager.requested == 0, "拒收的消息不能交给智能体"
        assert manager.ensured == 0, "拒收的消息不该白建一个工作区"
        assert spy.titles == [], "会话标题不能写成含凭据的消息"

        assert len(spy.records) == 2, "用户消息与回执各留一条"
        for record in spy.records:
            assert FAKE_PREFIXED not in (record["content"] or "")
            assert FAKE_PREFIXED not in (record["summary"] or "")
        assert spy.records[0]["extra"]["credential"] == "已知凭据前缀"

    async def test_session_token_in_the_message_is_refused(self, spy):
        """按值比对：令牌长得再普通，只要等于本会话那一份就拦得住。"""
        out = [
            e
            async for e in _service(_TrackingManager([])).chat(
                _session_with_token(FAKE), f"用这个 {FAKE} 拉一下代码"
            )
        ]

        assert [e["type"] for e in out] == ["ai", "done"]
        assert CRED_TERM in out[0]["content"]
        assert FAKE not in "\n".join(str(e) for e in spy.records)

    async def test_clean_message_still_runs(self, spy):
        """别拦过头：普通消息照常派给智能体、照常命名会话。"""
        manager = _TrackingManager([text_delta("好")])
        message = "实现一个 /hello 接口"
        out = [
            e async for e in _service(manager).chat(_session_with_token(FAKE), message)
        ]

        assert manager.requested == 1
        # 跑完一轮的最后两帧是「本轮的账」（usage）与 done
        assert [e["type"] for e in out] == ["ai", "usage", "done"]
        assert spy.titles == [message]
        assert spy.records[0]["content"] == message


class TestIntentDecisionPassthrough:
    """脱敏不改判定：词表是另一条通道，别把两者搅在一起。"""

    async def test_decision_from_keyword_ignores_terms(self):
        decision = await service_mod.detect_intent("同步仓库", model=None)
        assert decision == IntentDecision("sync", "keyword", "命中口令关键字")
