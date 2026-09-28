"""Model construction for the agent (按角色选模型)."""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model
from langchain_core.language_models import ModelProfile
from langchain_core.language_models.chat_models import BaseChatModel

from ai_coding_harness.config import ModelConfig, Settings

logger = logging.getLogger(__name__)

# 角色 → 取哪份模型配置。**只用一个名字空间**：主智能体的角色名就是形态名
# （``profiles.AgentProfile.form``），子智能体的角色名就是 ``subagents._ROLES`` 的键
# （planner / coder / reviewer）—— 两者都是「谁在干活」，不另立一套词汇。
#
# 未登记的角色**不回落**、直接报错：漏登记是构建期的错，静默落回某个模型会让某一轮
# 悄悄跑在错的模型上，比当场失败难查得多。守卫测试逐个形态、逐个角色钉住登记齐全。
_ROLE_MODEL_ATTR: dict[str, str] = {
    # 主控：多智能体形态用 glm（原为 qwen，因它在 v3 事件流下丢 tool_call id 而换掉），
    # 四种单智能体形态各用各的
    "orchestrate": "glm_model",
    "qa": "gpt_model",
    "analysis": "gpt_model",
    "plan": "glm_model",
    "review": "kimi_model",
    # 子智能体
    "planner": "glm_model",
    "coder": "deepseek_model",
    "reviewer": "kimi_model",
}


def _provider_attr(role: str) -> str:
    """该角色取 ``Settings`` 上的哪个 ``<供应商>_model`` 属性；未登记的角色直接报错。"""
    attr = _ROLE_MODEL_ATTR.get(role)
    if attr is None:
        raise ValueError(
            f"未登记的角色：{role!r}；请在 llm._ROLE_MODEL_ATTR 里登记它用哪个模型"
        )
    return attr


# 可以当兜底的供应商（``Settings`` 上各有一份 ``<供应商>_model`` 配置）。写死一份是为了
# 让「``.env`` 里把供应商名打错」当场报错，而不是 ``getattr`` 悄悄取到别的东西。
_FALLBACK_PROVIDERS: tuple[str, ...] = ("deepseek", "kimi", "glm", "gpt", "qwen")


def _config_for(settings: Settings, role: str) -> ModelConfig:
    return getattr(settings, _provider_attr(role))


def _model_from(cfg: ModelConfig, *, label: str) -> BaseChatModel:
    """按一份配置建模型实例（OpenAI 兼容）。``label`` 只用于报错时指认是谁。

    不传 ``thinking``：实测 DeepSeek 不带该参数也返回 ``reasoning_content``，而该参数
    并非官方 OpenAI 规范，传了只是多依赖一条未验证的接口契约；glm / qwen 的思考是
    服务端默认（qwen 在云端默认开启），本来就没有开关可传。
    """
    if not cfg.model:
        raise ValueError(f"{label} 的模型名未配置")
    critical_param = cfg.api_key or cfg.base_url
    if not critical_param:
        raise ValueError("api_key and base_url are required")
    model = init_chat_model(
        model=cfg.model,
        model_provider="openai",
        api_key=cfg.api_key or None,
        base_url=cfg.base_url or None,
        temperature=cfg.temperature,
    )
    if cfg.context_window > 0:
        # 压缩中间件按窗口比例（80%）触发，fraction 触发器要求模型带 max_input_tokens。
        model.profile = ModelProfile(max_input_tokens=cfg.context_window)
    return model


def build_model(settings: Settings, *, role: str) -> BaseChatModel:
    """按**角色**构造模型（OpenAI 兼容）。角色路由见 ``_ROLE_MODEL_ATTR``，构造细节见
    ``_model_from``。"""
    return _model_from(_config_for(settings, role), label=f"角色 {role!r}")


def build_fallback(settings: Settings, *, role: str) -> BaseChatModel | None:
    """该角色的**兜底**模型；``None`` 表示这个角色没有兜底（``FallbackMiddleware`` 不挂）。

    只有**全局一条**保底（``FALLBACK_PROVIDER``），不做链式降级。三种情况没有兜底：

    - 没配（留空即关掉这个功能）；
    - 角色本来就用那一家 —— 拿同一个模型再试一次等于没兜（coder 对 deepseek 就是这条）；
    - 那家没配齐（缺模型名或 key/base_url）—— 告警一声就算了：兜底是**降级**功能，
      不该因为它把整场会话的构建拖死。

    供应商名打错字属于构建期的错（``.env`` 写错了），直接报错不静默 —— 静默的后果是
    「以为有兜底，其实一直没挂」，比当场失败难查得多。
    """
    provider = settings.fallback_provider
    if not provider:
        return None
    if provider not in _FALLBACK_PROVIDERS:
        raise ValueError(
            f"FALLBACK_PROVIDER 不认识这家：{provider!r}"
            f"（可选 {' / '.join(_FALLBACK_PROVIDERS)}）"
        )
    attr = f"{provider}_model"
    if attr == _provider_attr(role):
        return None
    cfg: ModelConfig = getattr(settings, attr)
    if not cfg.model or not (cfg.api_key or cfg.base_url):
        logger.warning(
            "兜底供应商 %s 未配置齐全（缺模型名或 key/base_url），本角色无兜底：role=%s",
            provider,
            role,
        )
        return None
    return _model_from(cfg, label=f"兜底模型（{provider}）")



# 分类只看一个词 + 一句理由；给足余量是因为思考关不掉时（供应商强制思考）
# 思考本身要占几百 token，卡太死会把正文挤成空串 —— 实测踩过这个坑。
_CLASSIFIER_MAX_TOKENS = 512


def build_classifier_model(settings: Settings) -> BaseChatModel:
    """意图分类用的模型：独立配置（``INTENT_*``，必填），关思考、温度 0、不挂工具。

    分类是「选一个词」的判定任务，思考纯属浪费：实测关掉后 2.8~5.0s → 0.3s，
    completion token 从约 200 降到 1，判定结果不变。关思考的键见下面 ``extra_body``
    —— 它依供应商而异，换个模型服务得重新确认。主智能体不传该参数，两者只能是两个实例。
    """
    cfg = settings.classifier_model
    if not cfg.model:
        raise ValueError("意图分类模型未配置（设置 INTENT_MODEL）")
    critical_param = cfg.api_key or cfg.base_url
    if not critical_param:
        raise ValueError("api_key and base_url are required")
    return init_chat_model(
        model=cfg.model,
        model_provider="openai",
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        temperature=0,
        max_tokens=_CLASSIFIER_MAX_TOKENS,
        # 关思考得说 vLLM 的方言：顶层未知字段（包括 DeepSeek 那套 ``thinking``）它
        # 一律静默忽略，模型照旧把 ``<think>…</think>`` 内联写在正文里 —— 首行不是意图词，
        # 解析必然失败，还白占 token。换这个键后实测 2.8~5.0s → 0.3s，解析直接命中。
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
