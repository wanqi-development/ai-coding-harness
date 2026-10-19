"""模型构造：角色 → **哪一行** → 模型（路由、温度、上下文窗口）。

2026-10-15 起**没有 ``.env`` 那套出厂默认**（用户口径：干掉它）：每个角色都得在
「模型配置」里有一行模型可用，所以这份测试直接把行造出来 —— 路由错了就构不出模型。
温度、窗口同理：它们现在是**每一行自己的**字段，不再是逐家的 ``.env`` 常量。
不联网：模型对象只构造、不发请求。
"""

from __future__ import annotations

import pytest

from ai_coding_harness.agent import llm as llm_module
from ai_coding_harness.agent.llm import _AGENT_KEY, build_classifier_model, build_model
from ai_coding_harness.agent.profiles import PROFILED_INTENTS, profile_for
from ai_coding_harness.agent.subagents import ROLE_NAMES
from ai_coding_harness.config import FALLBACK_CONTEXT_WINDOW, ModelConfig, Settings

# 角色 → 期望建出来的模型名。故意各行取不同的名字，路由错了立刻看得出来。
_EXPECTED = {
    "orchestrate": "m-coding",
    "qa": "m-qa",
    "analysis": "m-analysis",
    "plan": "m-plan",
    "review": "m-review",
    # 子智能体：planner 复用「方案」、reviewer 复用「审查」、coder 单占「编码」
    "planner": "m-plan",
    "coder": "m-coder",
    "reviewer": "m-review",
}

#: 六个「各智能体」行 → 模型名（与上面的期望对得上）
_ROWS: dict[str, str] = {
    "qa": "m-qa",
    "analysis": "m-analysis",
    "plan": "m-plan",
    "review": "m-review",
    "coding": "m-coding",
    "coder": "m-coder",
}


def _row(model: str, **over: object) -> ModelConfig:
    base: dict[str, object] = {"model": model, "api_key": "k", "base_url": "https://x/v1"}
    base.update(over)
    return ModelConfig(**base)  # type: ignore[arg-type]


def _settings(rows: dict[str, ModelConfig] | None = None, **over: object) -> Settings:
    """默认给出一整套行（每行一个可辨认的模型名）；要测「没配」就显式传 ``rows``。"""
    models = rows if rows is not None else {key: _row(model) for key, model in _ROWS.items()}
    base: dict[str, object] = {"agent_models": models}
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


@pytest.mark.parametrize(("role", "expected"), sorted(_EXPECTED.items()))
def test_role_routes_to_its_row(role: str, expected: str) -> None:
    """角色 → 模型：主控按形态、子智能体按角色，共用一个名字空间。"""
    assert build_model(_settings(), role=role).model_name == expected


def test_the_row_map_is_the_only_route() -> None:
    """「谁用哪个模型」只有 ``_AGENT_KEY`` 这一份映射（``.env`` 那套已删，别再加回来）。"""
    assert _AGENT_KEY == {
        "orchestrate": "coding",
        "qa": "qa",
        "analysis": "analysis",
        "plan": "plan",
        "review": "review",
        "planner": "plan",
        "coder": "coder",
        "reviewer": "review",
    }


@pytest.mark.parametrize("intent", sorted(PROFILED_INTENTS))
def test_every_profile_form_is_registered(intent: str) -> None:
    """每个形态都得有模型：漏登记的话那一轮构建直接报错（未登记不回落）。"""
    assert profile_for(intent).form in _AGENT_KEY


def test_every_subagent_role_is_registered() -> None:
    """子智能体角色同理 —— 它们不再继承主智能体，漏一个就少一家。"""
    assert set(ROLE_NAMES) <= set(_AGENT_KEY)


def test_unknown_role_raises() -> None:
    """未登记的角色不静默落回某个模型：悄悄跑在错的模型上比当场失败难查。"""
    with pytest.raises(ValueError, match="未登记的角色"):
        build_model(_settings(), role="nobody")


def test_an_unconfigured_row_says_which_row_and_where_to_fix_it() -> None:
    """没配的行当场报错，文案要能指认**哪一行**、**去哪儿配**（没有别的模型顶上）。"""
    settings = _settings(rows={"qa": _row("m-qa")})

    with pytest.raises(ValueError, match="「方案」这一行还没配置模型"):
        build_model(settings, role="plan")


