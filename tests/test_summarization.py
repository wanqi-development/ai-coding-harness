"""压缩（摘要）中间件：我们自己构造那一份的**取值 / 身份 / 挂载位置**。

钉住的四件事都是 2026-10-15 排查「一轮 6.9M token」时量出来的，不是推测：

① ``trigger`` 必须是「窗口比例 **OR** 绝对上限」两条。只有比例那条时，窗口按模型查之后
   动辄 1M（``qwen3.8-max`` = 991808），线落在 843k —— 而那轮真机单次峰值只有 152k，
   等于**永不压缩**。
② 模型没有 ``profile`` 时**不能**带 fraction：上游 ``__init__`` 见到 fraction 而拿不到
   ``max_input_tokens`` 是直接 ``ValueError``（不是忽略），漏了这一档就是建 agent 当场崩。
③ ``trim_tokens_to_summarize`` 不能是 ``None``。上游**工厂**的默认值就是 ``None`` 并显式
   传给类（把类自己的 4000 覆盖掉），langchain 那边 ``None`` = 完全不裁 —— 摘要那次调用
   会把被逐出的全部历史再发一遍给主控那个贵模型，而它自己溢出时不像主调用那样有兜底，
   会把整轮带崩。
④ 我们那份要在 ``excluded_middleware`` 的**按类精确匹配**下活下来（上游那份被排掉），
   且位置在剪裁的**内侧** —— 上游把它挂在用户中间件外面，它数的就是「还没剪的账」。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents._excluded_middleware import _apply_excluded_middleware
from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    create_summarization_middleware,
)
from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model
from langchain.agents.middleware import (
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from ai_coding_harness.agent import graph as graph_mod
from ai_coding_harness.agent.middleware.context_calibration import CalibratedTokenCounter
from ai_coding_harness.agent.middleware.context_trim import (
    context_token_counter,
)
from ai_coding_harness.agent.middleware.summarization import (
    CONTEXT_MODE_BURST,
    CONTEXT_MODE_STEADY,
    PRETRIM_LOG_TAG,
    SUMMARY_LOG_TAG,
    HarnessSummarizationMiddleware,
    build_summarization,
    describe_trigger,
    profile_window,
    resolve_context_mode,
    summarization_defaults,
    trigger_line,
)
from ai_coding_harness.agent.subagents import build_subagents
from ai_coding_harness.config import Settings
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.safety.policy import policy_for

#: 真机上量到的那两格：qwen3.8-max 的窗口，与那轮单次输入的峰值
QWEN_WINDOW = 991808
PEAK_OF_THE_INCIDENT = 152_000


class _OpenAiFake(GenericFakeChatModel):
    """报 ``openai`` provider 的假模型 —— harness profile 是按 provider 名注册的。"""

    def _get_ls_params(self, **kwargs: object) -> dict[str, str]:
        return {"ls_provider": "openai"}


def _model(*, window: int | None = QWEN_WINDOW) -> GenericFakeChatModel:
    model = _OpenAiFake(messages=iter([AIMessage(content="ok")]))
    if window is not None:
        model.profile = {"max_input_tokens": window}
    return model


def _model_for_role(role: str) -> GenericFakeChatModel:
    """子智能体那条路用的模型工厂（``build_subagents`` 按角色取）。"""
    return _model()


def _backend() -> Any:
    """占位 backend：构造期只是被存下来，运行期才去解析。"""
    return SimpleNamespace()


def _settings(**overrides: Any) -> Settings:
    """不读 .env：这几条的断言不该被本机配置影响。"""
    # 绝大多数用例验的是「到线直接裁/压」；提醒线在单独用例里显式打开。
    values: dict[str, Any] = {"context_pretrim_notice_ratio": 0.0}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _one_message() -> list[BaseMessage]:
    """一条没有 ``usage_metadata`` 的历史 —— 免得「供应商自报真值」那条路替我们触发。"""
    return [HumanMessage(content="甲")]


def _summaries(stack: list[Any]) -> list[Any]:
    return [mw for mw in stack if isinstance(mw, SummarizationMiddleware)]


def _summary_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if SUMMARY_LOG_TAG in r.getMessage()]


def _pretrim_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if PRETRIM_LOG_TAG in r.getMessage()]


def _request(
    messages: list[BaseMessage],
    *,
    state: dict[str, Any] | None = None,
) -> ModelRequest:
    """``state`` 必须给：上游要从里面读上一次的压缩事件（``_summarization_event``）。"""
    return ModelRequest(
        model=_model(),
        messages=messages,
        state={"messages": messages, **(state or {})},
    )


def _long_history(blocks: int = 30) -> list[BaseMessage]:
    """一段够长的历史（每条 4000 个汉字 ≈ 2500 tokens，中文口径）。"""
    return [HumanMessage(content="甲" * 4000) for _ in range(blocks)]


def _tool_steps(count: int, *, chars: int = 4000) -> list[BaseMessage]:
    """造 ``count`` 轮「模型调一次工具 → 工具返回一大段」的历史。"""
    messages: list[BaseMessage] = []
    for index in range(count):
        call_id = f"c{index}"
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": call_id,
                        "name": "read_file",
                        "args": {"path": f"/p/{index}.java"},
                    }
                ],
            )
        )
        messages.append(
            ToolMessage(content="甲" * chars, tool_call_id=call_id, name="read_file")
        )
    return messages


def _current_step(*, chars: int = 500) -> list[BaseMessage]:
    """末尾这一步：模型刚发起一个工具调用，结果还没被任何压缩动过。"""
    return [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "now", "name": "read_file", "args": {"path": "/p/now.java"}}
            ],
        ),
        ToolMessage(
            content="当前结果" + "甲" * chars, tool_call_id="now", name="read_file"
        ),
    ]


async def _ok_handler(request: ModelRequest) -> ModelResponse:
    return ModelResponse(result=[AIMessage(content="ok")])


class TestTrigger:
    def test_burst_uses_40_percent_of_the_window(self):
        settings = _settings()

        trigger = summarization_defaults(
            _model(), settings, mode=CONTEXT_MODE_BURST
        )["trigger"]

        assert trigger == [("fraction", 0.40), ("tokens", 120_000)]
        # 两条是 OR，实际生效的是更小的那条（绝对上限）
        assert trigger_line(trigger, QWEN_WINDOW) == 120_000

    def test_steady_uses_70_percent_of_the_window(self):
        settings = _settings()

        trigger = summarization_defaults(
            _model(), settings, mode=CONTEXT_MODE_STEADY
        )["trigger"]

        assert trigger == [("fraction", 0.70), ("tokens", 200_000)]
        assert trigger_line(trigger, QWEN_WINDOW) == 200_000

    def test_a_huge_window_compresses_at_the_absolute_cap(self):
        """1M 窗口下，比例线（40 万）不是唯一的线：绝对上限 12 万也必须能触发。

        2026-10-20 真机：coder 一路涨到 194k 都没压 —— 只有比例线时线落在 40 万。
        绝对那条还有个副作用是好的：上游判它时会顺带看供应商自报的
        ``usage_metadata.total_tokens``（真值），所以估算偏了也照样能触发。
        """
        middleware = build_summarization(_model(), _backend(), _settings())
        helper = middleware._lc_helper

        assert helper._should_summarize(_one_message(), 120_001) is True
        assert helper._should_summarize(_one_message(), 119_999) is False

    def test_the_keep_line_is_absolute_too(self):
        """keep 用绝对值：``("fraction", 0.10)`` 在 1M 窗口下等于「压完还留 100k」，
        而触发线才 120k —— 压了等于没压。"""
        middleware = build_summarization(_model(), _backend(), _settings())

        assert middleware._lc_helper.keep == ("tokens", 20_000)

    def test_no_profile_falls_back_to_absolute_only(self):
        """②：没有窗口就不能带 fraction —— 上游 ``__init__`` 会直接 ``ValueError``。"""
        settings = _settings()
        model = _model(window=None)

        assert profile_window(model) is None
        assert summarization_defaults(model, settings)["trigger"] == (
            "tokens",
            settings.context_summary_trigger_tokens,
        )
        # 真的建一遍：这一档要是漏了，建 agent 当场就崩
        assert build_summarization(model, _backend(), settings) is not None

    def test_the_values_come_from_the_settings(self):
        settings = _settings(
            context_burst_trigger_fraction=0.5,
            context_burst_trigger_tokens=6543,
            context_summary_trigger_tokens=4321,
            context_summary_keep_tokens=999,
            context_summary_input_tokens=888,
        )
        helper = build_summarization(
            _model(), _backend(), settings, mode=CONTEXT_MODE_BURST
        )._lc_helper

        assert helper._trigger_clauses == [{"fraction": 0.5}, {"tokens": 6543}]
        assert helper.keep == ("tokens", 999)
        assert helper.trim_tokens_to_summarize == 888

    def test_the_switch_turns_it_off(self):
        settings = _settings(context_summary_enabled=False)

        assert build_summarization(_model(), _backend(), settings) is None


class TestModeMapping:
    def test_burst_roles(self):
        for role in ("plan", "planner", "coder", "reviewer"):
            assert resolve_context_mode(role) == CONTEXT_MODE_BURST

    def test_steady_roles(self):
        for role in ("qa", "coding", "orchestrate", "rework"):
            assert resolve_context_mode(role) == CONTEXT_MODE_STEADY

    def test_the_main_agent_uses_its_form(self):
        assert resolve_context_mode(MAIN_AGENT, "qa") == CONTEXT_MODE_STEADY
        assert resolve_context_mode(MAIN_AGENT, "plan") == CONTEXT_MODE_BURST
        assert resolve_context_mode(MAIN_AGENT, "orchestrate") == CONTEXT_MODE_STEADY
        assert resolve_context_mode(MAIN_AGENT, "coding") == CONTEXT_MODE_STEADY
        assert resolve_context_mode(MAIN_AGENT, "rework") == CONTEXT_MODE_STEADY

    def test_build_summarization_carries_the_resolved_mode(self):
        qa = build_summarization(
            _model(), _backend(), _settings(), agent=MAIN_AGENT, form="qa"
        )
        plan = build_summarization(
            _model(), _backend(), _settings(), agent=MAIN_AGENT, form="plan"
        )
        orchestrate = build_summarization(
            _model(), _backend(), _settings(), agent=MAIN_AGENT, form="orchestrate"
        )

        assert qa._mode == CONTEXT_MODE_STEADY
        assert qa._trigger_text.startswith("稳态：")
        assert plan._mode == CONTEXT_MODE_BURST
        assert plan._trigger_text.startswith("爆发：")
        assert orchestrate._mode == CONTEXT_MODE_STEADY
        assert orchestrate._trigger_text.startswith("稳态：")


class TestTheSummaryCallIsCapped:
    def test_ours_caps_the_summary_input(self):
        """③：摘要那次调用最多吃 ``context_summary_input_tokens``。"""
        middleware = build_summarization(_model(), _backend(), _settings())

        assert middleware._lc_helper.trim_tokens_to_summarize == 40_000

    def test_the_upstream_factory_leaves_it_uncapped(self):
        """③的对照：**上游工厂**给的是 ``None`` ＝完全不裁。

        这条不是测我们的代码，是把那个坑钉在测试里 —— 将来谁想「还是用回上游工厂吧」，
        这条会告诉他那一格的默认值是什么。
        """
        upstream = create_summarization_middleware(_model(window=None), _backend())

        assert upstream._lc_helper.trim_tokens_to_summarize is None


class TestIdentityAndExclusion:
    def test_the_counter_is_ours(self):
        middleware = build_summarization(_model(), _backend(), _settings())
        counter = middleware._lc_helper.token_counter

        # 计数器现在带一层「按模型真值校准」；没有样本时必须与原来的字符估算逐字一致。
        assert isinstance(counter, CalibratedTokenCounter)
        messages = _long_history(2)
        assert counter(messages) == context_token_counter(messages)

    def test_ours_survives_the_exclusion_that_drops_the_upstream_one(self):
        """④：排除是**按类精确匹配**的，所以子类实例不会被一起排掉。"""
        profile = _harness_profile_for_model(_model(), None)
        ours = build_summarization(_model(), _backend(), _settings())
        upstream = create_summarization_middleware(_model(), _backend())

        assert SummarizationMiddleware in profile.excluded_middleware
        kept = _apply_excluded_middleware(
            [upstream, ours], profile, matched_classes=set(), matched_names=set()
        )

        assert kept == [ours]
        assert isinstance(ours, SummarizationMiddleware)
        assert type(ours) is HarnessSummarizationMiddleware

    def test_the_name_is_not_the_upstream_alias(self):
        """字符串形式的排除（``"SummarizationMiddleware"``）也认不到我们这份。"""
        ours = build_summarization(_model(), _backend(), _settings())

        assert ours.name != "SummarizationMiddleware"


class TestPlacement:
    """④的后半：位置。压缩中间件在兜底外侧；**没有**单独的常规剪裁中间件。"""

    def _captured_stack(self, monkeypatch, **kwargs: Any) -> list[Any]:
        seen: dict[str, Any] = {}

        def fake_create_deep_agent(**captured: Any) -> Any:
            seen.update(captured)
            return SimpleNamespace()

        monkeypatch.setattr(graph_mod, "create_deep_agent", fake_create_deep_agent)
        graph_mod.build_agent(
            model=_model(),
            backend=_backend(),
            tools=[],
            store=_backend(),
            checkpointer=_backend(),
            system_prompt="SYS",
            policy=policy_for("coding", _settings()),
            **kwargs,
        )
        return list(seen["middleware"])

    def test_the_main_agent_puts_it_before_the_fallback(self, monkeypatch):
        summary = build_summarization(_model(), _backend(), _settings())
        fallback = SimpleNamespace()

        stack = self._captured_stack(
            monkeypatch, summarization=summary, fallback=fallback
        )

        assert stack.index(summary) < stack.index(fallback)

    def test_the_main_agent_list_carries_only_ours(self, monkeypatch):
        """上游那份由 ``excluded_middleware`` 排掉，所以这里只可能有一个压缩中间件。"""
        summary = build_summarization(_model(), _backend(), _settings())

        stack = self._captured_stack(monkeypatch, summarization=summary)

        assert _summaries(stack) == [summary]

    def test_not_passing_one_means_no_compression(self, monkeypatch):
        """漏传就是「不压缩」，**不会**退回上游默认 —— 那份已经被排掉了。"""
        assert _summaries(self._captured_stack(monkeypatch)) == []

    def test_a_subagent_gets_one_built_from_its_own_model(self):
        """子智能体那份要用**它自己那个模型实例**（触发线按那个模型的窗口算）。"""
        seen: dict[str, Any] = {}

        def summarization_for(role: str, model: Any) -> Any:
            seen[role] = model
            return build_summarization(model, _backend(), _settings())

        specs = build_subagents(
            tools_by_role={"coder": []},
            model_for=_model_for_role,
            policies={"coder": policy_for("coder")},
            summarization_for=summarization_for,
        )
        spec = specs[0]

        assert seen["coder"] is spec["model"], "同一个实例，不是另建一个同配置的"
        assert len(_summaries(spec["middleware"])) == 1

    def test_a_subagent_gets_the_summarization_middleware(self):
        specs = build_subagents(
            tools_by_role={"coder": []},
            model_for=_model_for_role,
            policies={"coder": policy_for("coder")},
            summarization_for=lambda role, model: build_summarization(
                model, _backend(), _settings()
            ),
        )
        stack = specs[0]["middleware"]

        assert len(_summaries(stack)) == 1
        assert not any(
            mw.__class__.__module__.endswith("context_trim") for mw in stack
        ), "不要再挂独立剪裁中间件"

    def test_a_subagent_without_it_simply_does_not_compress(self):
        specs = build_subagents(
            tools_by_role={"coder": []},
            model_for=_model_for_role,
            policies={"coder": policy_for("coder")},
        )

        assert _summaries(specs[0]["middleware"]) == []


class TestTheCompactionIsLogged:
    """压缩那一次到底省了多少，**只有这行日志说得出**。

    上游只把摘要那次调用记成一笔内部用量（``run_log`` 那行「摘要调用完成：输入 X / 输出 Y」），
    既不说压前压后各多长，也不说逐出的原文落到哪 —— 而那两样正是判断触发线该不该调的依据。

    这里把上游那一层换成假的：要验的是**我们这层的记账**，不是上游的压缩流程（上游的取值
    另有 ``TestTrigger`` 那几条钉着）。
    """

    @staticmethod
    def _fake_upstream(sent: list[BaseMessage], event: dict[str, Any] | None) -> Any:
        """装成上游：把 ``sent`` 交给内层；``event`` 不为空就报「这次压了」。"""

        async def fake(self: Any, request: ModelRequest, handler: Any) -> Any:
            response = await handler(request.override(messages=sent))
            if event is None:
                return response
            return ExtendedModelResponse(
                model_response=response,
                command=Command(update={"_summarization_event": event}),
            )

        return fake

    async def test_it_reports_before_after_and_where_the_original_went(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        settings = _settings(
            context_summary_trigger_tokens=1_000, context_summary_keep_tokens=200
        )
        middleware = build_summarization(
            _model(window=None), _backend(), settings, agent=MAIN_AGENT
        )
        history = _long_history()
        summary = HumanMessage(content="摘要正文")
        sent = [summary, *history[-2:]]
        event = {
            "cutoff_index": 28,
            "summary_message": summary,
            "file_path": "/conversation_history/t1.md",
        }
        monkeypatch.setattr(
            SummarizationMiddleware, "awrap_model_call", self._fake_upstream(sent, event)
        )
        before = middleware._count_tokens(history, None, None)

        with caplog.at_level(logging.INFO):
            await middleware.awrap_model_call(_request(history), _ok_handler)

        after = middleware._count_tokens(sent, None, None)
        (record,) = _summary_logs(caplog)
        text = record.getMessage()
        assert before > after
        assert f"{before} → {after} tokens" in text, "压前压后都要是**同口径**的真数"
        assert "消息 30 → 3 条" in text
        assert "/conversation_history/t1.md" in text, "逐出的原文在哪，得能顺着找回去"
        assert str(settings.context_summary_trigger_tokens) in text, "触发线要写在同一行"
        assert record.agent == MAIN_AGENT

    async def test_nothing_is_logged_when_the_context_was_not_touched(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """没压也没截断＝一行都不记：这一层每次模型调用都跑，不能把日志刷掉。"""
        middleware = build_summarization(_model(), _backend(), _settings())

        async def fake(self: Any, request: ModelRequest, handler: Any) -> Any:
            return await handler(request)  # 原样交下去

        monkeypatch.setattr(SummarizationMiddleware, "awrap_model_call", fake)

        with caplog.at_level(logging.INFO):
            await middleware.awrap_model_call(_request(_long_history(4)), _ok_handler)

        assert _summary_logs(caplog) == []

    def test_upstream_argument_truncation_is_disabled(self):
        """历史入参只走显式裁剪事件，不能再被上游截成前 20 字符。"""
        values = summarization_defaults(
            _model(),
            _settings(),
            mode=CONTEXT_MODE_BURST,
        )

        assert values["truncate_args_settings"] is None


class TestPreSummaryTrim:
    """到压缩线先应急裁剪工具返回：剪完不到线就不压，仍超线才压。"""

    @staticmethod
    def _low_trigger_settings(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "context_summary_trigger_tokens": 2_000,
            "context_summary_keep_tokens": 100,
        }
        values.update(overrides)
        return _settings(**values)

    @classmethod
    def _middleware(cls, **overrides: Any) -> HarnessSummarizationMiddleware:
        return build_summarization(
            _model(window=None),
            _backend(),
            cls._low_trigger_settings(**overrides),
            agent="coder",
        )

    async def test_it_skips_compression_when_the_trim_brings_it_below_the_line(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        middleware = self._middleware()
        history = _tool_steps(6) + _current_step()
        seen: dict[str, list[BaseMessage]] = {}

        async def handler(request: ModelRequest) -> ModelResponse:
            seen["messages"] = list(request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        async def boom(self: Any, request: ModelRequest, handler: Any) -> Any:
            raise AssertionError("剪完已低于压缩线，不该调用上游压缩")

        monkeypatch.setattr(SummarizationMiddleware, "awrap_model_call", boom)

        with caplog.at_level(logging.INFO):
            result = await middleware.awrap_model_call(_request(history), handler)

        tools = [m for m in seen["messages"] if isinstance(m, ToolMessage)]
        assert tools[-1].content.startswith("当前结果"), "当前这一步必须原样保留"
        assert all(m.content.startswith("〔已裁剪〕") for m in tools[:-1])
        assert isinstance(result, ExtendedModelResponse)
        event = result.command.update["_context_trim_event"]
        assert event["trimmed_result_ids"] == [f"c{i}" for i in range(6)]
        (record,) = _pretrim_logs(caplog)
        text = record.getMessage()
        assert "工具结果 6 条" in text
        assert "已低于压缩线且落在安全区，本轮不压缩" in text
        assert record.agent == "coder"

    @staticmethod
    def _fake_summary(monkeypatch: pytest.MonkeyPatch, *, fail_times: int = 0):
        """把上游压缩换成可控假实现：记录调用次数，可选前 N 次抛错。"""
        state = {"calls": 0}

        async def fake(self: Any, request: ModelRequest, handler: Any) -> Any:
            state["calls"] += 1
            if state["calls"] <= fail_times:
                raise RuntimeError("摘要失败")
            response = await handler(
                request.override(
                    messages=[HumanMessage(content="摘要"), *request.messages[-1:]]
                )
            )
            return ExtendedModelResponse(
                model_response=response,
                command=Command(
                    update={
                        "_summarization_event": {
                            "cutoff_index": 1,
                            "summary_message": HumanMessage(content="摘要"),
                            "file_path": None,
                        }
                    }
                ),
            )

        monkeypatch.setattr(SummarizationMiddleware, "awrap_model_call", fake)
        return state

    async def test_a_persisted_event_is_reused_without_replanning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """checkpoint 里的裁剪事件是持久投影：第二次调用不重新生成同一批 ID。"""
        middleware = self._middleware()
        history = _tool_steps(6) + _current_step()
        summary_state = self._fake_summary(monkeypatch)

        first = await middleware.awrap_model_call(_request(history), _ok_handler)
        assert isinstance(first, ExtendedModelResponse)
        event = first.command.update["_context_trim_event"]

        seen: dict[str, list[BaseMessage]] = {}

        async def handler(request: ModelRequest) -> ModelResponse:
            seen["messages"] = list(request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        caplog.clear()
        with caplog.at_level(logging.INFO):
            second = await middleware.awrap_model_call(
                _request(history, state={"_context_trim_event": event}),
                handler,
            )

        tools = [m for m in seen["messages"] if isinstance(m, ToolMessage)]
        assert all(m.content.startswith("〔已裁剪〕") for m in tools[:-1])
        assert tools[-1].content.startswith("当前结果")
        assert not isinstance(second, ExtendedModelResponse), "没有新事件需要写回"
        assert summary_state["calls"] == 0
        assert _pretrim_logs(caplog) == [], "已有事件已经生效，不该重复裁剪"

    async def test_the_total_switch_ignores_and_clears_an_existing_event(self):
        middleware = build_summarization(
            _model(window=None),
            _backend(),
            _settings(
                context_summary_trigger_tokens=100_000,
                context_summary_keep_tokens=100,
                context_trim_enabled=False,
            ),
            agent="coder",
        )
        history = _tool_steps(1) + _current_step()
        event = {
            "version": 1,
            "trimmed_result_ids": ["c0"],
            "compacted_arg_ids": [],
        }
        seen: dict[str, list[BaseMessage]] = {}

        async def handler(request: ModelRequest) -> ModelResponse:
            seen["messages"] = list(request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        result = await middleware.awrap_model_call(
            _request(history, state={"_context_trim_event": event}),
            handler,
        )

        assert seen["messages"][1].content == "甲" * 4000, "总开关关闭时不应用旧投影"
        assert isinstance(result, ExtendedModelResponse)
        assert result.command.update["_context_trim_event"] is None

    async def test_a_large_argument_can_enter_the_event_while_its_result_stays(self):
        middleware = self._middleware(context_trim_clear_inputs=True)
        history = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "write",
                        "name": "write_file",
                        "args": {
                            "file_path": "/p/a.java",
                            "content": "x" * 8_000,
                        },
                    }
                ],
            ),
            ToolMessage(content="ok", tool_call_id="write", name="write_file"),
            *_current_step(),
        ]

        result = await middleware.awrap_model_call(_request(history), _ok_handler)

        assert isinstance(result, ExtendedModelResponse)
        event = result.command.update["_context_trim_event"]
        assert event["trimmed_result_ids"] == [], "小结果不裁"
        assert event["compacted_arg_ids"] == ["write"]

    async def test_no_new_candidates_go_straight_to_summary(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """旧结果全在事件里、当前步骤又不能裁时，没有新增收益，直接摘要。"""
        middleware = self._middleware()
        history = _tool_steps(6) + _current_step(chars=4_000)
        event = {
            "version": 1,
            "trimmed_result_ids": [f"c{i}" for i in range(6)],
            "compacted_arg_ids": [],
        }
        seen: dict[str, list[BaseMessage]] = {}

        async def fake(self: Any, request: ModelRequest, handler: Any) -> Any:
            seen["messages"] = list(request.messages)
            response = await handler(
                request.override(
                    messages=[HumanMessage(content="摘要"), *request.messages[-1:]]
                )
            )
            return ExtendedModelResponse(
                model_response=response,
                command=Command(
                    update={
                        "_summarization_event": {
                            "cutoff_index": 1,
                            "summary_message": HumanMessage(content="摘要"),
                            "file_path": None,
                        }
                    }
                ),
            )

        monkeypatch.setattr(SummarizationMiddleware, "awrap_model_call", fake)

        with caplog.at_level(logging.INFO):
            await middleware.awrap_model_call(
                _request(history, state={"_context_trim_event": event}),
                _ok_handler,
            )

        assert seen["messages"][1].content == "甲" * 4000, (
            "摘要与 offload 必须看到原始工具结果，不能复用裁剪事件投影"
        )
        assert any(
            "没有新的大工具结果或大入参可裁" in record.getMessage()
            for record in _pretrim_logs(caplog)
        )
        assert _summary_logs(caplog)

    async def test_it_compresses_when_the_trim_stays_above_the_safe_zone(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """只压到安全区外（>70%）不算免压：原历史还在，下一次会原样重剪。

        2026-10-20 真机：coder 每次 121~135k 剪到 103~117k，刚好低于 120k 的线，
        于是一轮 38 次原地裁剪、一次摘要都没有。这里用 3000 字当前步骤造同样的形状：
        低于 2000 的线、但高于 70%（1400）。
        """
        middleware = self._middleware()
        history = _tool_steps(1) + _current_step(chars=2_600)
        seen: dict[str, list[BaseMessage]] = {}

        async def fake(self: Any, request: ModelRequest, handler: Any) -> Any:
            seen["messages"] = list(request.messages)
            response = await handler(
                request.override(
                    messages=[HumanMessage(content="摘要"), *request.messages[-1:]]
                )
            )
            return ExtendedModelResponse(
                model_response=response,
                command=Command(
                    update={
                        "_summarization_event": {
                            "cutoff_index": 1,
                            "summary_message": HumanMessage(content="摘要"),
                            "file_path": None,
                        }
                    }
                ),
            )

        monkeypatch.setattr(SummarizationMiddleware, "awrap_model_call", fake)

        with caplog.at_level(logging.INFO):
            await middleware.awrap_model_call(_request(history), _ok_handler)

        (record,) = _pretrim_logs(caplog)
        assert "仅勉强低于压缩线（>70%），继续压缩" in record.getMessage()
        assert _summary_logs(caplog), "勉强压到线边时必须真的摘要"

    async def test_it_compresses_from_the_untrimmed_baseline_when_the_trim_is_not_enough(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        middleware = self._middleware()
        history = _tool_steps(1) + _current_step(chars=4_000)
        seen: dict[str, list[BaseMessage]] = {}

        async def fake(self: Any, request: ModelRequest, handler: Any) -> Any:
            seen["messages"] = list(request.messages)
            response = await handler(
                request.override(
                    messages=[HumanMessage(content="摘要"), *request.messages[-1:]]
                )
            )
            return ExtendedModelResponse(
                model_response=response,
                command=Command(
                    update={
                        "_summarization_event": {
                            "cutoff_index": 1,
                            "summary_message": HumanMessage(content="摘要"),
                            "file_path": None,
                        }
                    }
                ),
            )

        monkeypatch.setattr(SummarizationMiddleware, "awrap_model_call", fake)

        with caplog.at_level(logging.INFO):
            await middleware.awrap_model_call(_request(history), _ok_handler)

        old = next(
            m
            for m in seen["messages"]
            if isinstance(m, ToolMessage) and m.tool_call_id == "c0"
        )
        assert old.content.startswith("甲"), "继续压缩时用原文，不把预剪占位符带进摘要"
        (record,) = _pretrim_logs(caplog)
        assert "仍高于压缩线，继续压缩" in record.getMessage()
        assert _summary_logs(caplog), "真正压缩时仍要记 CONTEXT_SUMMARY"

    async def test_reported_tokens_alone_do_not_force_compression(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        middleware = self._middleware()
        history = [
            HumanMessage(content="短短一句"),
            AIMessage(
                content="上一轮回复",
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150_000,
                },
                response_metadata={"model_provider": "openai"},
            ),
        ]
        seen: dict[str, list[BaseMessage]] = {}

        async def handler(request: ModelRequest) -> ModelResponse:
            seen["messages"] = list(request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        async def boom(self: Any, request: ModelRequest, handler: Any) -> Any:
            raise AssertionError("估算值没到线，不该压")

        monkeypatch.setattr(SummarizationMiddleware, "awrap_model_call", boom)

        with caplog.at_level(logging.INFO):
            await middleware.awrap_model_call(_request(history), handler)

        assert seen, "应直接调用模型"
        (record,) = _pretrim_logs(caplog)
        assert "只有供应商自报的上一轮用量过线" in record.getMessage()

    async def test_an_overflow_after_the_skip_falls_back_to_compression(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        middleware = self._middleware()
        history = _tool_steps(6) + _current_step()
        calls = {"n": 0}
        seen: dict[str, list[BaseMessage]] = {}

        async def handler(request: ModelRequest) -> ModelResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ContextOverflowError("too big")
            return ModelResponse(result=[AIMessage(content="ok")])

        async def fake(self: Any, request: ModelRequest, handler: Any) -> Any:
            seen["messages"] = list(request.messages)
            response = await handler(
                request.override(
                    messages=[HumanMessage(content="摘要"), *request.messages[-1:]]
                )
            )
            return ExtendedModelResponse(
                model_response=response,
                command=Command(
                    update={
                        "_summarization_event": {
                            "cutoff_index": 1,
                            "summary_message": HumanMessage(content="摘要"),
                            "file_path": None,
                        }
                    }
                ),
            )

        monkeypatch.setattr(SummarizationMiddleware, "awrap_model_call", fake)

        with caplog.at_level(logging.INFO):
            await middleware.awrap_model_call(_request(history), handler)

        assert seen, "溢出后必须改走压缩"
        assert calls["n"] == 2
        assert any(
            "真实调用溢出，改走压缩" in record.getMessage()
            for record in _pretrim_logs(caplog)
        )

    def test_the_estimate_check_ignores_reported_usage(self):
        middleware = self._middleware()
        history = [
            AIMessage(
                content="x",
                usage_metadata={
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 150_000,
                },
                response_metadata={"model_provider": "openai"},
            )
        ]

        assert middleware._should_summarize(history, 10) is True
        assert middleware._should_summarize_estimate(history, 10) is False

    def test_the_trim_switch_and_input_flag_are_wired(self):
        on = self._middleware()
        off = self._middleware(context_trim_enabled=False)
        flagged = self._middleware(context_trim_clear_inputs=True)
        unflagged = self._middleware(context_trim_clear_inputs=False)

        assert on._pretrim is True
        # 2026-10-22 起默认关：只压超大字段的可选策略，不再整体清空 args。
        assert on._pretrim_compact_inputs is False
        assert off._pretrim is False
        assert flagged._pretrim_compact_inputs is True
        assert unflagged._pretrim_compact_inputs is False
        assert on._pretrim_min_result_chars == 2_000
        assert on._pretrim_min_arg_chars == 2_000
        assert "_context_trim_event" in HarnessSummarizationMiddleware.state_schema.__annotations__


class TestDescribeTrigger:
    """日志里的触发线写**算出来的数**：fraction 那条只有乘上窗口才有意义。"""

    def test_the_fraction_is_multiplied_by_the_window(self):
        settings = _settings()

        text = describe_trigger(
            summarization_defaults(
                _model(), settings, mode=CONTEXT_MODE_BURST
            )["trigger"],
            QWEN_WINDOW,
        )

        assert text == (
            f"窗口 {QWEN_WINDOW}×{settings.context_burst_trigger_fraction}"
            f"={int(QWEN_WINDOW * settings.context_burst_trigger_fraction)}"
            f" 或 {settings.context_burst_trigger_tokens} tokens"
        )

    def test_without_a_window_only_the_absolute_line_is_there(self):
        assert describe_trigger([("tokens", 120_000)], None) == "120000 tokens"

    def test_the_middleware_carries_the_line_it_was_built_with(self):
        middleware = build_summarization(_model(), _backend(), _settings(), agent="coder")

        assert middleware._trigger_text.startswith("爆发：窗口 ")
        assert middleware._agent == "coder"


class TestSummaryPrompt:
    def test_the_must_keep_section_is_before_the_messages(self):
        middleware = build_summarization(_model(), _backend(), _settings())
        prompt = middleware._lc_helper.summary_prompt

        assert "必须保留（MUST KEEP）" in prompt
        assert "SESSION INTENT" in prompt, "默认摘要结构不能丢"
        assert "tool_call_id" in prompt
        assert "必须打码" in prompt
        assert prompt.index("必须保留") < prompt.index("<messages>")
        assert "MESSAGES" in prompt.format(messages="MESSAGES")


class TestTheWorkingNoteNotice:
    """90% 提醒：不额外调模型，让模型在本轮回复里写工作笔记 + KEEP 清单。"""

    @staticmethod
    def _notice_middleware():
        return build_summarization(
            _model(window=None),
            _backend(),
            _settings(
                context_summary_trigger_tokens=1_000,
                context_pretrim_notice_ratio=0.9,
            ),
            agent="coder",
        )

    def test_the_notice_is_off_when_tool_trim_is_off(self):
        """提醒只为应急裁剪服务；裁剪总开关关掉时，不要再要求模型写 KEEP。"""
        middleware = build_summarization(
            _model(),
            _backend(),
            _settings(
                context_summary_trigger_tokens=1_000,
                context_pretrim_notice_ratio=0.9,
                context_trim_enabled=False,
            ),
            agent="coder",
        )

        assert middleware._notice_at == 0

    def test_it_is_injected_once_per_agent_and_carries_the_keep_format(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from ai_coding_harness.agent.middleware import summarization as summarization_module

        state = SimpleNamespace(pretrim_noticed=set())
        monkeypatch.setattr(
            summarization_module,
            "get_configurable",
            lambda key, default=None: state if key == "turn_state" else default,
        )
        middleware = self._notice_middleware()
        request = _request([HumanMessage(content="甲" * 4000)])

        first, sent = middleware._maybe_notice(request, 1_000)
        assert sent
        assert "即将上下文裁剪" in first.system_message.text
        assert "<working-note>" in first.system_message.text
        assert "KEEP:" in first.system_message.text
        assert state.pretrim_noticed == {"coder"}

        second, again = middleware._maybe_notice(request, 1_000)
        assert not again and second is request

    async def test_the_notice_reaches_the_model_call_without_trimming_yet(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from ai_coding_harness.agent.middleware import summarization as summarization_module

        state = SimpleNamespace(pretrim_noticed=set())
        monkeypatch.setattr(
            summarization_module,
            "get_configurable",
            lambda key, default=None: state if key == "turn_state" else default,
        )
        middleware = self._notice_middleware()
        history = _tool_steps(2) + _current_step()
        seen: dict[str, Any] = {}

        async def handler(request: ModelRequest) -> ModelResponse:
            seen["system"] = request.system_message.text
            seen["messages"] = list(request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        await middleware.awrap_model_call(_request(history), handler)

        assert "即将上下文裁剪" in seen["system"]
        tools = [m for m in seen["messages"] if isinstance(m, ToolMessage)]
        assert len(tools) == 3
        assert all(not m.content.startswith("〔已裁剪〕") for m in tools), (
            "提醒这一轮先不裁；下一轮才按 KEEP 执行"
        )

    async def test_the_trim_keeps_what_the_note_names(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        middleware = build_summarization(
            _model(window=None),
            _backend(),
            _settings(
                context_summary_trigger_tokens=5_000,
                context_summary_keep_tokens=100,
                context_pretrim_notice_ratio=0.0,
            ),
            agent="coder",
        )
        history = _tool_steps(2) + [
            AIMessage(content="<working-note>\nKEEP: c0\n</working-note>")
        ] + _current_step()
        seen: dict[str, list[BaseMessage]] = {}

        async def handler(request: ModelRequest) -> ModelResponse:
            seen["messages"] = list(request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        async def boom(self: Any, request: ModelRequest, handler: Any) -> Any:
            raise AssertionError("剪完已低于压缩线，不该调用上游压缩")

        monkeypatch.setattr(SummarizationMiddleware, "awrap_model_call", boom)
        await middleware.awrap_model_call(_request(history), handler)

        tools = [m for m in seen["messages"] if isinstance(m, ToolMessage)]
        by_id = {m.tool_call_id: m.content for m in tools}
        assert by_id["c0"] == "甲" * 4000
        assert by_id["c1"].startswith("〔已裁剪〕")
        assert by_id["now"].startswith("当前结果")
