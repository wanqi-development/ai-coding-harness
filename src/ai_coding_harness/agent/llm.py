"""Model construction for the agent (按角色选模型)."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from langchain_core.language_models import ModelProfile
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ai_coding_harness.agent.chat_model import ReasoningChatOpenAI
from ai_coding_harness.config import (
    FALLBACK_CONTEXT_WINDOW,
    MODEL_AGENT_LABEL,
    ModelConfig,
    Settings,
    provider_key_for,
    temperature_for,
)

logger = logging.getLogger(__name__)

#: 角色 → **界面上的哪一行配置**。这是「谁用哪个模型」的**唯一**映射 ——
#: 2026-10-15 去掉 ``.env`` 那套出厂默认之后，路由表只剩这一份。
#
#: **只用一个名字空间**：主智能体的角色名就是形态名（``profiles.AgentProfile.form``），
#: 子智能体的角色名就是 ``subagents`` 的键（planner / coder / reviewer）——
#: 两者都是「谁在干活」，不另立一套词汇。未登记的角色**不回落**、直接报错。
#
#: 界面上的行清单与这一份映射是同一个事实的两面：``config.MODEL_AGENT_ROLES`` 反着写了一遍
#: 给界面用，守卫测试逐个角色钉住两处一致。
#:
#: 「编码」是一主三从（2026-10-14 用户口径）：**主控与 coder 各占一行，另外两个角色复用已有的行** ——
#: planner 复用「方案」、reviewer 复用「审查」。这样四个角色都有出处，但只有主控与编码要配。
_AGENT_KEY: dict[str, str] = {
    "orchestrate": "coding",
    "qa": "qa",
    "analysis": "analysis",
    "plan": "plan",
    "review": "review",
    "planner": "plan",
    "coder": "coder",
    "reviewer": "review",
}


def agent_key(role: str) -> str:
    """该角色对应「模型配置」里的哪一行；未登记的角色直接报错。"""
    key = _AGENT_KEY.get(role)
    if key is None:
        raise ValueError(f"未登记的角色：{role!r}；请在 llm._AGENT_KEY 里登记它算哪个智能体")
    return key


def row_label(role: str) -> str:
    """这个角色落在「模型配置」里的哪一行、那一行叫什么（报错文案要用）。"""
    key = agent_key(role)
    return MODEL_AGENT_LABEL.get(key, key)


def _unconfigured(role: str) -> ValueError:
    """「这一行没配模型」的报错。**给用户看的**：说清哪一行、去哪儿配（拿不到别的模型顶上）。"""
    return ValueError(
        f"「{row_label(role)}」这一行还没配置模型（角色 {role}）："
        "去设置页 →「模型配置」里填上模型名与连接；正在跑的这一轮没法继续"
    )


def _row_key_for(settings: Settings, role: str) -> str | None:
    """哪一行赢了：① **该智能体那一行**（配了就按它）→ ② ``unified``（**只兜底没配的行**）。

    2026-10-15 用户口径：统一模型**不是总闸**，它只覆盖没填的部分 —— 所以这里的顺序是
    「自己的行优先」，``unified`` 退成兜底。
    """
    rows = settings.agent_models
    for key in (agent_key(role), "unified"):
        row = rows.get(key)
        if row is not None and row.model:
            return key
    return None


def _row_for(settings: Settings, role: str) -> ModelConfig | None:
    """取值的那一行；没配返回 ``None``（**不再**回落到 ``.env``）。"""
    key = _row_key_for(settings, role)
    return None if key is None else settings.agent_models[key]


def resolve_window(settings: Settings, role: str) -> int:
    """这个角色的上下文窗口。

    ① 那一行填了 → ② **按模型查**（供应商的模型详情表：先 ``(供应商, 模型)`` 再按模型名）
    → ③ 保守兜底值（默认 128k，可在设置页改）。

    这一行**没配模型**时不报错：窗口只是一条提示与压缩线，没配就说不出用哪个模型，
    直接给保守值（偏小只是早一点触发压缩）；真正会拦下来的是 ``_config_for``。

    **为什么模型优先于供应商**（2026-10-14 用户口径）：窗口是**模型**的属性 ——
    同一家 ``glm-5.3`` 是 1M、``glm-4.6`` 是 128k；挂在家上时，同一家里两个智能体配不同模型
    会拿到同一个窗口，压缩线就被推到了错的地方。

    窗口的「读」在界面上完成（用户点「测试连接」时后端去问厂商的 ``/models``，读到就存进那一行）；
    这里只做**解析**。兜底（默认 128k，可在设置页改）是用户口径里的保守取值：偏小只是早一点
    触发压缩与压力提示。
    """
    return _row_window(settings, _row_for(settings, role))[0]


def _row_window(settings: Settings, row: ModelConfig | None) -> tuple[int, str]:
    """一份连接配置的窗口 + **它是哪来的**：``row`` / ``model`` / ``fallback``。

    「哪来的」这一格是给界面用的：``fallback`` 是**我们自己的保守值**（不是厂商给的数），
    回显出来只会让人以为查到了（2026-10-15 用户就问了「这个 128K 从哪来」）——
    界面上只回显 ``row`` / ``model`` 这两种真值。
    """
    if row is not None and row.context_window > 0:
        return int(row.context_window), "row"
    model = row.model if row is not None else ""
    if model:
        base_url = (row.base_url if row is not None else "") or ""
        provider = provider_key_for(base_url, settings.provider_metas())
        window = settings.window_for_model(model, provider)
        if window > 0:
            return window, "model"
    # 兜底值本身也可以在设置页改；它被填成 0/负数时退回常量（窗口 0 会让压缩永不触发）
    window = int(getattr(settings, "fallback_context_window", 0) or 0)
    return (window if window > 0 else FALLBACK_CONTEXT_WINDOW), "fallback"


def resolve_window_with_source(settings: Settings, role: str) -> tuple[int, str]:
    """这个角色的窗口 + 它是哪来的（口径与 ``resolve_window`` 同一份，只是多回一格来源）。"""
    return _row_window(settings, _row_for(settings, role))


def resolve_model(settings: Settings, role: str) -> str:
    """这个角色**实际会用**的模型名：① 该智能体那一行 → ② ``unified``（兜底）→ ③ 没配＝空串。

    给界面标「这条回答是哪个模型说的」（2026-10-14 用户口径）用 —— 只做解析、**不建客户端**：
    为了显示一个名字去连一次模型没有道理。

    **真走了兜底这里不体现**：兜底是运行时的第二跳，账与调用都记在同一个角色名下，
    实测的 v3 事件载荷里也没有「这次是谁答的」这一格。
    """
    row = _row_for(settings, role)
    return str(row.model) if row is not None and row.model else ""


def effective_for(settings: Settings, role: str) -> dict[str, Any]:
    """这个角色**现在真的用**什么（设置页的「生效值」读它：只解析，不建客户端）。

    ``source`` 三态：

    * ``row``：这一行自己配的；
    * ``unified``：**这一行没配**，走「统一模型」兜底（用户口径 2026-10-15：统一模型只补空档，
      配了的行不受它影响）；
    * ``unset``：没配 —— 用到它的角色会当场报错。

    ``window_source`` 说的是窗口那一格的来源：``row``（行内手填）/ ``model``（按模型查到的）/
    ``fallback``（我们的保守值，**不是**厂商给的数）/ ``none``（这一行没配）。界面只回显前两种。

    两个特例（它们不是形态也不是子智能体角色）：

    * ``role="unified"`` —— 「统一模型」那一行看的是**它自己**（以前借 ``orchestrate`` 去算，
      行优先之后会显示成「编码主控」那一行的模型，2026-10-15 修）；
    * ``role="classifier"`` —— 取值顺序与 ``build_classifier_model`` 一致：
      「意图识别（大模型）」→ ``unified``。
    """
    if role == "unified":
        row = settings.agent_models.get("unified")
        key = "unified" if row is not None and row.model else None
    elif role == "classifier":
        own = settings.agent_models.get("classifier")
        if own is not None and own.model:
            key, row = "classifier", own
        else:
            unified = settings.agent_models.get("unified")
            key, row = ("unified", unified) if unified is not None and unified.model else (None, None)
    else:
        key = _row_key_for(settings, role)
        row = settings.agent_models.get(key) if key is not None else None
    if key is None or row is None:
        return {
            "role": role,
            "row": "",
            "model": "",
            "window": 0,
            "window_source": "none",
            "base_url": "",
            "has_key": False,
            "source": "unset",
        }
    window, window_source = _row_window(settings, row)
    return {
        "role": role,
        "row": key,
        "model": str(row.model),
        # 意图识别那一关没有「上下文窗口」这回事（它是一次判定调用）：不给数
        "window": 0 if role == "classifier" else window,
        "window_source": "none" if role == "classifier" else window_source,
        "base_url": row.base_url or "",
        "has_key": bool(row.api_key),
        # 「统一模型」那一行看的是它自己 → 它自己的来源就是「本行」；别的行才是"走统一模型 "
        "source": "row" if role == "unified" else ("unified" if key == "unified" else "row"),
    }


def _row_config(row: ModelConfig, *, window: int) -> ModelConfig:
    """把界面那一行变成可用的连接：窗口按解析结果补上（0 ＝ 用保守值）。"""
    return replace(row, context_window=window or FALLBACK_CONTEXT_WINDOW)


def _config_for(settings: Settings, role: str) -> ModelConfig:
    """这个角色用哪套连接：① 该智能体那一行 → ② ``unified``（只兜底没配的行）。

    **没有第 ③ 档**（2026-10-15 用户口径：干掉出厂默认）：两行都没配就当场报错，
    文案里点明是哪一行、去哪儿配。
    """
    row = _row_for(settings, role)
    if row is None:
        raise _unconfigured(role)
    return _row_config(row, window=resolve_window(settings, role))


def _wants_reasoning_effort(model: str) -> bool:
    """只有 OpenAI 的推理模型吃 ``reasoning_effort``。

    智谱 glm-5.3-flash 带这个键会把思考流关掉（实测 ``high`` → 0 字、``medium`` → 400），
    DeepSeek / Kimi / Qwen 也都不需要它。只按模型名认 OpenAI 推理家族：``gpt-5*`` /
    ``o1*`` / ``o3*`` / ``o4*``。
    """
    name = (model or "").strip().lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


def _model_from(cfg: ModelConfig, *, label: str) -> BaseChatModel:
    """按一份配置建模型实例（OpenAI 兼容）。``label`` 只用于报错时指认是谁。

    思考参数分两家：``extra_body`` 的 ``thinking`` 保留（glm / deepseek 接受，qwen
    默认开启）；``reasoning_effort`` **只给 OpenAI 推理模型** —— 2026-10-20 实测智谱
    glm-5.3-flash：不带该键时 ``reasoning_content`` 正常返回，带上 ``high`` 思考内容
    变成 0，``medium`` 直接 400（「该模型始终思考，不支持关闭思考」）。

    **温度按模型名兜一层**（``config.temperature_for``）：界面上的行没有温度这一格，
    而 kimi 系**只收 1**（传 0.3 直接 400）—— 2026-10-15 那次分析轮就是主模型失败后
    兜底到 kimi、被温度挡下、整轮挂掉。
    """
    if not cfg.model:
        raise ValueError(f"{label} 的模型名未配置")
    critical_param = cfg.api_key or cfg.base_url
    if not critical_param:
        raise ValueError("api_key and base_url are required")
    # 用带 reasoning 补丁的 ChatOpenAI：第三方网关的 ``reasoning_content`` 在
    # LangChain 的流式转换里会被丢掉，见 ``agent/chat_model.py``。
    thinking: dict[str, Any] = {"extra_body": {"thinking": {"type": "enabled"}}}
    if _wants_reasoning_effort(cfg.model):
        thinking["reasoning_effort"] = "high"
    model = ReasoningChatOpenAI(
        model=cfg.model,
        api_key=cfg.api_key or None,
        base_url=cfg.base_url or None,
        temperature=temperature_for(cfg.model, cfg.temperature),
        # 流式最后一帧也要 usage（输入/输出 tokens）。默认只有 api.openai.com 会自动开，
        # 自定义网关一律不发送 stream_options.include_usage —— 日志里那行「（无用量信息）」
        # 就是这么来的。网关不认这个字段时由 StreamUsageMiddleware 按地址降级重试。
        stream_usage=True,
        streaming=True,
        **thinking,
    )
    if cfg.context_window > 0:
        # 压缩中间件的 fraction 触发器要求模型带 max_input_tokens —— 这一格就是它读的
        # （比例线 85%，另有一条绝对上限与它 OR，见 `agent/summarization.py`）。
        # 没这一格时那边会退回纯绝对值那一档：上游见到 fraction 而拿不到 profile 是
        # 直接 ValueError，不是忽略。
        model.profile = ModelProfile(max_input_tokens=cfg.context_window)
    return model


def build_model(settings: Settings, *, role: str) -> BaseChatModel:
    """按**角色**构造模型（OpenAI 兼容）。角色路由见 ``_AGENT_KEY``，构造细节见
    ``_model_from``。这一行没配模型时抛 ``ValueError``（文案见 ``_unconfigured``）。"""
    return _model_from(_config_for(settings, role), label=f"角色 {role!r}")


def build_fallback(settings: Settings, *, role: str) -> BaseChatModel | None:
    """该角色的**兜底**模型；``None`` 表示这个角色没有兜底（``FallbackMiddleware`` 不挂）。

    只有**全局一条**保底，不做链式降级，**唯一来源是界面上「兜底模型」那一行**
    （``FALLBACK_PROVIDER`` 那个 .env 口径 2026-10-15 已作废）。四种情况没有兜底：

    - 没配（留空即关掉这个功能）；
    - 角色用的就是同一套连接 —— 拿同一个模型再试一次等于没兜；
    - 那一行没配齐（缺 key/base_url）—— 告警一声就算了：兜底是**降级**功能，
      不该因为它把整场会话的构建拖死；
    - 角色自己的那一行也没配 —— 主模型都没有，兜底更无从谈起（``_config_for`` 会报错，
      这里先按「没有兜底」返回，把报错留给主模型那一处）。
    """
    row = settings.agent_models.get("fallback")
    if row is None or not row.model:
        return None
    primary = _row_for(settings, role)
    if primary is not None and (row.base_url, row.model) == (
        primary.base_url,
        primary.model,
    ):
        return None  # 与主模型同一套：再试一次等于没兜
    if not (row.api_key or row.base_url):
        logger.warning("「兜底模型」那一行缺 key/base_url，本角色无兜底：role=%s", role)
        return None
    return _model_from(
        _row_config(row, window=row.context_window), label="兜底模型（界面配置）"
    )

# 分类只看一个词 + 一句理由；给足余量是因为思考关不掉时（供应商强制思考）
# 思考本身要占几百 token，卡太死会把正文挤成空串 —— 实测踩过这个坑。
_CLASSIFIER_MAX_TOKENS = 512


def build_classifier_model(settings: Settings) -> BaseChatModel:
    """意图分类用的模型：**界面上一行**（「意图识别（大模型）」），关思考、温度 0、不挂工具。

    分类是「选一个词」的判定任务，思考纯属浪费：实测关掉后 2.8~5.0s → 0.3s，
    completion token 从约 200 降到 1，判定结果不变。关思考的键见下面 ``extra_body``
    —— 它依供应商而异，换个模型服务得重新确认。主智能体不传该参数，两者只能是两个实例。

    取值顺序：① 「意图识别（大模型）」那一行 → ② ``unified``（统一模型的场景下它就是
    「默认大模型」）→ 都没有就抛错 —— 调用方接住之后落**关键字口径**（判定是增强，
    不是新的故障点，见 ``server/service.py``）。
    """
    cfg = settings.agent_models.get("classifier") or settings.agent_models.get("unified")
    if cfg is None or not cfg.model:
        raise ValueError(
            "「意图识别（大模型）」这一行还没配置模型："
            "去设置页 →「模型配置」→ 意图识别 里填上，或先配一行「统一模型」"
        )
    critical_param = cfg.api_key or cfg.base_url
    if not critical_param:
        raise ValueError("api_key and base_url are required")
    # **不走** ``init_chat_model``：意图识别这一路要自己的开关（关/开思考、``max_tokens``、
    # 不挂工具），和主智能体那套按形态路由的装配是两件事。独立构造也让这里的参数不会被
    # 通用工厂的默认值悄悄改掉。
    return ChatOpenAI(
        model=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        # 判定要确定性 → 温度 0；但 kimi 系**只收 1**（传 0 直接 400），那一层由
        # ``config.temperature_for`` 兜住（与主智能体同一口径）
        temperature=temperature_for(cfg.model, 0),
        max_tokens=_CLASSIFIER_MAX_TOKENS,
        # 关思考得说 vLLM 的方言：顶层未知字段（包括 DeepSeek 那套 ``thinking``）它
        # 一律静默忽略，模型照旧把 ``<think>…</think>`` 内联写在正文里 —— 首行不是意图词，
        # 解析必然失败，还白占 token。换这个键后实测 2.8~5.0s → 0.3s，解析直接命中。
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
