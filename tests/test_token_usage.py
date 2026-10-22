"""Token 消耗的记账与聚合：按执行主角分类、认不出的用量不当 0、中断轮次也入账。

三层各钉一段：

1. **记账**（``AgentRunLog``）—— 与日志共用同一个 ``actor()`` 归属判定，所以顺手就测到
   「帐与日志不会各说各话」；关键是**认不出用量时必须记进 ``missing`` 而不是记成 0**
   （0 会让「没读到」看起来像「没花钱」）。
2. **落库形状**（``_turn_usage``）—— 意图判定那一笔（流外调用、另一个模型）单独归一类，
   中断的轮次带上 ``interrupted`` 标记但**照样入账**（钱真花了）。
3. **聚合接口** —— 分类求和、算得多的排前面、缺数据的轮次如实上报（历史记录没有
   ``extra.usage``），**不把缺的轮次当 0 消耗**。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ai_coding_harness.api.routes import sessions as sessions_route
from ai_coding_harness.config import ModelConfig, Settings
from ai_coding_harness.observability.logging import MAIN_AGENT_NAME
from ai_coding_harness.observability.run_log import (
    INTENT_ACTOR,
    INTERNAL_ACTOR,
    MAIN_ACTOR,
    AgentRunLog,
    usage_numbers,
)
from tests.v3_events import MAIN_NS, RunLogFeed, message_finish, sub_ns, tool_start

#: 各「智能体行」给的窗口（2026-10-15 起窗口写在行里，不再是 ``.env`` 的逐家常量）
WINDOW_GLM = 128_000
WINDOW_KIMI = 256_000
WINDOW_GPT = 128_000

SESSION = "78780d7a-91f4-498c-bdcf-5f55a8190f11"


def _finish(
    *,
    inp: Any = 120,
    outp: Any = 8,
    ns: tuple[str, ...] = MAIN_NS,
    agent: str = "None",
    internal: bool = False,
) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    if inp is not None:
        usage["input_tokens"] = inp
    if outp is not None:
        usage["output_tokens"] = outp
    return message_finish(ns=ns, agent=agent, usage=usage, internal=internal)


def _log() -> AgentRunLog:
    return AgentRunLog(arg_limit=100, result_limit=100)


def _feed(log: AgentRunLog) -> RunLogFeed:
    return RunLogFeed(log)


# -- 一、记账 --------------------------------------------------------------


def test_numbers_accept_both_field_naming() -> None:
    """两种字段名都认：不同 SDK / 网关回的形状不保证一致。"""
    assert usage_numbers({"input_tokens": 3, "output_tokens": 4}) == (3, 4)
    assert usage_numbers({"prompt_tokens": 5, "completion_tokens": 6}) == (5, 6)
    # 认不出来的要说「不知道」，不是 (0, 0) —— 后者会变成一笔假账
    assert usage_numbers({}) is None
    assert usage_numbers(None) is None
    assert usage_numbers("输入 1") is None


def test_the_snapshot_starts_empty() -> None:
    """一轮都没跑时账是空的：界面据此显示空态，而不是「消耗 0」。"""
    snapshot = _log().usage_snapshot()

    assert snapshot == {
        "totals": {"input": 0, "output": 0, "calls": 0},
        "by_actor": {},
        "missing": 0,
        "interrupted": False,
    }


def test_usage_is_grouped_by_the_acting_agent(caplog: pytest.LogCaptureFixture) -> None:
    """主控与两个子智能体各记一行，归属沿用日志那一套（``actor()``）。"""
    log = _log()
    feed = _feed(log)
    coder_ns = sub_ns("task-1")

    feed.llm(_finish(inp=1000, outp=100))  # 主控
    feed.start(tool_start("task", call_id="task-1"))  # 派 coder
    feed.llm(_finish(inp=2000, outp=200, ns=coder_ns, agent="coder"))
    feed.llm(_finish(inp=3000, outp=300, ns=coder_ns, agent="coder"))
    feed.llm(_finish(inp=500, outp=50))  # 主控再调一次

    snapshot = log.usage_snapshot()

    assert snapshot["by_actor"] == {
        MAIN_ACTOR: {"input": 1500, "output": 150, "calls": 2},
        "coder": {"input": 5000, "output": 500, "calls": 2},
    }
    assert snapshot["totals"] == {"input": 6500, "output": 650, "calls": 4}
    assert snapshot["missing"] == 0


def test_summarisation_calls_are_not_folded_into_the_main_agent() -> None:
    """中间件自己的调用（上下文摘要）单独一类。

    实测单次就有 7 万~8 万输入 tokens —— 并进「主控」名下等于把最大的一笔开销藏起来。
    """
    log = _log()
    feed = _feed(log)

    feed.llm(_finish(inp=1000, outp=100))
    feed.llm(_finish(inp=72301, outp=3339, internal=True))

    snapshot = log.usage_snapshot()

    assert snapshot["by_actor"][MAIN_ACTOR] == {"input": 1000, "output": 100, "calls": 1}
    assert snapshot["by_actor"][INTERNAL_ACTOR] == {
        "input": 72301,
        "output": 3339,
        "calls": 1,
    }


def test_an_unreadable_usage_is_counted_as_missing_not_as_zero() -> None:
    """平台没回用量：记 ``missing``，**绝不当成 0**（缺口要能在界面上看见）。"""
    log = _log()
    feed = _feed(log)

    feed.llm(_finish(inp=100, outp=10))
    feed.llm(message_finish())  # 没有 usage 载荷
    feed.llm(_finish(inp=None, outp=None))  # 有载荷但没有可认的字段

    snapshot = log.usage_snapshot()

    assert snapshot["missing"] == 2
    assert snapshot["totals"] == {"input": 100, "output": 10, "calls": 1}, (
        "缺的那两次不进总数（连调用次数都不算 —— 那两次的账根本不知道）"
    )


def test_an_interrupted_round_is_still_recorded() -> None:
    """中断的轮次照样入账（钱真花了），只是标出来。"""
    log = _log()
    _feed(log).llm(_finish(inp=700, outp=70))

    assert log.usage_snapshot(interrupted=True)["interrupted"] is True
    assert log.usage_snapshot()["interrupted"] is False, "默认不打中断标记"


# -- 二、落库形状 ----------------------------------------------------------


def _session() -> Any:
    return SimpleNamespace(public_id=SESSION, db_id=1, id=1)


def _service(
    intent_usage: dict[str, int] | None = None,
    *,
    contexts: dict[str, int] | None = None,
) -> Any:
    from ai_coding_harness.server.service import HarnessService

    # settings 用真配置（**模型配置那几行**）：上下文那一格要按本轮主控形态取模型窗口，
    # 桩件给不出窗口值；2026-10-15 起没有 .env 出厂默认，窗口只能写在行里。
    service = HarnessService(
        settings=Settings(
            _env_file=None,
            agent_models={
                "qa": ModelConfig(
                    model="gpt", api_key="k", base_url="https://gpt/v1",
                    context_window=WINDOW_GPT,
                ),
                "analysis": ModelConfig(
                    model="gpt", api_key="k", base_url="https://gpt/v1",
                    context_window=WINDOW_GPT,
                ),
                "plan": ModelConfig(
                    model="glm", api_key="k", base_url="https://glm/v1",
                    context_window=WINDOW_GLM,
                ),
                "coding": ModelConfig(
                    model="glm", api_key="k", base_url="https://glm/v1",
                    context_window=WINDOW_GLM,
                ),
                "review": ModelConfig(
                    model="kimi", api_key="k", base_url="https://kimi/v1",
                    context_window=WINDOW_KIMI,
                ),
                "coder": ModelConfig(
                    model="ds", api_key="k", base_url="https://ds/v1",
                    context_window=1_048_576,
                ),
            },
        ),
        manager=SimpleNamespace(),
        database=SimpleNamespace(),
    )
    service._intent_usage = intent_usage or {}
    service._context_state = contexts or {}
    return service


def test_the_intent_classification_is_one_more_actor() -> None:
    """意图判定那笔单独归一类：它跑在事件流之外、用的还是另一个模型。"""
    log = _log()
    _feed(log).llm(_finish(inp=1000, outp=100))
    service = _service({"input": 30, "output": 5, "calls": 1})

    usage = service._turn_usage(_session(), "coding", log)

    assert usage["by_actor"][INTENT_ACTOR] == {"input": 30, "output": 5, "calls": 1}
    # 判定那一笔也算进总数：它问的是「这一轮一共花了多少」，界面上的合计不能小于各行之和
    assert usage["totals"] == {"input": 1030, "output": 105, "calls": 2}


def test_a_prompt_hit_costs_nothing_and_adds_no_actor() -> None:
    """口令层命中时判定没调模型：账上不该凭空多出「意图分类」这一项。"""
    service = _service({})

    usage = service._turn_usage(_session(), "coding", _log())

    assert INTENT_ACTOR not in usage["by_actor"]


class _FakeJevClient:
    """``AsyncTypeSafeClient`` 的替身：只实现我们用到的那一个方法。"""

    def __init__(self, *, usage: Any) -> None:
        self._usage = usage
        self.calls = 0

    async def system_one(self, **_kwargs: Any) -> Any:
        self.calls += 1
        answer = SimpleNamespace(choice="coding", confidence=0.9, probabilities={})
        return SimpleNamespace(answers={"intent": answer}, usage=self._usage, model="jev")


async def test_the_jev_gate_pays_into_the_callers_accumulator() -> None:
    """JEV 那一关的账进**调用方给的**累加器，判定器自己不留账。

    2026-10-12 修的一处：原来记在 ``JevDecider.usage`` 上 —— 那份 dict 没人读（面板里
    「意图分类」这一项因此少一笔），而且从不 reset，将来谁接上就会把历史几轮一起算进当轮。
    """
    pytest.importorskip("typesafe_sdk")
    from ai_coding_harness.server.intent import JevDecider

    decider = JevDecider(api_key="dummy", base_url="https://example.invalid", timeout=1.0)
    decider._client = _FakeJevClient(  # 只换掉 HTTP 那一层
        usage=SimpleNamespace(input_tokens=1234, output_tokens=5)
    )
    acc: dict[str, int] = {}

    decision = await decider.decide("把这个需求做了", usage=acc)

    assert decision is not None and decision[0] == "coding"
    assert acc == {"input": 1234, "output": 5, "calls": 1}
    assert not hasattr(decider, "usage"), "账不该留在判定器自己的实例上"


def test_an_interrupted_round_keeps_the_usage_on_the_record() -> None:
    """中断轮次走 ``_record_interrupted``：账要跟着记录一起落库。"""
    log = _log()
    _feed(log).llm(_finish(inp=400, outp=40))
    service = _service()

    fields: dict[str, Any] = {"intent": "coding", "partial": True}
    fields["usage"] = service._turn_usage(_session(), "coding", log, interrupted=True)

    assert fields["usage"]["totals"] == {"input": 400, "output": 40, "calls": 1}
    assert fields["usage"]["interrupted"] is True


async def test_an_interrupted_round_keeps_changes_on_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中止前已经建成的 PR 仍要留改动清单（2026-10-20 reviewer 触发守卫那轮）。"""
    service = _service()
    captured: dict[str, Any] = {}

    async def fake_record(
        session: Any, *, type: str, summary: str, content: str, extra: dict[str, Any]
    ) -> None:
        captured.update(extra)

    monkeypatch.setattr(service, "_record", fake_record)
    translator = SimpleNamespace(parts=SimpleNamespace(has_todos=False, data=[]))
    changes = {
        "base": "master",
        "head": "feature/x",
        "files": [{"path": "a.java", "added": 1, "deleted": 0, "status": "A"}],
    }

    await service._record_interrupted(
        _session(),
        "已产出",
        translator,
        "coding",
        (),
        closing="〔已中止：同一工具重复调用，本轮未取得进展〕",
        extra={"aborted": "repeated_tool"},
        changes=changes,
    )

    assert captured["changes"] == changes
    assert captured["aborted"] == "repeated_tool"
    assert captured["partial"] is True


