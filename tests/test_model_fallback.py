"""兜底：切模型的行为 + 留痕 + 装配位置。

留痕的目标是「事后能查出一轮到底跑在哪家模型上」—— 上游 ``ModelFallbackMiddleware``
兜底成功时一声不响，这条痕是它唯一的记录点，所以断言要钉到**字段**（角色、两个模型名、
异常类型/状态码），而不只是「有日志」。

脱敏那条红线也钉在这里：异常摘要只取类型与状态码，**不取原文** —— 供应商的报错体可能
把 api key 原样回显。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from ai_coding_harness.agent import graph as graph_module
from ai_coding_harness.agent.graph import build_agent
from ai_coding_harness.agent.llm import build_fallback
from ai_coding_harness.agent.middleware import (
    FALLBACK_LOG_TAG,
    FallbackMiddleware,
    HistoryRepairMiddleware,
)
from ai_coding_harness.agent.middleware import fallback as fallback_module
from ai_coding_harness.agent.middleware.fallback import (
    CIRCUIT_LOG_TAG,
    _error_summary,
    reset_circuits,
)
from ai_coding_harness.agent.profiles import ORCHESTRATE_FORM
from ai_coding_harness.agent.subagents import build_subagents
from ai_coding_harness.config import Settings
from ai_coding_harness.observability.logging import MAIN_AGENT
from ai_coding_harness.safety.policy import policy_for
from ai_coding_harness.session.manager import SessionManager

# 只构建图 / 只建客户端对象，都不发请求：假 key + 不可达的 base_url
_UNREACHABLE = "http://127.0.0.1:1/v1"


def _model(name: str) -> BaseChatModel:
    return init_chat_model(
        model=name,
        model_provider="openai",
        api_key="dummy",
        base_url=_UNREACHABLE,
    )


def _rate_limited(message: str) -> Exception:
    """真 SDK 的异常形状（带 ``status_code``、原文里常夹带密钥）。"""
    return openai.RateLimitError(
        message,
        response=httpx.Response(
            429, request=httpx.Request("POST", f"{_UNREACHABLE}/chat/completions")
        ),
        body=None,
    )


def _request(primary: BaseChatModel) -> ModelRequest:
    return ModelRequest(model=primary, messages=[])


def _logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if FALLBACK_LOG_TAG in r.getMessage()]


def _ok(text: str) -> ModelResponse:
    return ModelResponse(result=[AIMessage(content=text)])


# -- 中间件行为 --------------------------------------------------------------


class TestFallbackMiddleware:
    async def test_primary_failure_switches_to_the_fallback_and_logs(
        self, caplog: pytest.LogCaptureFixture
    ):
        primary, fallback = _model("glm-5.3-flash"), _model("deepseek-v4-pro")
        middleware = FallbackMiddleware(
            role="plan", agent=MAIN_AGENT, fallback=fallback
        )
        tried: list[str] = []

        async def handler(request: ModelRequest) -> ModelResponse:
            name = request.model.model_name  # type: ignore[union-attr]
            tried.append(name)
            if name == "glm-5.3-flash":
                raise _rate_limited("主导挂了")
            return _ok("兜底答的")

        with caplog.at_level(logging.WARNING):
            out = await middleware.awrap_model_call(_request(primary), handler)

        assert [m.content for m in out.result] == ["兜底答的"]
        assert tried == ["glm-5.3-flash", "deepseek-v4-pro"], "先主导、后兜底"

        (record,) = _logs(caplog)
        assert record.levelname == "WARNING"
        assert "role=plan" in record.getMessage()
        assert "primary=glm-5.3-flash" in record.getMessage()
        assert "fallback=deepseek-v4-pro" in record.getMessage()
        assert "error=RateLimitError/429" in record.getMessage()
        # 主控那一行要落进 %(agent)s 列（与其它运行日志同口径），不是「-」
        assert record.agent == MAIN_AGENT

    async def test_success_writes_nothing_and_never_touches_the_fallback(
        self, caplog: pytest.LogCaptureFixture
    ):
        primary, fallback = _model("glm-5.3-flash"), _model("deepseek-v4-pro")
        middleware = FallbackMiddleware(
            role="plan", agent=MAIN_AGENT, fallback=fallback
        )
        tried: list[str] = []

        async def handler(request: ModelRequest) -> ModelResponse:
            name = request.model.model_name  # type: ignore[union-attr]
            tried.append(name)
            if name != "glm-5.3-flash":
                raise AssertionError("主导成功时不该动兜底")
            return _ok("正常答的")

        with caplog.at_level(logging.WARNING):
            out = await middleware.awrap_model_call(_request(primary), handler)

        assert [m.content for m in out.result] == ["正常答的"]
        assert tried == ["glm-5.3-flash"]
        assert _logs(caplog) == []

    async def test_all_failed_raises_the_fallback_error_then_logs_once(
        self, caplog: pytest.LogCaptureFixture
    ):
        """上游抛的是**兜底那次**的异常（``raise last_exception``）—— 容易误读成主导的。"""
        primary, fallback = _model("glm-5.3-flash"), _model("deepseek-v4-pro")
        middleware = FallbackMiddleware(
            role="coder", agent="coder", fallback=fallback
        )

        async def handler(request: ModelRequest) -> ModelResponse:
            name = request.model.model_name  # type: ignore[union-attr]
            raise _rate_limited("主导挂了" if name == "glm-5.3-flash" else "兜底也挂了")

        with caplog.at_level(logging.WARNING), pytest.raises(openai.RateLimitError) as excinfo:
            await middleware.awrap_model_call(_request(primary), handler)

        assert "兜底也挂了" in str(excinfo.value)

        records = _logs(caplog)
        assert len(records) == 1, "只有一次兜底尝试，只该留一条痕"
        assert "role=coder" in records[0].getMessage()
        assert records[0].agent == "coder"

    def test_error_summary_never_carries_the_raw_text(self):
        """红线：报错原文可能带密钥，摘要只留类型与状态码。"""
        exc = _rate_limited("invalid api key: sk-should-not-be-logged")

        assert _error_summary(exc) == "RateLimitError/429"
        assert "sk-should-not-be-logged" not in _error_summary(exc)

    def test_error_summary_without_a_status_code(self):
        assert _error_summary(ValueError("boom")) == "ValueError"
        assert _error_summary(None) == "-"


# -- 熔断（自己写的状态机）---------------------------------------------------


class _Clock:
    """可控时钟：验「冷却到点放探针」不用真等 60 秒。"""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _circuit_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if CIRCUIT_LOG_TAG in r.getMessage()]


class TestCircuitBreaker:
    """主导连续失败到阈值就开闸；冷却期内不再撞它，冷却到点放一次探针。"""

    @pytest.fixture(autouse=True)
    def _isolated(self):
        """状态挂在模块级字典上，会跨用例串味。"""
        reset_circuits()
        yield
        reset_circuits()

    def _middleware(self, *, threshold: int, cooldown: float = 60.0) -> FallbackMiddleware:
        return FallbackMiddleware(
            role="plan",
            agent=MAIN_AGENT,
            fallback=_model("deepseek-v4-pro"),
            circuit_threshold=threshold,
            circuit_cooldown=cooldown,
        )

    def _handler(self, tried: list[str], *, primary_fails: list[bool]) -> Any:
        """``primary_fails`` 是长度 1 的可变开关：测试中途翻转它模拟「主导恢复」。"""

        async def handler(request: ModelRequest) -> ModelResponse:
            name = request.model.model_name  # type: ignore[union-attr]
            tried.append(name)
            if name == "glm-5.3-flash":
                if primary_fails[0]:
                    raise _rate_limited("主导挂了")
                return _ok("主导恢复了")
            return _ok("兜底答的")

        return handler

    async def test_opens_at_the_threshold_then_stops_touching_the_primary(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: Any
    ):
        clock = _Clock()
        monkeypatch.setattr(fallback_module, "_now", clock)
        middleware = self._middleware(threshold=2)
        tried: list[str] = []
        handler = self._handler(tried, primary_fails=[True])
        primary = _model("glm-5.3-flash")

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                out = await middleware.awrap_model_call(_request(primary), handler)
            clock.advance(10)
            await middleware.awrap_model_call(_request(primary), handler)

        assert [m.content for m in out.result] == ["兜底答的"]
        assert tried == [
            # 前两轮各撞一次主导（第 2 次撞完到阈值，当轮就开闸）
            "glm-5.3-flash", "deepseek-v4-pro",
            "glm-5.3-flash", "deepseek-v4-pro",
            # 第 3、4 轮一下都没撞主导 —— 这就是熔断省掉的那段等待
            "deepseek-v4-pro",
            "deepseek-v4-pro",
        ]

        opened, skipped = _circuit_logs(caplog)[0], _circuit_logs(caplog)[-1]
        assert "primary=glm-5.3-flash" in opened.getMessage()
        assert "state=open" in opened.getMessage()
        assert "failures=2" in opened.getMessage()
        assert "cooldown=60s" in opened.getMessage()
        assert opened.agent == MAIN_AGENT
        assert "state=open" in skipped.getMessage()
        assert "age=10s" in skipped.getMessage(), "skip 那行要能看出开闸多久了"
        assert "fallback=deepseek-v4-pro" in skipped.getMessage()
        # 熔断期的轮次不再有 MODEL_FALLBACK 那行 —— 路由由上面这行交代
        assert len(_logs(caplog)) == 2

    async def test_a_success_clears_the_count(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: Any
    ):
        """计数是**连续**失败：中间成功一次就该归零，否则零散故障会攒出一场熔断。"""
        monkeypatch.setattr(fallback_module, "_now", _Clock())
        middleware = self._middleware(threshold=2)
        tried: list[str] = []
        fails = [True]
        handler = self._handler(tried, primary_fails=fails)
        primary = _model("glm-5.3-flash")

        with caplog.at_level(logging.WARNING):
            await middleware.awrap_model_call(_request(primary), handler)  # 失败 1
            fails[0] = False
            await middleware.awrap_model_call(_request(primary), handler)  # 成功 → 归零
            fails[0] = True
            await middleware.awrap_model_call(_request(primary), handler)  # 失败 1，不是 2

        assert tried.count("glm-5.3-flash") == 3, "三次都该撞主导"
        # 不归零的话第 3 次就到阈值了 —— 这条断言就是「计数是连续的」的判据
        assert _circuit_logs(caplog) == []

    async def test_threshold_zero_turns_it_off(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: Any
    ):
        monkeypatch.setattr(fallback_module, "_now", _Clock())
        middleware = self._middleware(threshold=0)
        tried: list[str] = []
        handler = self._handler(tried, primary_fails=[True])
        primary = _model("glm-5.3-flash")

        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                await middleware.awrap_model_call(_request(primary), handler)

        assert tried.count("glm-5.3-flash") == 5, "关掉熔断＝退回每轮都撞一遍"
        assert _circuit_logs(caplog) == []

    async def test_the_probe_closes_the_circuit_when_the_primary_is_back(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: Any
    ):
        clock = _Clock()
        monkeypatch.setattr(fallback_module, "_now", clock)
        middleware = self._middleware(threshold=1)
        tried: list[str] = []
        fails = [True]
        handler = self._handler(tried, primary_fails=fails)
        primary = _model("glm-5.3-flash")

        with caplog.at_level(logging.WARNING):
            await middleware.awrap_model_call(_request(primary), handler)  # 撞死 → 开闸
            await middleware.awrap_model_call(_request(primary), handler)  # 冷却中：直接兜底
            clock.advance(60)  # 到点
            fails[0] = False
            out = await middleware.awrap_model_call(_request(primary), handler)  # 探针

        assert [m.content for m in out.result] == ["主导恢复了"]
        assert tried == [
            "glm-5.3-flash", "deepseek-v4-pro",
            "deepseek-v4-pro",
            "glm-5.3-flash",
        ]
        assert "state=closed" in _circuit_logs(caplog)[-1].getMessage()

    async def test_the_probe_reopens_the_circuit_when_the_primary_is_still_down(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: Any
    ):
        clock = _Clock()
        monkeypatch.setattr(fallback_module, "_now", clock)
        middleware = self._middleware(threshold=1)
        tried: list[str] = []
        handler = self._handler(tried, primary_fails=[True])
        primary = _model("glm-5.3-flash")

        with caplog.at_level(logging.WARNING):
            await middleware.awrap_model_call(_request(primary), handler)
            await middleware.awrap_model_call(_request(primary), handler)
            clock.advance(60)
            await middleware.awrap_model_call(_request(primary), handler)  # 探针又撞死
            await middleware.awrap_model_call(_request(primary), handler)  # 重新开闸 → 直接兜底

        assert tried == [
            "glm-5.3-flash", "deepseek-v4-pro",
            "deepseek-v4-pro",
            "glm-5.3-flash", "deepseek-v4-pro",
            "deepseek-v4-pro",
        ]
        states = [
            "reopen" if "state=reopen" in r.getMessage() else "open"
            for r in _circuit_logs(caplog)
        ]
        assert states == ["open", "open", "reopen", "open"], "探针失败＝重新计时，不是加长"

    async def test_only_the_primary_is_counted_and_the_fallback_is_not(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: Any
    ):
        """兜底的失败记到主导账上的话，一家坏会连带把另一家的闸也拉下来。"""
        monkeypatch.setattr(fallback_module, "_now", _Clock())
        middleware = self._middleware(threshold=2)
        tried: list[str] = []
        primary = _model("glm-5.3-flash")
        fallback_fails = [True]

        async def handler(request: ModelRequest) -> ModelResponse:
            name = request.model.model_name  # type: ignore[union-attr]
            tried.append(name)
            if name == "glm-5.3-flash" or fallback_fails[0]:
                raise _rate_limited(f"{name} 挂了")
            return _ok("兜底答的")

        with caplog.at_level(logging.WARNING):
            with pytest.raises(openai.RateLimitError):  # 两家都挂了，这次整轮失败
                await middleware.awrap_model_call(_request(primary), handler)
            fallback_fails[0] = False
            await middleware.awrap_model_call(_request(primary), handler)  # 主导第 2 次失败
            await middleware.awrap_model_call(_request(primary), handler)  # 开闸 → 直接兜底

        # 若兜底那次也算数，第 2 轮就不会再撞主导（tried 里会少一个主导）
        assert tried == [
            "glm-5.3-flash", "deepseek-v4-pro",
            "glm-5.3-flash", "deepseek-v4-pro",
            "deepseek-v4-pro",
        ]
        opened = _circuit_logs(caplog)[0].getMessage()
        assert "failures=2" in opened, "两轮 = 主导两次失败，兜底那次不在账上"

    async def test_a_control_flow_exception_is_neither_counted_nor_sticky(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: Any
    ):
        """GraphBubbleUp 是暂停/恢复这类控制流，跟供应商健康无关：不计数、原样抛。"""
        clock = _Clock()
        monkeypatch.setattr(fallback_module, "_now", clock)
        middleware = self._middleware(threshold=1)
        tried: list[str] = []
        bubble = [True]
        primary = _model("glm-5.3-flash")

        async def handler(request: ModelRequest) -> ModelResponse:
            name = request.model.model_name  # type: ignore[union-attr]
            tried.append(name)
            if name == "glm-5.3-flash" and bubble[0]:
                raise GraphBubbleUp("控制流")
            return _ok("答了")

        with caplog.at_level(logging.WARNING), pytest.raises(GraphBubbleUp):
            await middleware.awrap_model_call(_request(primary), handler)

        bubble[0] = False
        with caplog.at_level(logging.WARNING):
            out = await middleware.awrap_model_call(_request(primary), handler)

        assert tried == ["glm-5.3-flash", "glm-5.3-flash"], "没被当成失败，主导照撞"
        assert [m.content for m in out.result] == ["答了"]
        assert _circuit_logs(caplog) == []

    async def test_the_probe_flag_is_released_when_the_probe_is_interrupted(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: Any
    ):
        """探针撞上控制流异常时没有结论 —— 不放掉标志，这个熔断窗就再也不会被探。"""
        clock = _Clock()
        monkeypatch.setattr(fallback_module, "_now", clock)
        middleware = self._middleware(threshold=1)
        tried: list[str] = []
        mode = ["down"]
        primary = _model("glm-5.3-flash")

        async def handler(request: ModelRequest) -> ModelResponse:
            name = request.model.model_name  # type: ignore[union-attr]
            tried.append(name)
            if name != "glm-5.3-flash":
                return _ok("兜底答的")
            if mode[0] == "down":
                raise _rate_limited("主导挂了")
            if mode[0] == "bubble":
                raise GraphBubbleUp("控制流")
            return _ok("主导恢复了")

        with caplog.at_level(logging.WARNING):
            await middleware.awrap_model_call(_request(primary), handler)  # 撞死 → 开闸
            clock.advance(60)
            mode[0] = "bubble"
            with pytest.raises(GraphBubbleUp):  # 控制流异常不被兜底吸收
                await middleware.awrap_model_call(_request(primary), handler)
            mode[0] = "ok"
            out = await middleware.awrap_model_call(_request(primary), handler)  # 还能再探

        assert tried == [
            "glm-5.3-flash", "deepseek-v4-pro",  # 第 1 轮：撞死 + 兜底接手
            "glm-5.3-flash",  # 第 2 轮：探针被中断
            "glm-5.3-flash",  # 第 3 轮：还探得到（标志已放掉）
        ]
        assert [m.content for m in out.result] == ["主导恢复了"]
        assert "state=closed" in _circuit_logs(caplog)[-1].getMessage()


# -- 兜底模型怎么挑（llm.build_fallback）------------------------------------


def _settings(**over: Any) -> Settings:
    """四家都给上假配置（显式给全，不受本机 .env 影响）。"""
    base: dict[str, Any] = {
        "deepseek_model_name": "deepseek-v4-pro",
        "deepseek_api_key": "k-deepseek",
        "deepseek_base_url": "https://ds.example/v1",
        "deepseek_context_window": 1_048_576,
        "kimi_model_name": "kimi-k2.6",
        "kimi_api_key": "k-kimi",
        "kimi_base_url": "https://kimi.example/v1",
        "kimi_context_window": 262_144,
        "glm_model_name": "glm-5.3-flash",
        "glm_api_key": "k-glm",
        "glm_base_url": "https://glm.example/v1",
        "glm_context_window": 128_000,
        "gpt_model_name": "gpt-5.6-luna",
        "gpt_api_key": "k-gpt",
        "gpt_base_url": "https://gpt.example/v1",
        "gpt_context_window": 128_000,
    }
    base.update(over)
    return Settings(**base)


class TestBuildFallback:
    def test_takes_the_provider_own_config(self):
        """温度/窗口都得取**目标那家**的：kimi 收 0.3 直接 400，不能沿用主导的。"""
        model = build_fallback(_settings(), role="reviewer")  # reviewer 主导是 kimi

        assert model is not None
        assert model.model_name == "deepseek-v4-pro"
        assert model.temperature == 0.3  # deepseek 的口径，不是 kimi 的 1
        assert model.profile == {"max_input_tokens": 1_048_576}

    def test_no_fallback_when_the_role_already_runs_on_it(self):
        assert build_fallback(_settings(), role="coder") is None

    def test_empty_provider_turns_the_feature_off(self):
        assert build_fallback(_settings(fallback_provider=""), role="reviewer") is None

    def test_unknown_provider_fails_loudly(self):
        """静默的后果是「以为有兜底，其实一直没挂」，比当场失败难查。"""
        with pytest.raises(ValueError, match="FALLBACK_PROVIDER 不认识这家"):
            build_fallback(_settings(fallback_provider="anthropic"), role="reviewer")

    def test_unregistered_role_fails_loudly(self):
        with pytest.raises(ValueError, match="未登记的角色"):
            build_fallback(_settings(), role="nobody")

    def test_half_configured_provider_degrades_instead_of_breaking(
        self, caplog: pytest.LogCaptureFixture
    ):
        """兜底是降级功能：配坏了就告警 + 没兜底，不该把整场会话的构建拖死。"""
        settings = _settings(fallback_provider="kimi", kimi_model_name="")

        with caplog.at_level(logging.WARNING):
            assert build_fallback(settings, role="coder") is None

        assert [r for r in caplog.records if "kimi" in r.getMessage()]


# -- 装配 --------------------------------------------------------------------


class TestWiring:
    def test_build_agent_mounts_the_fallback_innermost(self, monkeypatch: Any):
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            graph_module, "create_deep_agent", lambda **kw: captured.update(kw)
        )
        sentinel = FallbackMiddleware(
            role=ORCHESTRATE_FORM, agent=MAIN_AGENT, fallback=_model("deepseek-v4-pro")
        )

        build_agent(
            model=_model("glm-5.3-flash"),
            backend=None,  # type: ignore[arg-type]
            tools=[],
            store=None,  # type: ignore[arg-type]
            checkpointer=None,  # type: ignore[arg-type]
            system_prompt="SYS",
            policy=policy_for(ORCHESTRATE_FORM),
            fallback=sentinel,
        )

        middleware = captured["middleware"]
        # 最内侧：降级重试不再重跑边界与脱敏那几层
        assert middleware[-1] is sentinel
        assert isinstance(middleware[-2], HistoryRepairMiddleware)

    def test_build_agent_without_a_fallback_changes_nothing(self, monkeypatch: Any):
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            graph_module, "create_deep_agent", lambda **kw: captured.update(kw)
        )

        build_agent(
            model=_model("glm-5.3-flash"),
            backend=None,  # type: ignore[arg-type]
            tools=[],
            store=None,  # type: ignore[arg-type]
            checkpointer=None,  # type: ignore[arg-type]
            system_prompt="SYS",
            policy=policy_for(ORCHESTRATE_FORM),
        )

        assert isinstance(captured["middleware"][-1], HistoryRepairMiddleware)

    def test_subagents_take_a_fallback_each(self):
        sentinel = FallbackMiddleware(
            role="planner", agent="planner", fallback=_model("deepseek-v4-pro")
        )
        specs = build_subagents(
            tools_by_role={"planner": [], "coder": []},
            model_for=lambda role: _model("glm-5.3-flash"),
            policies={role: policy_for(role) for role in ("planner", "coder")},
            fallbacks={"planner": sentinel},
        )

        by_name = {spec["name"]: spec for spec in specs}
        assert by_name["planner"]["middleware"][-1] is sentinel
        # 缺键＝该角色没有兜底（合法状态），不是漏挂
        assert not any(
            isinstance(m, FallbackMiddleware) for m in by_name["coder"]["middleware"]
        )


def _manager(fallback_for: Any) -> SessionManager:
    return SessionManager(
        settings=Settings(),
        model_for=lambda role: SimpleNamespace(),
        fallback_for=fallback_for,
        web_search_tool=SimpleNamespace(),
        store=SimpleNamespace(),
        checkpointer=SimpleNamespace(),
        database=SimpleNamespace(),
    )


class TestManagerLabels:
    def test_main_agent_is_labelled_with_the_form_and_main(self):
        """主控那行既要认得出是主控（与其它运行日志同列），又要认得出是哪个形态。"""
        main = _manager(lambda role: _model("deepseek-v4-pro"))._fallback_middleware(
            ORCHESTRATE_FORM, MAIN_AGENT
        )
        sub = _manager(lambda role: _model("deepseek-v4-pro"))._fallback_middleware(
            "coder", "coder"
        )

        assert main is not None and sub is not None
        assert (main._role, main._agent) == (ORCHESTRATE_FORM, MAIN_AGENT)
        assert (sub._role, sub._agent) == ("coder", "coder")

    def test_no_model_means_no_middleware(self):
        assert _manager(lambda role: None)._fallback_middleware("coder", "coder") is None
