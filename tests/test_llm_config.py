"""模型构造：角色路由、温度、上下文窗口。

温度是调优时改过的（1.1 → 0.3，高温下方案发散），钉住以免被悄悄改回去。
不联网：模型对象只构造、不发请求。

**角色路由表是这张测试的重点**：它把「谁用哪家模型」写成一处数据，漏登记或多登记
都从行为上看不出来（那一轮只是静默跑在另一个模型上），所以这里既钉死映射，也钉死
「形态与子智能体角色全都登记了」。
"""

from __future__ import annotations

import pytest

from ai_coding_harness.agent.llm import _ROLE_MODEL_ATTR, build_model
from ai_coding_harness.agent.profiles import PROFILED_INTENTS, profile_for
from ai_coding_harness.agent.subagents import ROLE_NAMES
from ai_coding_harness.config import Settings

# 角色 → 期望建出来的模型名。故意各家取不同的名字，路由错了立刻看得出来。
_EXPECTED = {
    # 主控：四种单智能体形态各一家，多智能体形态也归 glm
    "orchestrate": "glm-5.3",
    "qa": "gpt-5.5",
    "analysis": "gpt-5.5",
    "plan": "glm-5.3",
    "review": "kimi-k2.6",
    # 子智能体：planner 与主控的 plan 同源，reviewer 与 review 同源，coder 仍是 deepseek
    "planner": "glm-5.3",
    "coder": "deepseek-v4-pro",
    "reviewer": "kimi-k2.6",
}


def _settings(**over) -> Settings:
    base = {
        "deepseek_api_key": "fake",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model_name": "deepseek-v4-pro",
        "kimi_api_key": "fake",
        "kimi_base_url": "https://api.moonshot.cn/v1",
        "kimi_model_name": "kimi-k2.6",
        "glm_api_key": "fake",
        "glm_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "glm_model_name": "glm-5.3",
        "gpt_api_key": "fake",
        "gpt_base_url": "https://api.ztoken.pro/v1",
        "gpt_model_name": "gpt-5.5",
        "qwen_api_key": "fake",
        "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen_model_name": "qwen3.8-flash",
    }
    base.update(over)
    return Settings(**base)


@pytest.mark.parametrize(("role", "expected"), sorted(_EXPECTED.items()))
def test_role_routes_to_its_own_provider(role, expected):
    """角色 → 模型：主控按形态、子智能体按角色，共用一个名字空间。"""
    assert build_model(_settings(), role=role).model_name == expected


def test_role_table_covers_every_expected_role():
    """映射表与期望表必须一一对应 —— 表里多一个没人用的角色也算漂移。"""
    assert set(_ROLE_MODEL_ATTR) == set(_EXPECTED)


@pytest.mark.parametrize("intent", sorted(PROFILED_INTENTS))
def test_every_profile_form_is_registered(intent):
    """每个形态都得有模型：漏登记的话那一轮构建直接报错（未登记不回落）。"""
    assert profile_for(intent).form in _ROLE_MODEL_ATTR


def test_every_subagent_role_is_registered():
    """子智能体角色同理 —— 它们不再继承主智能体，漏一个就少一家。"""
    assert set(ROLE_NAMES) <= set(_ROLE_MODEL_ATTR)


def test_unknown_role_raises():
    """未登记的角色不静默落回某个模型：悄悄跑在错的模型上比当场失败难查。"""
    with pytest.raises(ValueError, match="未登记的角色"):
        build_model(_settings(), role="nobody")


def test_temperature_is_low():
    """高温度下方案发散明显，降到 0.3。"""
    assert build_model(_settings(), role="coder").temperature == 0.3


@pytest.mark.parametrize("role", ["review", "reviewer"])
def test_kimi_only_accepts_temperature_one(role):
    """kimi-k2.6 只收 temperature=1（传 0.3 直接 400），所以温度是逐家配置。"""
    assert build_model(_settings(), role=role).temperature == 1


@pytest.mark.parametrize("role", sorted(_EXPECTED))
def test_no_thinking_param_is_sent(role):
    """不传 thinking：实测 deepseek-flash 不带它也返回 reasoning_content，该参数又不在
    官方 OpenAI 规范内；glm / qwen 的思考是服务端默认，本来也没有开关可传。"""
    assert build_model(_settings(), role=role).extra_body is None


def test_context_window_sets_profile():
    """压缩中间件按窗口比例触发，模型必须带上 max_input_tokens。"""
    model = build_model(_settings(deepseek_context_window=1234), role="coder")

    assert model.profile == {"max_input_tokens": 1234}


@pytest.mark.parametrize(
    ("role", "field", "window"),
    [
        ("orchestrate", "glm_context_window", 4096),
        ("plan", "glm_context_window", 8192),
        ("qa", "gpt_context_window", 16384),
        ("review", "kimi_context_window", 32768),
    ],
)
def test_each_provider_carries_its_own_window(role, field, window):
    """窗口是**逐家**的配置：取错一家就等于压缩阈值跟着错（太小早压、太大溢出）。"""
    model = build_model(_settings(**{field: window}), role=role)

    assert model.profile == {"max_input_tokens": window}


def test_window_zero_means_no_compression_trigger():
    """0 表示未知 —— 不设 profile，压缩就不触发（这正是要给三家填上值的理由）。"""
    model = build_model(_settings(glm_context_window=0), role="plan")

    assert model.profile is None


def test_missing_model_name_raises():
    """模型名为空要明确报错，并且要说清是哪个角色。"""
    with pytest.raises(ValueError, match="'plan' 的模型名未配置"):
        build_model(_settings(glm_model_name=""), role="plan")