# -- 二之二、上下文压力 ----------------------------------------------------


def test_the_peak_input_only_counts_the_main_agent() -> None:
    """压力只看**主控**那一路。

    子智能体各有自己的窗口（coder 是 1M），把它们的输入拿去比主控的窗口会得出
    「永远安全」的错结论 —— 这里就把那个错结论钉死在测试里。
    """
    log = _log()
    feed = _feed(log)
    coder_ns = sub_ns("t1")

    feed.llm(_finish(inp=92000, outp=100))
    feed.start(tool_start("task", call_id="t1"))
    feed.llm(_finish(inp=900000, outp=100, ns=coder_ns, agent="coder"))

    assert log.peak_input == 92000, "子智能体的 90 万不算进压力"


def test_the_main_agent_is_recognised_by_its_real_name() -> None:
    """主控在事件流里的署名是 ``MAIN_AGENT_NAME``（``graph.py`` 的 ``name=``），不是占位名。

    2026-10-12 实测的坑：只认 ``MAIN_AGENT`` 时，主控那一笔被归到一个英文名下（面板上就
    显示成那个名字），而且 ``peak_input`` 恒为 0 —— 上下文压力整块静默失效（真机接口里
    ``max_input: 0 / ratio: 0.0``，而那一轮主控明明吃进 26,432 输入）。这里两种署名都钉住。
    """
    log = _log()
    _feed(log).llm(_finish(inp=26432, outp=20, agent=MAIN_AGENT_NAME))

    assert list(log.usage_snapshot()["by_actor"]) == [MAIN_ACTOR], "真名也要归到 main"
    assert log.peak_input == 26432
    assert log.last_input == 26432

    placeholder = _log()
    _feed(placeholder).llm(_finish(inp=100, outp=1, agent="None"))
    assert list(placeholder.usage_snapshot()["by_actor"]) == [MAIN_ACTOR], "占位名同样归 main"