def test_a_row_without_a_model_name_counts_as_unconfigured() -> None:
    """半填的行（有地址 / key、没模型名）＝没配 —— 报错指认的就是那一行。"""
    settings = _settings(rows={"plan": _row("", base_url="https://x/v1")})

    with pytest.raises(ValueError, match="「方案」这一行还没配置模型"):
        build_model(settings, role="plan")


def test_unified_covers_every_role() -> None:
    """「统一模型」填了就顶掉所有行（各智能体一行没配也照样能跑）。"""
    settings = _settings(rows={"unified": _row("one-for-all")})

    assert build_model(settings, role="plan").model_name == "one-for-all"
    assert build_model(settings, role="reviewer").model_name == "one-for-all"


def test_temperature_comes_from_the_row() -> None:
    """温度现在是**每一行自己的**字段（kimi-k2.6 只收 1 —— 那是配它的人填 1）。"""
    rows = {
        "coder": _row("m-coder", temperature=0.3),
        "review": _row("m-review", temperature=1),
    }

    assert build_model(_settings(rows=rows), role="coder").temperature == 0.3
    assert build_model(_settings(rows=rows), role="reviewer").temperature == 1


def test_a_kimi_row_gets_temperature_one_whatever_the_row_says() -> None:
    """**kimi 系只收 1**（传 0.3 直接 400 `invalid temperature`，2026-10-15 那次分析轮就是这么挂的）。

    界面上的行没有「温度」这一格，所以这条规则按**模型名**兜（``config.temperature_for``）——
    与 `.env` 时代 kimi 写死 1 是同一个口径。
    """
    rows = {"review": _row("kimi-k2.6"), "qa": _row("m-other")}

    assert build_model(_settings(rows=rows), role="reviewer").temperature == 1
    assert build_model(_settings(rows=rows), role="qa").temperature == 0.3, "别家的行不变"


def test_the_classifier_also_respects_the_kimi_rule() -> None:
    """意图识别那一关强制温度 0，但 kimi 收不了 0 —— 同一层兜住，别让它 400。"""
    settings = _settings(rows={"classifier": _row("kimi-k2.6")})

    assert build_classifier_model(settings).temperature == 1


def test_the_classifier_builds_its_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """意图识别**不走**通用工厂：那一行有自己的开关（思考 / max_tokens / 不挂工具），
    与主智能体按形态路由那套装配是两件事。"""
    seen: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(llm_module, "ChatOpenAI", FakeChatOpenAI)
    settings = _settings(rows={"classifier": _row("kimi-k2.6")})

    assert isinstance(build_classifier_model(settings), FakeChatOpenAI)
    assert seen["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert "reasoning_effort" not in seen  # 关思考：不传这个键


def test_the_classifier_turns_thinking_off_and_uses_zero_temperature() -> None:
    """意图识别是「选一个词」：思考既慢又会把正文挤空；温度 0 才是确定性口径。"""
    model = build_classifier_model(_settings(rows={"classifier": _row("m-cls")}))

    assert model.temperature == 0.0
    assert model.reasoning_effort is None
    assert model.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.parametrize("role", sorted(_EXPECTED))
def test_the_main_model_enables_thinking(role: str) -> None:
    """主智能体（含三个子智能体）开思考；意图识别那一行单独关掉（见上一组用例）。"""
    model = build_model(_settings(), role=role)

    assert model.reasoning_effort == "high"
    assert model.extra_body == {"thinking": {"type": "enabled"}}


def test_context_window_sets_profile() -> None:
    """压缩中间件按窗口比例触发，模型必须带上 max_input_tokens。"""
    rows = {key: _row(model) for key, model in _ROWS.items()}
    rows["coder"] = _row("m-coder", context_window=1234)

    assert build_model(_settings(rows=rows), role="coder").profile == {
        "max_input_tokens": 1234
    }


def test_a_row_without_a_window_uses_the_conservative_value() -> None:
    """没填窗口、也查不到模型窗口 → 保守值（差一点只是早压，撑溢出才是真的错）。"""
    model = build_model(_settings(), role="plan")

    assert model.profile == {"max_input_tokens": FALLBACK_CONTEXT_WINDOW}