def test_the_compression_count_comes_from_the_internal_call() -> None:
    """压缩的判据是**中间件内部调用**（摘要只在压缩时发起），不必去上游埋钩子。"""
    log = _log()
    feed = _feed(log)

    feed.llm(_finish(inp=1000, outp=100))
    assert log.compressions == 0
    feed.llm(_finish(inp=72301, outp=3339, internal=True))
    assert log.compressions == 1


def test_the_window_follows_the_rounds_main_model() -> None:
    """窗口按**本轮主控形态**取：coding / plan 看「编码主控」「方案」两行，review 看「审查」。"""
    from ai_coding_harness.server.service import _main_window

    settings = _service()._settings

    assert _main_window(settings, "coding") == WINDOW_GLM
    assert _main_window(settings, "plan") == WINDOW_GLM
    assert _main_window(settings, "review") == WINDOW_KIMI
    assert _main_window(settings, "qa") == WINDOW_GPT


# -- 二·五、末行标模型 + 跑动中推账（2026-10-14 用户口径）----------------------


def test_the_answer_carries_the_main_model_name() -> None:
    """末行要标「这条回答是哪个模型说的」：解析口径与窗口同一套（coding/plan → GLM）。"""
    service = _service()

    assert service._turn_usage(_session(), "coding", _log())["model"] == "glm"
    assert service._turn_usage(_session(), "plan", _log())["model"] == "glm"
    assert service._turn_usage(_session(), "review", _log())["model"] == "kimi"
    assert service._turn_usage(_session(), "qa", _log())["model"] == "gpt"


def test_progress_frames_do_not_write_into_the_cross_round_state() -> None:
    """跑动中的中间帧**只读**：压缩次数是 ``+=``，同一轮多算几次就重复累加了。"""
    log = _log()
    feed = _feed(log)
    feed.llm(_finish(inp=1000, outp=100))
    feed.llm(_finish(inp=72301, outp=3339, internal=True))
    service = _service()
    session = _session()

    first = service._turn_usage(session, "coding", log, commit_context=False)
    second = service._turn_usage(session, "coding", log, commit_context=False)

    assert first["context"]["compressed"] == second["context"]["compressed"] == 1
    assert service._context_state == {}, "中间帧不该往跨轮累计里写东西"

    final = service._turn_usage(session, "coding", log)  # 收尾那次才落账
    assert final["context"]["compressed"] == 1
    assert service._context_state[str(session.public_id)][1] == 1, "落账也只累加一次"


def test_progress_frames_are_throttled() -> None:
    """同一秒里不为每一次调用重建一帧：0.5 秒内的第二次直接跳过（返回 None）。"""
    service = _service()
    log = _log()
    _feed(log).llm(_finish(inp=10, outp=2))

    first = service._live_usage(_session(), "coding", log)
    second = service._live_usage(_session(), "coding", log)

    assert first is not None and first["model"] == "glm"
    assert second is None


async def test_the_round_streams_the_account_while_it_runs(monkeypatch) -> None:
    """token 消耗走 SSE **动态**返回：跑动中就推，不是只在收尾推一次。

    假件复用 ``test_plan_events`` 那套（不跑真 LLM、不连库）：两次工具收尾各推一帧，
    加上收尾那一帧，至少三条 —— 这里把节流关掉，好让每一处进度点都留下痕迹。
    """
    from ai_coding_harness.server import service as service_mod
    from tests.test_plan_events import _run, _tool_call

    monkeypatch.setattr(service_mod.turn_usage, "_USAGE_FRAME_MIN_GAP", 0.0)

    out = await _run([*_tool_call("execute"), *_tool_call("read_file", call_id="t2")])

    usages = [e["usage"] for e in out if e["type"] == "usage"]
    assert len(usages) >= 3, "两次工具收尾 + 收尾那一帧"
    assert out[-1]["type"] == "usage", "收尾那一帧仍是最后一帧（前端按顺序处理帧）"
    assert len({u["model"] for u in usages}) == 1, "同一轮里主控模型不会变"
    assert usages[-1]["model"], "每帧都带上模型名"


def test_the_context_state_keeps_the_latest_call_across_rounds() -> None:
    """「最近值」与压缩次数**跨轮保留**（2026-10-20 用户口径）。

    环图说的是「现在这条历史有多长」：本轮没有主控调用（这里只有一次摘要调用）时沿用
    上一轮记下的值；一旦本轮有主控调用，就换成**那一次**的输入 —— 不是历史峰值。
    """
    service = _service()
    session = _session()

    first = _log()
    _feed(first).llm(_finish(inp=50000, outp=100))
    service._turn_usage(session, "coding", first)

    second = _log()  # 新一轮：本轮的账从零开始
    _feed(second).llm(_finish(inp=30000, outp=100, internal=True))
    usage = service._turn_usage(session, "coding", second)

    ctx = usage["context"]
    assert ctx["input"] == 50000, "本轮没有主控调用：沿用上一轮的值"
    assert ctx["compressed"] == 1, "压缩次数跨轮累加"
    assert ctx["compressed_now"] is True
    assert ctx["window"] == WINDOW_GLM
    assert ctx["ratio"] == round(50000 / WINDOW_GLM, 3)

    third = _log()
    _feed(third).llm(_finish(inp=30000, outp=100))  # 本轮主控真的调了，且比上一轮小
    assert service._turn_usage(session, "coding", third)["context"]["input"] == 30000, (
        "取最近一次调用，不是历史峰值"
    )


def test_the_context_state_is_per_session() -> None:
    """一个会话的压力不能串到另一个会话上。"""
    service = _service()
    log = _log()
    _feed(log).llm(_finish(inp=50000, outp=100))
    other_session = SimpleNamespace(public_id="other-session", db_id=2, id=2)

    service._turn_usage(_session(), "coding", log)
    other = service._turn_usage(other_session, "coding", _log())

    assert other["context"]["input"] == 0


def test_a_compressed_round_drops_the_pressure_back_down() -> None:
    """压过之后水位要**掉下来**：用压缩后的末值，不用压缩前那次峰值。

    否则一次压缩就让提示永久停在「接近压缩线」—— 而历史其实已经掉到 10%，下一条消息
    根本不会触发压缩。这里把「涨上去」与「压完落回来」两段都钉住。

    输入按**配置里的窗口**算（86% / 14%），不写死数字 —— glm 的窗口从 128k 改成 1M
    （2026-10-13 用户确认）时，这条测试不该跟着红。
    """
    service = _service()
    session = _session()
    window = WINDOW_GLM

    grown = _log()
    _feed(grown).llm(_finish(inp=int(window * 0.86), outp=100))  # 窗口的 86%
    assert service._turn_usage(session, "coding", grown)["context"]["ratio"] > 0.8

    compressed = _log()
    feed = _feed(compressed)
    feed.llm(_finish(inp=72301, outp=3339, internal=True))  # 摘要调用 ＝ 这一轮压过
    feed.llm(_finish(inp=int(window * 0.14), outp=100))  # 压缩之后主控的真实占用
    ctx = service._turn_usage(session, "coding", compressed)["context"]

    assert ctx["input"] == int(window * 0.14), "水位跟着压缩掉下来"
    assert ctx["ratio"] < 0.6, "掉到 60% 以下，那条提示就不该再画"
    assert ctx["compressed"] == 1
    assert ctx["compressed_now"] is True


def test_an_unknown_window_does_not_invent_a_ratio() -> None:
    """窗口未知（没配模型 / 没登记角色）时比例是 0，而不是瞎算一个数。"""
    from ai_coding_harness.server.service import _ratio

    assert _ratio(50000, 0) == 0.0
    assert _ratio(0, 128000) == 0.0
    assert _ratio(64000, 128000) == 0.5


@asynccontextmanager
async def _db_session(*_a: Any, **_kw: Any):
    yield None


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.state.database = SimpleNamespace(session=_db_session)
    # 真配置：接口里要用它算 context 的窗口
    app.state.settings = Settings(_env_file=None)
    return app


async def _get(
    path: str, rows: list[Any], monkeypatch: pytest.MonkeyPatch
) -> httpx.Response:
    """走真实路由、真实聚合，只把「库里有什么」换掉。"""
    from ai_coding_harness.db import dao

    async def fake_session(db: Any, public_id: Any) -> Any:
        return _session()

    async def fake_records(db: Any, *, session_id: int, limit: int = 200) -> list[Any]:
        return rows

    monkeypatch.setattr(dao, "get_session_by_public_id", fake_session)
    monkeypatch.setattr(dao, "list_records_with_detail", fake_records)
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def _record(
    *,
    kind: str = "assistant",
    usage: dict[str, Any] | None = None,
    extra: Any = None,
) -> Any:
    """一条 ``(记录, 详情)``。

    ``extra`` 显式给 ``None`` 时用 ``{}``（模拟真库里「详情行在、但没有 usage」），
    想造真正的空详情就传 ``extra=SimpleNamespace(extra=None)``。
    """
    detail = SimpleNamespace(
        extra=extra if extra is not None else ({"usage": usage} if usage else {})
    )
    return SimpleNamespace(type=kind), detail


def _usage(
    *, inp: int, outp: int, by_actor: dict[str, Any], interrupted: bool = False, missing: int = 0
) -> dict[str, Any]:
    return {
        "totals": {"input": inp, "output": outp},
        "by_actor": by_actor,
        "missing": missing,
        "interrupted": interrupted,
    }


async def test_the_endpoint_sums_by_actor_and_puts_the_biggest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _record(
            usage=_usage(
                inp=1000,
                outp=100,
                by_actor={
                    MAIN_ACTOR: {"input": 1000, "output": 100, "calls": 2},
                    "coder": {"input": 5000, "output": 500, "calls": 4},
                },
            )
        ),
        _record(
            usage=_usage(
                inp=2000,
                outp=200,
                by_actor={"coder": {"input": 2000, "output": 200, "calls": 3}},
            )
        ),
        _record(kind="user"),  # 用户消息不进账
    ]

    resp = await _get(f"/sessions/{SESSION}/usage", rows, monkeypatch)

    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"] == {
        "key": "total",
        "input": 3000,
        "output": 300,
        "calls": 9,  # 2 + 4 + 3：合计的调用数也要真的加起来（曾漏填，恒为 0）
        "total": 3300,
    }
    # 花得多的排前面：面板第一眼要看到「钱花在哪」
    assert [actor["key"] for actor in body["actors"]] == ["coder", MAIN_ACTOR]
    assert body["actors"][0] == {
        "key": "coder",
        "input": 7000,
        "output": 700,
        "calls": 7,
        "total": 7700,
    }
    assert body["rounds"] == 2
    assert body["rounds_with_usage"] == 2


async def test_a_legacy_record_under_the_main_agents_real_name_merges_into_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本功能上线的那几轮把主控记在了真名下：读账时归一次，界面上不该冒出英文名。

    （写账那一侧已经按真名归好了，见 ``test_the_main_agent_is_recognised_by_its_real_name``；
    这条管的是**已经躺在库里的**那几轮。）
    """
    rows = [
        _record(
            usage=_usage(
                inp=26432,
                outp=20,
                by_actor={
                    MAIN_AGENT_NAME: {"input": 26432, "output": 20, "calls": 1},
                    MAIN_ACTOR: {"input": 100, "output": 10, "calls": 1},
                },
            )
        )
    ]

    body = (await _get(f"/sessions/{SESSION}/usage", rows, monkeypatch)).json()

    assert [actor["key"] for actor in body["actors"]] == [MAIN_ACTOR]
    assert body["actors"][0]["input"] == 26532
    assert body["totals"]["calls"] == 2


async def test_rounds_without_usage_are_reported_not_counted_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本功能上线前的记录没有 ``extra.usage``：如实说「有 N 轮没有数据」。"""
    rows = [
        _record(),  # 上线前跑的：没有 usage
        _record(extra=SimpleNamespace(extra=None)),  # 连详情行都没有
        _record(
            usage=_usage(
                inp=10,
                outp=1,
                by_actor={MAIN_ACTOR: {"input": 10, "output": 1, "calls": 1}},
                interrupted=True,
                missing=2,
            )
        ),
    ]

    resp = await _get(f"/sessions/{SESSION}/usage", rows, monkeypatch)

    body = resp.json()
    assert body["rounds"] == 3
    assert body["rounds_with_usage"] == 1
    assert body["rounds_interrupted"] == 1
    assert body["missing"] == 2
    assert body["totals"]["total"] == 11


async def test_the_endpoint_no_longer_carries_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上下文压力挪到输入框右边的环图（2026-10-20 用户口径）。

    Token 面板只留 token 账；会话级那份「最小窗口 + 最大峰值」的聚合会把两个来自
    不同轮次的数拼成一个假比例（实测 298277 / 128000 = 233%），所以整格删掉。
    """
    rows = [
        _record(
            usage={
                "totals": {"input": 100, "output": 10},
                "by_actor": {},
                "context": {"input": 90000, "window": 256000, "ratio": 0.352},
            }
        )
    ]

    resp = await _get(f"/sessions/{SESSION}/usage", rows, monkeypatch)

    assert "context" not in resp.json()


async def test_the_endpoint_splits_the_main_actor_by_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主控在事件流里只有一个名字，按形态拆要读每轮的 ``extra.intent``（2026-10-20）。

    面板上 analysis / qa / plan / review 各占一行，coding 与 rework 并进同一组
    （rework 不单列）；子智能体不进 ``by_form`` —— 它们本来就是独立的执行主角。
    """
    rows = [
        _record(
            extra={
                "intent": "analysis",
                "usage": _usage(
                    inp=100,
                    outp=10,
                    by_actor={"main": {"input": 100, "output": 10, "calls": 1}},
                ),
            }
        ),
        _record(
            extra={
                "intent": "coding",
                "usage": _usage(
                    inp=300,
                    outp=30,
                    by_actor={
                        "main": {"input": 100, "output": 10, "calls": 1},
                        "coder": {"input": 200, "output": 20, "calls": 2},
                    },
                ),
            }
        ),
        _record(
            extra={
                # 老记录里主控记在真名下：按形态拆之前也要先归一到 main
                "intent": "rework",
                "usage": _usage(
                    inp=50,
                    outp=5,
                    by_actor={"ai-coding-harness": {"input": 50, "output": 5, "calls": 1}},
                ),
            }
        ),
    ]

    body = (await _get(f"/sessions/{SESSION}/usage", rows, monkeypatch)).json()

    assert set(body["by_form"]) == {"analysis", "coding", "rework"}
    assert body["by_form"]["analysis"]["total"] == 110
    assert body["by_form"]["coding"]["total"] == 110
    assert body["by_form"]["rework"]["total"] == 55
    # 子智能体不进形态那一格：coding 的 110 只含主控那一次
    assert body["by_form"]["coding"]["calls"] == 1


async def test_a_session_without_any_usage_returns_an_empty_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = await _get(
        f"/sessions/{SESSION}/usage", [_record(kind="user")], monkeypatch
    )

    body = resp.json()
    assert body["rounds"] == 0
    assert body["actors"] == []
    assert body["totals"]["total"] == 0


async def test_dirty_numbers_do_not_break_the_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """库里那份是 JSONB，不保证干净：一条脏记录不该把整个面板打挂。"""
    rows = [
        _record(
            usage={
                "totals": {"input": "不是数字", "output": None},
                "by_actor": {"coder": "形状不对", MAIN_ACTOR: {"input": 5}},
                "missing": "两个",
            }
        )
    ]

    resp = await _get(f"/sessions/{SESSION}/usage", rows, monkeypatch)

    body = resp.json()
    assert resp.status_code == 200
    assert body["missing"] == 0
    assert body["actors"] == [
        {"key": MAIN_ACTOR, "input": 5, "output": 0, "calls": 0, "total": 5}
    ]


async def test_an_invalid_session_id_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = await _get("/sessions/not-a-uuid/usage", [], monkeypatch)

    assert resp.status_code == 400


class TestTurnCostLog:
    """一轮结束必须有一行「输入 / 输出 / 调用 / 峰值」，超阈值再单独告警。"""

    def _run(self, caplog, *, warn: int = 1000) -> None:
        from ai_coding_harness.server.service import HarnessService

        service = SimpleNamespace(
            _settings=SimpleNamespace(turn_cost_warn_tokens=warn)
        )
        usage = {
            "totals": {"input": 12, "output": 3, "calls": 2},
            "by_actor": {
                "coder": {"input": 9},
                "main": {"input": 3},
            },
            "context": {"input": 8},
        }
        with caplog.at_level(logging.INFO):
            HarnessService._log_turn_cost(
                service,
                SimpleNamespace(public_id="p-1"),
                "coding",
                usage,
                llm_calls=2,
                tool_calls=1,
                chars=5,
            )

    def test_a_one_line_summary_is_always_logged(self, caplog):
        self._run(caplog)

        assert "本轮成本" in caplog.text
        assert "输入 12" in caplog.text
        assert "主控最近上下文 8" in caplog.text
        assert "本轮 token 偏高" not in caplog.text

    def test_over_the_threshold_it_warns_and_names_the_top_actor(self, caplog):
        self._run(caplog, warn=10)

        assert "本轮 token 偏高" in caplog.text
        assert "coder 占 9" in caplog.text


