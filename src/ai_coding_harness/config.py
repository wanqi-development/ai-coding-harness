"""Runtime configuration loaded from environment variables / .env file."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from ai_coding_harness.core.codeplatform import Platform
from ai_coding_harness.safety.redact import secret_terms
from ai_coding_harness.utils.path_util import ROOT_DIR

logger = logging.getLogger(__name__)

DEPLOYMENT_MODES: tuple[str, ...] = ("multi", "standalone")
SANDBOX_PROVIDERS: tuple[str, ...] = ("opensandbox", "wsl")


def _default_repo_view_dir() -> str:
    """代码面板缓存的默认位置：**用户个人目录**下的 `~/.ai-coding-harness/repo_cache`。

    这是跟着机器走的路径，所以用工厂算出来而不是写死字符串；`REPO_VIEW_DIR` 照旧可覆盖。
    """
    return str(Path.home() / ".ai-coding-harness" / "repo_cache")


@dataclass(frozen=True)
class ModelConfig:
    """OpenAI-compatible model configuration (model name + endpoint + key)."""

    model: str
    api_key: str
    base_url: str
    context_window: int = 0  # 上下文窗口（tokens）；0 表示未知
    # 温度是**逐家**的，不是全局常量：kimi-k2.6 只收 1，传 0.3 直接 400
    # （``invalid temperature: only 1 is allowed for this model``），而其余几家 0.3
    # 是调优结果（温度高时方案发散）。默认 0.3，例外的那家在属性里显式给。
    temperature: float = 0.3


#: 「模型配置」界面上的行：稳定键 → 中文名（2026-10-13 用户口径：按**智能体**配，名字用中文）。
#: 接口按这张表下发，前端不自己映射 —— 免得两处各写一份中文、漂了还看不出来。
#: 三个子智能体（planner / coder / reviewer）不各占一行：planner 复用「方案」、
#: reviewer 复用「评审」、coder 单占「编码」（见 ``agent/llm._AGENT_KEY``）。
MODEL_AGENT_ROWS: tuple[tuple[str, str], ...] = (
    ("qa", "问答"),
    ("plan", "方案"),
    ("review", "评审"),
    # 「编码」是一主三从：主控与 coder 各占一行（2026-10-14 用户口径），
    # 另外两个角色**复用已有的两行** —— planner 用「方案」、reviewer 用「审查」
    # （见 ``agent/llm._AGENT_KEY``），所以这里不再为它们各开一行。
    ("coding", "编码主控"),
    ("coder", "编码"),
    ("unified", "统一模型"),
    ("fallback", "兜底模型"),
    # 意图识别二选一：JEV 一行（供应商锁定 typesafe）、大模型一行（走普通模型配置那一套）。
    ("jev", "意图识别（JEV）"),
    ("classifier", "意图识别（大模型）"),
)

#: 键 → 中文名（界面上的报错要能说出「哪一行」）
MODEL_AGENT_LABEL: dict[str, str] = dict(MODEL_AGENT_ROWS)

#: 每一行属于设置页的哪一栏（界面按它分组 —— 前端不再自己硬编码行名，2026-10-15）
MODEL_SECTIONS: tuple[tuple[str, str], ...] = (
    ("agents", "各智能体"),
    ("global", "全局"),
    ("intent", "意图识别"),
)
MODEL_AGENT_SECTION: dict[str, str] = {
    "qa": "agents",
    "plan": "agents",
    "review": "agents",
    "coding": "agents",
    "coder": "agents",
    "unified": "global",
    "fallback": "global",
    "jev": "intent",
    "classifier": "intent",
}

#: 每一行**服务哪些角色**（口径与 ``agent/llm._AGENT_KEY`` 同一份事实，反着写一遍给界面用）
MODEL_AGENT_ROLES: dict[str, tuple[str, ...]] = {
    "qa": ("qa",),
    "plan": ("plan", "planner"),
    "review": ("reviewer",),
    "coding": ("orchestrate",),
    "coder": ("coder",),
    "unified": ("*",),
    "fallback": ("*",),
    "jev": (),
    "classifier": ("classifier",),
}

#: 角色 → 界面上怎么叫它（只用于展示）
ROLE_LABELS: dict[str, str] = {
    "orchestrate": "主控",
    "planner": "规划",
    "coder": "编码",
    "reviewer": "评审",
}

#: 「编码」那一组**按角色**展示的顺序：``(角色, 落在哪一行, 是否复用别的行)``
CODING_GROUP: tuple[tuple[str, str, bool], ...] = (
    ("orchestrate", "coding", False),
    ("planner", "plan", True),
    ("coder", "coder", False),
    ("reviewer", "review", True),
)

#: 供应商被**锁死**的行（界面上那一栏不可改）：JEV 就是 typesafe 那家（2026-10-15 用户口径）
MODEL_AGENT_PROVIDER_LOCK: dict[str, str] = {"jev": "typesafe"}

#: 每一行拿**哪个角色**去算「生效值」（一行服务多个角色时取值结果一致，取头一个即可）。
#: ``fallback`` / ``jev`` 不在表里：它们不是主模型，没有「生效值」这一说。
MODEL_AGENT_EFFECTIVE_ROLE: dict[str, str] = {
    "qa": "qa",
    "plan": "plan",
    "review": "reviewer",
    "coding": "orchestrate",
    "coder": "coder",
    # 「统一模型」那一行看的是**它自己**：拿 orchestrate 去算会让它显示成「编码主控」的模型
    # （2026-10-15 用户报的「里边是 deepseek，外边显示 glm」）
    "unified": "unified",
    "classifier": "classifier",
}

#: 窗口读不到、也没填时的保守值（用户口径：保守取 128k）
FALLBACK_CONTEXT_WINDOW = 128_000


#: **可以改完立刻生效**的配置项（表 ``app_settings`` 里存的那几格）。
#:
#: 为什么需要这张表：这些值原本只在 ``.env`` 里，改一个就要重启后端。用户口径（2026-10-14）：
#: 「热生效」—— 界面上改完立刻用新的。做法是**把值写回这份 ``Settings`` 实例**（这些字段本来就
#: 是实例属性，读它们的地方一行都不用改）＋ **主智能体每轮现建、不缓存**（见 ``SessionManager.get_agent``），
#: 于是「改完这一轮就是新的模型/窗口」。
#:
#: 每条：稳定键（也是 ``app_settings.key``）／界面中文名／分组／类型／落在 ``Settings`` 的哪个字段。
@dataclass(frozen=True)
class RuntimeSetting:
    key: str
    label: str
    group: str
    #: 值的类型。``secret`` 与 ``str`` 同型，只是**接口层**要按凭据对待：
    #: 读的时候只回掩码、保存之后不再回显（见 ``api/routes/app_settings.py``）。
    kind: str  # "str" | "int" | "float" | "secret"
    field: str


RUNTIME_SETTINGS: tuple[RuntimeSetting, ...] = (
    # -- 上下文窗口配置：**只留两条阈值**（2026-10-14 用户口径）--
    # 窗口本身是**模型**的属性，界面上不再逐家/逐个配：按模型查（表 provider_models）→
    # 行内手填覆盖 → 兜底值。阈值是**提醒**的口径（环图变色），留在这儿。
    RuntimeSetting(
        "context_warn_ratio", "告警线（环图变色）", "上下文窗口配置", "float", "context_warn_ratio"
    ),
    RuntimeSetting(
        "context_alert_ratio", "危险线（给一句说明）", "上下文窗口配置", "float", "context_alert_ratio"
    ),
    RuntimeSetting(
        "context_pretrim_notice_ratio",
        "裁剪前提醒线（压缩线×比例，0=关闭）",
        "上下文压缩配置",
        "float",
        "context_pretrim_notice_ratio",
    ),
    # -- 意图识别：低置信度时转 qa 追问（0 = 关闭）--
    RuntimeSetting(
        "intent_clarify_confidence",
        "低置信度澄清阈值（0=关闭）",
        "意图识别",
        "float",
        "intent_clarify_confidence",
    ),
)

#: 键 → 定义（写接口与启动加载都按它校验/落到字段上）
RUNTIME_SETTING_BY_KEY: dict[str, RuntimeSetting] = {item.key: item for item in RUNTIME_SETTINGS}


def coerce_runtime_setting(item: RuntimeSetting, value: Any) -> Any:
    """把界面/库里来的值转成该字段的类型；转不动就抛 ``ValueError``（调用方回 400）。

    空串对字符串是合法值（比如把默认分支清空＝回到「用远程探测的默认分支」）。
    """
    if item.kind in ("str", "secret"):
        return str(value).strip()
    if item.kind == "int":
        return int(value)
    return float(value)


def apply_runtime_settings(settings: Settings, values: dict[str, Any]) -> None:
    """把 ``{键: 值}`` 落到 ``Settings`` 实例上（认不出的键、转不动的值一律跳过）。

    跳过而不是报错：一次加载/保存里坏一格，不该让其它格都不生效 —— 校验在写接口那侧做。
    """
    for key, raw in values.items():
        item = RUNTIME_SETTING_BY_KEY.get(key)
        if item is None:
            logger.warning("认不出的运行期配置项，已忽略：%s", key)
            continue
        try:
            setattr(settings, item.field, coerce_runtime_setting(item, raw))
        except (TypeError, ValueError):
            logger.warning("运行期配置项 %s 的值不合法，已忽略：%r", key, raw)

#: 内置**厂商目录**（2026-10-13 用户口径①：界面上先选提供商，再填它那一套）。
#: 只做「帮你少打字」：选中一个就把它的 base_url 带出来（仍可改），并作为「测试连接」的默认
#: 地址。base_url **取自 `.env` 的同名配置** —— 你正在用的那个网关就是它（比如 gpt 那一格你
#: 走的是自建网关），取不到就留空让你填。``custom`` 是「自定义 / 其它网关」。
PROVIDER_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("deepseek", "DeepSeek", "deepseek_base_url"),
    ("kimi", "Kimi（月之暗面）", "kimi_base_url"),
    ("glm", "GLM（智谱）", "glm_base_url"),
    ("gpt", "GPT（OpenAI 兼容）", "gpt_base_url"),
    ("qwen", "Qwen（通义）", "qwen_base_url"),
    ("custom", "自定义 / 其它网关", ""),
)


#: 提供商目录的种子文件（表为空时灌一次；之后**以库为准**）。
#: 里面每个 ``[[provider]]`` 一节的字段说明见文件本身的注释。
PROVIDER_SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "model_providers.toml"


@dataclass(frozen=True)
class ProviderMeta:
    """一家提供商的元数据（表 ``model_providers`` 的一行 / 种子文件的一节）。

    **配置的轴仍然是智能体**（见 ``model_endpoints``）：这里只决定界面「提供商」那一栏长什么样、
    选中它带出哪个地址，以及「测试连接」去哪拉模型列表、怎么鉴权。
    """

    provider: str
    display_name: str
    default_base_url: str = ""
    ui_group: str = ""
    sort_order: int = 0
    status: int = 1
    is_dynamic_model_list: bool = True
    models_path: str = "/models"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "


def load_provider_seed(path: Path | None = None) -> tuple[ProviderMeta, ...]:
    """读种子文件（缺文件 / 坏了都返回空元组，由调用方决定告警）。

    只认 ``[[provider]]`` 一节里的白名单字段；不认识的键**丢掉并告警**（与 RAGFlow 那份
    方案里 ``extra_config`` 白名单同一个思路：数据不许写逻辑，字段不许无限长）。
    """
    import tomllib  # Python 3.11+ 标准库，不引依赖

    target = path or PROVIDER_SEED_PATH
    if not target.is_file():
        return ()
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("读模型提供商种子文件失败：%s", target)
        return ()
    allowed = set(ProviderMeta.__dataclass_fields__)
    out: list[ProviderMeta] = []
    for section in data.get("provider", []):
        if not isinstance(section, dict):
            continue
        unknown = set(section) - allowed
        if unknown:
            logger.warning("提供商种子里的字段不认，已忽略：%s %s", section.get("provider"), sorted(unknown))
        fields = {key: value for key, value in section.items() if key in allowed}
        if not fields.get("provider") or not fields.get("display_name"):
            logger.warning("提供商种子缺 provider / display_name，已跳过：%r", section)
            continue
        try:
            out.append(ProviderMeta(**fields))
        except TypeError:
            logger.exception("提供商种子这一节解析失败：%r", section)
    return tuple(out)


#: **只收特定温度**的模型：**按模型名判**（与 ``model_types.model_type_of`` 同一个套路）。
#: 实测 kimi-k2.6 / k3：传 0.3 直接 400 `invalid temperature: only 1 is allowed for this model`。
#: 2026-10-15 之前这条信息写在 `.env` 的 ``kimi_model`` 属性上（那里写死 1）；那套删掉之后
#: 界面上的行**没有温度这一格**，于是任何 kimi 行都 400（连兜底都挂 —— 用户那次分析轮就这么死的）。
TEMPERATURE_HINTS: tuple[tuple[str, float], ...] = (("kimi", 1.0),)


def temperature_for(model: str, default: float) -> float:
    """这个模型该用多少温度：命中 ``TEMPERATURE_HINTS`` 就按它，否则用给的默认值。"""
    name = (model or "").lower()
    for hint, value in TEMPERATURE_HINTS:
        if hint in name:
            return value
    return default


def provider_key_for(base_url: str, metas: Sequence[ProviderMeta]) -> str:
    """按 base_url 反推「提供商」——**纯界面提示，不参与任何解析**。

    命中目录里某个地址就给它的 key，否则 ``custom``。之所以不把「提供商」存进 agent 那一行：
    它只影响界面带出哪个 base_url，而模型真正用哪套连接由 base_url + 模型 + key 决定
    （见 ``llm._config_for``）。
    """
    target = (base_url or "").strip().rstrip("/").lower()
    if not target:
        return "custom"
    for meta in metas:
        if meta.default_base_url and meta.default_base_url.strip().rstrip("/").lower() == target:
            return meta.provider
    return "custom"


def provider_meta_of(row: Any) -> ProviderMeta:
    """把 ``model_providers`` 的一行（或任何同形对象）掰成 ``ProviderMeta``。

    只做形状对齐：``None`` → 空串、缺列给默认值。不查库也不校验 —— 表里存的就是界面要的
    （种子文件的字段在 ``load_provider_seed`` 里过白名单）。
    """
    return ProviderMeta(
        provider=str(row.provider),
        display_name=str(row.display_name or row.provider),
        default_base_url=row.default_base_url or "",
        ui_group=row.ui_group or "",
        sort_order=int(row.sort_order or 0),
        status=int(row.status if row.status is not None else 1),
        is_dynamic_model_list=bool(row.is_dynamic_model_list),
        models_path=row.models_path or "/models",
        auth_header=row.auth_header or "Authorization",
        auth_prefix=row.auth_prefix or "",
    )


def provider_metas_of(rows: Iterable[Any]) -> tuple[ProviderMeta, ...]:
    """一表行 → ``ProviderMeta`` 元组（顺序交给 ``Settings.provider_metas`` 排）。"""
    return tuple(provider_meta_of(row) for row in rows)


@dataclass(frozen=True)
class ProviderModelMeta:
    """供应商的**模型详情**的一行（表 ``provider_models``，2026-10-14 加）。

    只带解析与界面要的那几格；**上下文长度挂在这里而不是供应商上** —— 同一家 ``glm-5.3`` 是 1M、
    ``glm-4.6`` 是 128k，挂在家上会把压缩线推错地方。
    """

    provider: str
    model: str
    model_type: str = "chat"
    context_window: int = 0
    #: 这个窗口/类型哪来的：probe（刷新时读到的）/ manual / seed
    source: str = "manual"
    status: int = 1


def model_meta_of(row: Any) -> ProviderModelMeta:
    """表行 → ``ProviderModelMeta``（形状对齐，不查库不校验）。"""
    return ProviderModelMeta(
        provider=str(row.provider),
        model=str(row.model),
        model_type=str(getattr(row, "model_type", "") or "chat"),
        context_window=int(getattr(row, "context_window", 0) or 0),
        source=str(getattr(row, "source", "") or "manual"),
        status=int(getattr(row, "status", 1) if getattr(row, "status", None) is not None else 1),
    )


def model_metas_of(rows: Iterable[Any]) -> tuple[ProviderModelMeta, ...]:
    """一表行 → ``ProviderModelMeta`` 元组。"""
    return tuple(model_meta_of(row) for row in rows)


def sort_provider_metas(metas: Iterable[ProviderMeta]) -> tuple[ProviderMeta, ...]:
    """留下启用项，按 ``ui_group`` → ``sort_order`` → ``provider`` 排。

    界面上的下拉直接照这个顺序画 optgroup，不再自己排 —— 排序只有一个出处：
    **现读库那份**和**内置兜底那份**都走这里。
    """
    return tuple(
        sorted(
            (meta for meta in metas if meta.status == 1),
            key=lambda item: (item.ui_group, item.sort_order, item.provider),
        )
    )


class Settings(BaseSettings):
    """Settings for the harness service.

    Values come from environment variables (no prefix) or a ``.env`` file.
    """

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 模型连接（**按智能体**配，2026-10-13）：键见 ``MODEL_AGENT_ROWS``；启动时从业务库读
    # 进来（见 api/app.py 的装配 `apply_model_endpoints`），界面上保存之后**下一轮**生效。
    #
    # **没有出厂默认**（2026-10-15 用户口径：干掉 .env 那套回落）：空 = 这一行没有模型可用，
    # 用到它的角色会当场报错（报错文案见 ``agent/llm._config_for``）；下面那些
    # ``<供应商>_*`` 字段只剩 **web 搜索**（GLM）在用，不再参与模型解析。
    #: 仅保留给无用户的单元测试 / 旧调用；生产请求按 ``user_agent_models`` 取自己的配置，
    #: 不存在跨用户回落。
    agent_models: dict[str, ModelConfig] = {}
    user_agent_models: dict[int, dict[str, ModelConfig]] = {}
    user_intent_modes: dict[int, str] = {}
    #: 意图识别走哪一关：``jev``（优先，默认）/ ``llm``。库里 ``intent_mode`` 那一行覆盖它。
    intent_mode: str = "jev"
    #: 提供商目录（表 ``model_providers`` 的当前内容；启动时读进来）。空 = 用内置那份
    #: （``PROVIDER_OPTIONS`` + ``.env`` 里的地址）—— 这样「没有种子文件 / 库是空的」时
    #: 行为与以前完全一致。
    providers: tuple[ProviderMeta, ...] = ()
    #: 供应商的**模型详情**（表 ``provider_models`` 的当前内容）—— 窗口按模型查就查它。
    #: 启动时读进来，界面上「刷新模型清单」之后当场刷新（不用重启）。
    provider_models: tuple[ProviderModelMeta, ...] = ()

    # LLM — DeepSeek (primary) and Kimi (backup), both OpenAI-compatible.
    deepseek_api_key: str = ""
    deepseek_base_url: str = ""
    deepseek_model_name: str = ""

    kimi_api_key: str = ""
    kimi_base_url: str = ""
    kimi_model_name: str = ""

    glm_api_key: str = ""
    glm_base_url: str = ""
    glm_model_name: str = ""

    gpt_api_key: str = ""
    gpt_base_url: str = ""
    gpt_model_name: str = ""

    qwen_api_key: str = ""
    qwen_base_url: str = ""
    qwen_model_name: str = ""

    # 上下文窗口（tokens）**不在这里配**（2026-10-15 删掉了逐家的 `*_CONTEXT_WINDOW`）：
    # 窗口是**模型**的属性，口径是「那一行手填 → 按模型查（表 provider_models）→ 保守兜底值
    # （``fallback_context_window``，默认 128k）」。压缩**不再只按窗口比例**触发 —— 窗口按
    # 模型查之后动辄 1M，比例那条线落在 843k，等于不压；改成「比例 OR 绝对上限」两条，
    # 取值见下面的 ``CONTEXT_SUMMARY_*`` 与 `agent/summarization.py`。
    # 上下文压力提醒的两条线（占窗口比例）。**只是提示，不改行为**：压缩按上面那两条触发。
    # 分两档是因为单档要么太晚（压完了才知道，来不及做任何决定），要么太早（刚开口就报警，
    # 成了噪声）：60% 起画一条细进度条让人**有预期地看着它涨**，80% 才给警示语。
    # 设 0 关掉对应那一档。
    context_warn_ratio: float = float(os.getenv("CONTEXT_WARN_RATIO", "0.6"))
    context_alert_ratio: float = float(os.getenv("CONTEXT_ALERT_RATIO", "0.8"))
    # 窗口读不到时的保守兜底（tokens）。原来写死在 ``FALLBACK_CONTEXT_WINDOW`` 常量里，
    # 2026-10-14 起可以在设置页改（常量仍留着当默认值与它的出处）。
    fallback_context_window: int = FALLBACK_CONTEXT_WINDOW

    # 工具裁剪（2026-10-22）：只在**压缩线**生成持久化投影事件。事件只存 tool_call_id，
    # 原始消息仍在 checkpoint；每次模型调用动态应用。没有新增可裁项时直接摘要。
    context_trim_enabled: bool = os.getenv("CONTEXT_TRIM_TOOL_RESULTS", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # 历史工具入参的选择性压缩：只压超大字符串字段、保留路径与调用参数结构。
    # 2026-10-22 起默认关：旧实现把整个 args 清成 {}，真机上出现了 `read_file 参数={}`。
    context_trim_clear_inputs: bool = (
        os.getenv("CONTEXT_TRIM_CLEAR_INPUTS", "false").strip().lower() in ("1", "true", "yes")
    )
    # 工具结果低于这个字符数不裁：普通命令输出／短状态值留着，避免占位符比原文还长。
    context_pretrim_min_result_chars: int = int(
        os.getenv("CONTEXT_PRETRIM_MIN_RESULT_CHARS", "2000")
    )
    # 历史工具入参中的单个字符串低于这个字符数不压；只对大文件正文 / patch / 长命令生效。
    context_pretrim_min_arg_chars: int = int(
        os.getenv("CONTEXT_PRETRIM_MIN_ARG_CHARS", "2000")
    )

    # 压缩（摘要）：什么时候压 / 压完留多少 / 摘要那次调用最多吃多少。三件都在这组里，
    # **不上界面**（与上面的剪裁同属「调参」，口径见 docs/config-page-design.md 第九节）。
    # 计数用**中英混合**口径（≈真实 token，见 middleware/context_trim.py），所以下面这几个数
    # 就是「真 token」的量级，不是上一版那种被高估 2.5 倍的估算值。
    context_summary_enabled: bool = os.getenv("CONTEXT_SUMMARY_ENABLED", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # 稳态（qa / analysis）：按窗口比例触发；这两类通常不会一次读大量文件。
    context_steady_trigger_fraction: float = float(
        os.getenv("CONTEXT_STEADY_TRIGGER_FRACTION", "0.70")
    )
    # 爆发（plan / coder / reviewer）：读文件是常态，上下文涨得快，提前压。
    # 2026-10-19 用户口径：先按 40% 窗口验证压缩/裁剪比例，后续可调。
    context_burst_trigger_fraction: float = float(
        os.getenv("CONTEXT_BURST_TRIGGER_FRACTION", "0.40")
    )
    # 触发线 = 比例线 **或** 下面这条绝对上限（先到先触发）。2026-10-20 真机：coder
    # 一轮 131 次调用、上下文从 15k 涨到 194k，40%×1M=400k 的线永远到不了，于是整轮
    # 没有压缩 —— 成本是「每次调用 × 当前上下文」，必须在 40 万之前刹住。
    context_burst_trigger_tokens: int = int(
        os.getenv("CONTEXT_BURST_TRIGGER_TOKENS", "120000")
    )
    context_steady_trigger_tokens: int = int(
        os.getenv("CONTEXT_STEADY_TRIGGER_TOKENS", "200000")
    )
    # 窗口解析不出来时的兜底线（比例线没法算，只能用绝对值）。设 0 会被上游校验拒掉。
    context_summary_trigger_tokens: int = int(
        os.getenv("CONTEXT_SUMMARY_TRIGGER_TOKENS", "120000")
    )
    # 压完保留最近这么多 token。上游那个 ``("fraction", 0.10)`` 在 1M 窗口下等于留 100k，
    # 与 40%/70% 的触发线相比，窗口 10% 在大窗口下仍然太大 —— 所以这里用绝对值。
    context_summary_keep_tokens: int = int(os.getenv("CONTEXT_SUMMARY_KEEP_TOKENS", "20000"))
    # 摘要那次调用最多吃多少 token（上游 ``trim_tokens_to_summarize``）。上游**工厂**的默认是
    # ``None`` ＝完全不裁，等于把被逐出的全部历史再发一遍给主控那个贵模型，还可能自己溢出
    # （溢出时它不像主调用那样有兜底，会把整轮带崩）。原始全文另有 offload
    # （``/conversation_history/{thread_id}.md``）且摘要里带路径可回看，所以这里裁掉的是
    # 「更早的那部分不进摘要」，不是「丢了」。
    context_summary_input_tokens: int = int(
        os.getenv("CONTEXT_SUMMARY_INPUT_TOKENS", "40000")
    )
    # 单轮输入合计超过这条线记一条 WARNING（只告警，不改变执行）。
    turn_cost_warn_tokens: int = int(
        os.getenv("TURN_COST_WARN_TOKENS", "3000000")
    )
    #: 临近压缩线时先提醒模型写工作笔记的线 = 压缩线 × 这个比例（0=关闭）。
    #: 90% 给模型留出一次正常回复的机会，把「已读到什么、还需要什么」写成 KEEP 清单；
    #: 到线后的应急裁剪按这份清单保留，不再只保当前步骤。
    context_pretrim_notice_ratio: float = float(
        os.getenv("CONTEXT_PRETRIM_NOTICE_RATIO", "0.9")
    )

    # 兜底（2026-10-15 起**只有**「兜底模型」那一行这一个来源，``FALLBACK_PROVIDER`` 作废）：
    # 主模型重试耗尽时换它再试一次；那一行留空 = 关掉兜底。
    #
    # 兜底的重试有代价：智能体一轮对话要跑多次模型节点（model → tools → model …），
    # 主导死着的时候**每一轮**都得先撞死一次才轮到兜底，半路断流那种还会把半截句子重复
    # 吐进流里。所以主导连续失败到阈值就开闸，冷却期内不再撞它，直接走兜底。
    # 阈值 0 = 关掉熔断（退回每轮都撞）；冷却到点放**一次**探针，探针成功即恢复。
    # 状态按主导模型名记、进程内共享（多个 worker 各记一份，跨进程要做就得进 Redis）。
    fallback_circuit_threshold: int = int(os.getenv("FALLBACK_CIRCUIT_THRESHOLD", "3"))
    fallback_circuit_cooldown_seconds: float = float(
        os.getenv("FALLBACK_CIRCUIT_COOLDOWN_SECONDS", "60")
    )

    # Redis（**可选**）：多 worker 时的跨进程旁路状态 —— 运行缓冲（刷新后接回哪一轮、
    # 以游标重放）、会话运行权（同一会话不许两个 worker 同时跑）、模型熔断状态。
    # 留空 `REDIS_URL` = 全部留在进程内，就是 2026-09-26 之前的行为（单 worker 够用）。
    # `REDIS_REQUIRED=true` 时连不上直接启动失败 —— 多 worker 部署该开：静默退回进程内
    # 意味着「你以为共享的东西其实各 worker 一份」，比启动失败更难查（见 infra/redis_cache.py）。
    redis_url: str = os.getenv("REDIS_URL", "")
    # 密码单独一个键（而不是塞进 URL）：① URL 会被日志原样打出来，密码进去就跟着泄；
    # ② 两边都写时以这个键为准（见 infra/redis_cache.default_client_factory）。
    redis_password: str = os.getenv("REDIS_PASSWORD", "")
    redis_prefix: str = os.getenv("REDIS_PREFIX", "harness:")
    redis_required: bool = os.getenv("REDIS_REQUIRED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # 单条命令的超时：Redis 半死时宁可降级，也不能让一轮对话卡在这里。
    # **远端 / 走隧道的 Redis 必须放大**：2026-10-11 实测 —— 127.0.0.1:6379 其实是 SSH 隧道
    # （PING 中位 62ms、p95 92ms，服务端慢日志是空的），1 秒只给 16 倍余量，隧道抖一下
    # （TCP 重传通常 ≥200ms、连丢可到 1s 以上）就把进程打到降级。默认给 5 秒。
    redis_timeout_seconds: float = float(os.getenv("REDIS_TIMEOUT_SECONDS", "5.0"))
    # 版本化 Session 快照缓存；token 在 Redis 里始终加密，TTL 只是兜底。
    redis_session_cache_ttl_seconds: int = int(
        os.getenv("REDIS_SESSION_CACHE_TTL_SECONDS", "300")
    )
    session_cache_encryption_key: str = os.getenv("SESSION_CACHE_ENCRYPTION_KEY", "")
    session_cache_key_version: int = int(os.getenv("SESSION_CACHE_KEY_VERSION", "1"))
    verify_repo_access: bool = os.getenv("VERIFY_REPO_ACCESS", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # 降级窗口：一次失败后退回进程内这么多秒，**过了就再试一次**（不是永久降级 ——
    # 理由与实测见 infra/redis_cache.py 的 ``RedisBacked.run``）。设 0 ＝ 永不重试（旧行为）。
    redis_degrade_cooldown_seconds: float = float(
        os.getenv("REDIS_DEGRADE_COOLDOWN_SECONDS", "60")
    )
    # 运行缓冲的存活时间与容量上限（超出上限从最老的帧开始丢）
    run_buffer_ttl_seconds: int = int(os.getenv("RUN_BUFFER_TTL_SECONDS", "3600"))
    run_buffer_max_frames: int = int(os.getenv("RUN_BUFFER_MAX_FRAMES", "2000"))
    # 会话运行权的 TTL（持有者进程被 kill 后的兜底）与续期间隔
    run_lock_ttl_seconds: int = int(os.getenv("RUN_LOCK_TTL_SECONDS", "120"))
    run_poll_interval_seconds: float = float(os.getenv("RUN_POLL_INTERVAL_SECONDS", "0.2"))

    # 意图分类（每轮一次）：强/弱关键字直通，其余交 LLM 判定，失败回落关键字。
    # 分类与主智能体的差别只在参数：分类必须关思考（vLLM 只认 chat_template_kwargs
    # 那个键，实测 2.8~5.0s → 0.3s），主智能体不传该参数。同一模型两套参数，所以它的
    # **模型连接单独一行**（「意图识别（大模型）」，见 ``llm.build_classifier_model``）；
    # ``INTENT_MODEL`` / ``INTENT_BASE_URL`` / ``INTENT_API_KEY`` 三个 .env 键 2026-10-15 作废。
    intent_llm_enabled: bool = True
    intent_timeout_seconds: float = 5.0
    #: 低置信度澄清阈值：JEV / 大模型的 confidence 低于它时，本轮转 qa 先追问用户。
    #: 0 = 关闭。2026-10-19 200 条实测：JEV < 0.55 的 16 条只对 43.8%，≥0.55 的 147 条
    #: 对 91.2%；0.50 / 0.60 分别会问 6.7% / 14.1%。0.55 是“低置信准确率明显塌陷、
    #: 追问量约 10%”的折中。
    intent_clarify_confidence: float = 0.55
    # 分类时附带的历史用户消息条数（指代要靠它，如「就按你说的弄」）
    intent_history_turns: int = 3

    # JEV（SystemOne，决策型模型）做意图判定 —— 二选一里的另一侧（见 server/intent.py）：
    # 口令 → **JEV** → 关键字口径。凭据来自界面上的「意图识别（JEV）」那一行
    # （2026-10-15 起：`TYPESAFE_*` 三个 .env 键作废，供应商锁定 typesafe）。
    # SDK 是 `typesafe-sdk`，**懒加载**：没装这个包或那一行没配齐就跳过 JEV，落关键字口径。
    intent_jev_enabled: bool = True
    typesafe_api_key: str = ""
    typesafe_base_url: str = ""
    #: JEV 的模型名：那一行填了就用它，留空 = SDK 默认的 ``jev-latest``
    typesafe_model_name: str = "jev-latest"
    typesafe_timeout_seconds: float = float(os.getenv("TYPESAFE_TIMEOUT_SECONDS", "10"))

    # Service
    host: str = "127.0.0.1"
    port: int = 8000

    # 部署档位。默认 multi 保持现有 PostgreSQL + Redis + OpenSandbox 行为。
    deployment_mode: str = os.getenv("DEPLOYMENT_MODE", "multi").strip().lower()
    #: 空＝按 deployment_mode 推导。standalone 默认 SQLite，multi 默认 PostgreSQL。
    database_url: str = os.getenv("DATABASE_URL", "").strip()
    #: standalone 未显式给 DATABASE_URL 时的统一 SQLite 文件。
    sqlite_path: str = os.getenv("SQLITE_PATH", "").strip()
    #: 空＝standalone 使用 wsl，multi 使用 opensandbox。
    sandbox_provider: str = os.getenv("SANDBOX_PROVIDER", "").strip().lower()

    # WSL 本地沙箱（standalone）。后端在 WSL 内运行时这些路径就是 Linux 原生路径。
    wsl_distro: str = os.getenv("WSL_DISTRO", "ai-coding-harness")
    wsl_user: str = os.getenv("WSL_USER", "harness")
    wsl_root: str = os.getenv("WSL_ROOT", "/home/harness/.ai-coding-harness/sessions")
    wsl_bwrap: str = os.getenv("WSL_BWRAP", "/usr/bin/bwrap")
    wsl_command_timeout_seconds: int = int(
        os.getenv("WSL_COMMAND_TIMEOUT_SECONDS", "600")
    )
    #: 第一版先保留普通网络；后续网络 allowlist 代理接好后可切换。
    wsl_allow_network: bool = (
        os.getenv("WSL_ALLOW_NETWORK", "true").strip().lower()
        in ("1", "true", "yes")
    )

    # 主智能体图的最大超级步数（LangGraph recursion_limit）。
    # deepagents 给自己绑定的是 9999，但该绑定在 astream_events 路径下不生效，
    # 不显式传入就退化为 LangGraph 默认的 25 —— 主控光探查仓库就能用掉 8 轮，
    # 远不够「探查 → 方案 → 编码 → 推送 → 建 PR → 评论」的完整链路。
    # 注：子智能体各自持有自己的步数预算（同样是 9999），不计入本值。
    agent_recursion_limit: int = int(os.getenv("AGENT_RECURSION_LIMIT", "200"))

    # 多用户认证：浏览器只持有不透明 token，会话事实存 PostgreSQL，因此多 worker 可共享。
    auth_session_cookie: str = os.getenv("AUTH_SESSION_COOKIE", "harness_session")
    auth_csrf_cookie: str = os.getenv("AUTH_CSRF_COOKIE", "harness_csrf")
    auth_cookie_secure: bool = (
        os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower()
        in ("1", "true", "yes")
    )
    auth_session_ttl_hours: int = int(os.getenv("AUTH_SESSION_TTL_HOURS", "168"))
    auth_idle_timeout_hours: int = int(os.getenv("AUTH_IDLE_TIMEOUT_HOURS", "24"))
    auth_login_max_failures: int = int(os.getenv("AUTH_LOGIN_MAX_FAILURES", "5"))
    auth_login_lock_seconds: int = int(os.getenv("AUTH_LOGIN_LOCK_SECONDS", "60"))

    # 空转刹车：同一工具、同一参数在本轮出现到第 N 次就不再执行（回一条警告 ToolMessage），
    # 第 N+1 次直接中止本轮并补一条中断记录。计的是**本轮出现次数**、不要求相邻 ——
    # 2026-09-21 那次 686 秒的打转里 `git status` 与 `git log` 是交错的。0 = 关掉。
    loop_guard_repeats: int = int(os.getenv("LOOP_GUARD_REPEATS", "3"))

    # handoffTask 把内层结果交回外层时的正文上限（字符）。内层是完整的一轮智能体运行，
    # 正文可能上万字，而它在外层只是一个工具返回值 —— 全带回来既撑上下文又没意义，
    # 外层要的是结论（见 ``tools/handoff.py``）。
    handoff_result_max: int = int(os.getenv("HANDOFF_RESULT_MAX", "4000"))

    # 日志：控制台 + 接口日志 + 智能体日志（滚动文件）
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_api_file: str = "api.log"
    log_agent_file: str = "agent.log"
    log_max_bytes: int = 10_000_000  # 单文件上限（约 10MB，超出即滚动）
    log_backup_count: int = 5  # 保留的历史文件数
    # 工具参数 / 委派描述写进日志时的字符上限（超出截断并加省略号）。
    # 调大能看全待办列表与委派描述，但 write_file 这类大参数会明显推高 agent.log 体积。
    log_tool_arg_max: int = 2000
    # 工具返回值写进日志时的字符上限。工具的成败常只体现在返回值里（如记忆写入被拒绝），
    # 不记就无法从日志判断「工具到底成没成」——所以单独留一个上限，比参数上限小得多。
    log_tool_result_max: int = 300
    # 委派（task）返回值写进日志时的字符上限，比普通工具返回宽：这一行是「一个子智能体这趟
    # 到底干成了什么」唯一的落盘记录，300 字连一份 PR 摘要都放不下；而它每轮只有几条。
    log_delegation_result_max: int = 1000

    # Persistence（LangGraph checkpointer + store，PostgreSQL；经 SSH 隧道走本地）
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "ai_coding_harness"
    postgres_user: str = "postgres"
    postgres_password: str = ""
    postgres_connect_timeout_seconds: int = int(
        os.getenv("POSTGRES_CONNECT_TIMEOUT_SECONDS", "5")
    )

    # OpenSandbox
    opensandbox_domain: str = "192.168.72.128:8080"
    opensandbox_api_key: str = os.getenv("OPENSANDBOX_API_KEY", "")
    sandbox_image: str = "opensandbox/code-interpreter:v1.1.0"
    sandbox_root: str = "/ai_coding"
    sandbox_timeout_minutes: int = 60
    opensandbox_request_timeout_seconds: int = 600
    # 沙箱探活缓存：距上次探活成功不足该秒数就跳过真探（0 = 每次调用都真探，更准但更慢）
    sandbox_health_probe_ttl_seconds: float = 5.0
    # 沙箱续期：距上次续期超过该秒数就再续一次（把到期时间设为「现在 + SANDBOX_TIMEOUT_MINUTES」），
    # **0 = 关掉续期**。默认 1800（30 分钟）= 默认 TTL 的一半：活跃会话不会因为「一轮跑太久」
    # 或「用户隔一阵回来」被回收，也就少一次重建（重建会丢容器里非卷的内容）。
    sandbox_renew_interval_seconds: float = 1800

    # 沙箱运行时资产：镜像不再预置 skill / 规则 / 凭据脚本，由 SandboxProvider 在每次
    # 创建与重连时推入（容器重建会丢掉非卷文件，所以重连也要重推）。资产全部集中在仓库
    # 的 `sandbox/` 下 —— 这里三处路径都相对仓库根解析（provider._repo_path）。
    sandbox_assets_dir: str = "sandbox/deploy/sandbox"  # askpass / setup.sh 源目录
    sandbox_local_skills_dir: str = "sandbox/skills"  # skill 源目录
    sandbox_local_policies_dir: str = "sandbox/policies"  # 规则源目录
    sandbox_sbin_dir: str = "/usr/local/sbin"  # askpass / setup.sh 在沙箱内的落点
    # Maven 加速与缓存（2026-09-25）：`sandbox/maven/settings.xml` 里配了阿里云公共仓库镜像，
    # 并把本地仓库固定写到 `/ai_coding/.m2/repository`；下面三项决定这个目录是不是挂在卷上
    # —— 挂上就跨容器重建存活（不然每次构建都要重新下依赖）。
    sandbox_local_maven_dir: str = "sandbox/maven"  # settings.xml 的源目录（相对仓库根）
    sandbox_maven_dir: str = "/ai_coding/maven"  # 推入沙箱后的落点
    sandbox_maven_cache_enabled: bool = True  # 关掉则不挂缓存卷（本地仓库退化成容器内目录）
    # true = 所有会话共用一个缓存卷（跨会话也是热的；要求存储能多机同时挂：Docker 单机、
    # 或 RWX 的 PVC）；false = 按会话各挂一个（独占无并发写，代价是新会话冷启动）。
    sandbox_maven_cache_shared: bool = True
    sandbox_maven_cache_prefix: str = "ai-coding-m2"
    sandbox_maven_cache_mount_path: str = "/ai_coding/.m2"
    # 静态 git 身份（只有 commit 需要，与认证无关）
    sandbox_git_user_name: str = "ai-coding-agent"
    sandbox_git_user_email: str = "agent@example.com"

    # 主智能体回给用户的「输出形态」规则（**仓库根相对**，与上面三处同口径，但
    # **不进沙箱** —— 由 ``prompts.build_main_prompt`` 直接读盘拼进 system prompt）。
    # 只挂主智能体：它管的是「怎么对用户说话」，子智能体不直接与用户对话。
    # 读在构建期，所以改完这个文件**新建会话即生效**，不用重启后端。
    agent_output_style_file: str = "adhd/AGENT.md"

    # 工作区持久化（Volume：容器可换、坏能重挂）
    sandbox_volume_enabled: bool = True
    sandbox_volume_prefix: str = "ai-coding-ws"
    sandbox_volume_delete_on_termination: bool = False
    # 卷后端：pvc（本地命名卷 / K8s PVC）| host（NFS 等共享路径）| ossfs | none
    sandbox_volume_backend: str = "pvc"
    # host 后端：共享存储挂载根（如 NFS 挂载点），每会话用其子目录
    sandbox_volume_host_path: str = ""
    # pvc 后端（K8s 用；Docker 下留空即可）
    sandbox_volume_storage_class: str = ""
    sandbox_volume_storage: str = ""
    sandbox_volume_access_modes: str = ""  # 逗号分隔，如 "ReadWriteMany"
    # ossfs 后端
    sandbox_oss_bucket: str = ""
    sandbox_oss_endpoint: str = ""
    sandbox_oss_version: str = "2.0"
    sandbox_oss_access_key_id: str = ""
    sandbox_oss_access_key_secret: str = ""

    # 注入沙箱的令牌环境变量名（由 setup 脚本消费后写入 .secrets 并 unset）。
    # 与平台**无关**：一个会话只装一个令牌，沙箱侧不必知道它来自 Gitee 还是 GitHub ——
    # 平台差异留在工具层（见 tools/git.py 的 build_git_client）。
    sandbox_git_token_env: str = "GIT_TOKEN"
    # 注入沙箱的 git 认证用户名变量名（默认 oauth2 / x-access-token，PAT 也可填真实用户名；
    # 默认值由 codeplatform.Platform.default_git_username 给）
    sandbox_git_username_env: str = "GIT_USERNAME"
    # 沙箱创建后由应用经 `execute` 执行的初始化脚本（由 sandbox/deploy/sandbox/setup.sh
    # 推入，读沙箱内 `$GIT_TOKEN` 写 `.secrets`；必须不阻塞，勿含 `exec tail`）
    sandbox_setup_command: str = "/usr/local/sbin/setup.sh"

    # 代码平台 base URL 在部署级配置；token / git 用户名是用户级配置
    # （``user_settings`` → ``settings_for_user``）。下面两格只留给无用户的旧测试夹具，
    # 生产用户绝不回落到这里。
    gitee_access_token: str = ""
    gitee_username: str = ""
    gitee_base_url: str = "https://gitee.com/api/v5"
    # 代码面板默认打开哪个分支。**留空 ＝ 按远程探测**（裸仓 HEAD 指着谁就是谁）；
    # 填了就**以它为准** —— 2026-10-14 起可在设置页改（用户口径：代码缓存配置），
    # 所以填错会让面板打开一个不存在的分支。原来的默认值是写死的 master / main。
    gitee_base_branch: str = ""

    github_access_token: str = ""
    github_username: str = ""
    github_base_url: str = "https://api.github.com"
    github_base_branch: str = ""

    # 右侧栏「代码」面板：**宿主机自己** clone 一份仓库做只读浏览（不经沙箱）。
    # 为什么要有：沙箱会没（TTL 到期、会话结束、首次创建失败），而「看看这个仓库的代码」
    # 是随时都可能想做的事；本机 clone 一份之后，面板与沙箱彻底解耦（见 infra/repo_cache.py）。
    repo_view_enabled: bool = True
    # 缓存根目录，按 owner/repo 各一份裸仓。**默认落在用户个人目录下**
    # （`~/.ai-coding-harness/repo_cache`），不写进项目目录 —— 那是下载来的第三方代码，
    # 混在项目里既会被误当成项目文件，也会跟着项目被备份/同步走。
    # 给了相对路径就按**当前工作目录**解析（`infra/repo_cache.py` 会把它转成绝对路径）。
    repo_view_dir: str = Field(default_factory=_default_repo_view_dir)
    # git 可执行文件；留空 = 自动探测（PATH + Windows 常见安装位置）
    repo_view_git: str = ""
    # 单条 git 命令的超时（秒）。首次 clone 可能很大，给得比探活宽得多。
    repo_view_timeout_seconds: float = float(os.getenv("REPO_VIEW_TIMEOUT_SECONDS", "300"))
    # 单个文件回给前端的字节上限；超过只回「太大」+ 大小，不把正文塞进 JSON
    repo_view_file_max_bytes: int = int(os.getenv("REPO_VIEW_FILE_MAX_BYTES", "1048576"))

    # Sandbox working-directory layout (paths under sandbox_root)
    sandbox_project_dir: str = "/ai_coding/project"
    sandbox_policies_dir: str = "/ai_coding/policies"
    sandbox_skills_dir: str = "/ai_coding/skills"
    sandbox_output_dir: str = "/ai_coding/output"
    sandbox_review_dir: str = "/ai_coding/review"
    sandbox_tmp_dir: str = "/ai_coding/tmp"

    # Memory routing prefix (files under this path go to StoreBackend)
    memories_prefix: str = "/memories/"

    @property
    def git_askpass_path(self) -> str:
        """沙箱内 askpass 脚本路径（git 经 ``GIT_ASKPASS`` 调用它取令牌）。

        与平台无关：脚本只把「用户名 + ``.secrets/git_token``」交给 git，令牌来自哪家
        平台沙箱不关心。
        """
        return f"{self.sandbox_sbin_dir}/git-askpass"

    def token_for(self, platform: Platform) -> str:
        """该 Settings 视图里的平台令牌。

        生产调用方拿到的都应是 ``settings_for_user`` 生成的用户视图；没有 user_id 的旧测试
        夹具才直接读这两格。
        """
        return (
            self.github_access_token
            if platform is Platform.GITHUB
            else self.gitee_access_token
        )

    def username_for(self, platform: Platform) -> str:
        """该平台配置里的 git 用户名；留空走平台默认值。"""
        return (
            self.github_username if platform is Platform.GITHUB else self.gitee_username
        )

    def code_platform_secret_values(self) -> tuple[str, ...]:
        """两家的访问令牌 —— 给脱敏词表用。

        它们可能在设置页里配（2026-10-20 起），也就可能被模型复述进日志 / 记录里；
        与会话里那份（``session.repo.token``）一起进词表，见 ``server/turn/runner.py``。
        """
        return secret_terms(self.gitee_access_token, self.github_access_token)

    def model_secret_values(self) -> tuple[str, ...]:
        """所有**模型凭据**（界面配的那些行 + ``.env`` 里几家 + JEV / 分类）—— 给脱敏词表用。

        2026-10-13 之前词表里只有 git token（模型 key 只在 ``.env``、不进沙箱）。现在 key
        既可能出现在界面上、也可能被模型自己复述出来，所以一并纳入；
        ``redact.secret_terms`` 自己会滤掉空值与过短的值。
        """
        from_env = (
            self.deepseek_api_key,
            self.kimi_api_key,
            self.glm_api_key,
            self.gpt_api_key,
            self.qwen_api_key,
            self.typesafe_api_key,
        )
        from_rows = tuple(row.api_key or "" for row in self.agent_models.values())
        return tuple(value for value in (*from_env, *from_rows) if value)

    def provider_metas(self) -> tuple[ProviderMeta, ...]:
        """启动时读进来的那份目录 —— 现在只当**兜底**用（见下）。

        界面上的目录是**每条请求现读库**（`api/routes/model_config/common.py::_provider_metas`），
        所以改库 + 刷新就生效、不用重启。这里这份在两种情况下生效：
        库里读不出来（表坏了 / 连不上），以及压根没有种子文件的老环境 —— 退回
        ``PROVIDER_OPTIONS`` + ``.env`` 里的地址，行为与加表之前一致。

        它**不预填给未配置的智能体行**（用户口径：不给默认值）。
        """
        if self.providers:
            return sort_provider_metas(self.providers)
        return sort_provider_metas(
            ProviderMeta(
                provider=key,
                display_name=label,
                default_base_url=(getattr(self, attr, "") or "") if attr else "",
                sort_order=index * 10,
            )
            for index, (key, label, attr) in enumerate(PROVIDER_OPTIONS)
        )

    def models_of_provider(self, provider: str, *, model_type: str = "") -> tuple[ProviderModelMeta, ...]:
        """这家启用的模型（可只看某一类）—— 界面上的候选清单用它。"""
        return tuple(
            item
            for item in self.provider_models
            if item.provider == provider
            and item.status == 1
            and (not model_type or item.model_type == model_type)
        )

    def window_for_model(self, model: str, provider: str = "") -> int:
        """这个模型的上下文长度（tokens）；不知道就 0。

        **先按 ``(供应商, 模型)`` 精确查，再按模型名全局查一次** —— 用户自己的网关（``custom``）
        地址对不上任何一家，但模型名通常还是同一个，同名模型的窗口是一样的。启用项才算数。
        """
        wanted = (model or "").strip()
        if not wanted:
            return 0
        loose = 0
        for item in self.provider_models:
            if item.status != 1 or item.model != wanted or item.context_window <= 0:
                continue
            if item.provider == provider:
                return item.context_window
            loose = loose or item.context_window
        return loose

    def api_base_for(self, platform: Platform) -> str:
        """该平台的 API 根地址。"""
        return (
            self.github_base_url if platform is Platform.GITHUB else self.gitee_base_url
        )

    def default_branch_for(self, platform: Platform) -> str:
        """该平台常见的默认基准分支：Gitee 多为 ``master``、GitHub 多为 ``main``。

        目前只作为**口径**存在（实际 base 一直由模型给工具的入参决定），放在这里是为了
        将来要读它时只有一个出处。
        """
        return (
            self.github_base_branch
            if platform is Platform.GITHUB
            else self.gitee_base_branch
        )

    @property
    def postgres_url(self) -> str:
        """PostgreSQL 连接串（psycopg 格式）。"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_async_url(self) -> str:
        """SQLAlchemy 异步连接串（psycopg3 驱动，用于业务库 ORM / Alembic）。"""
        return self.postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def is_standalone(self) -> bool:
        return self.deployment_mode == "standalone"

    @property
    def effective_sandbox_provider(self) -> str:
        if self.sandbox_provider:
            return self.sandbox_provider
        return "wsl" if self.is_standalone else "opensandbox"

    @property
    def standalone_database_path(self) -> Path:
        raw = self.sqlite_path
        if not raw and self.database_url.startswith("sqlite"):
            database = make_url(self.database_url).database
            if database and database != ":memory:":
                raw = database
        if not raw:
            raw = str(Path.home() / ".ai-coding-harness" / "harness.sqlite3")
        return Path(raw).expanduser().resolve()

    @property
    def resolved_database_url(self) -> str:
        """当前部署实际使用的 SQLAlchemy URL。"""
        if self.database_url:
            return self.database_url
        if self.is_standalone:
            return f"sqlite+aiosqlite:///{self.standalone_database_path.as_posix()}"
        return self.postgres_async_url

    def validate_deployment(self) -> None:
        """启动前校验部署档位，避免多 worker 静默退回单进程语义。"""
        if self.deployment_mode not in DEPLOYMENT_MODES:
            raise ValueError(
                f"DEPLOYMENT_MODE 只能是 {' / '.join(DEPLOYMENT_MODES)}"
            )
        provider = self.effective_sandbox_provider
        if provider not in SANDBOX_PROVIDERS:
            raise ValueError(
                f"SANDBOX_PROVIDER 只能是 {' / '.join(SANDBOX_PROVIDERS)}"
            )
        url = self.resolved_database_url
        if self.is_standalone:
            if not url.startswith("sqlite"):
                raise ValueError("standalone 必须使用 SQLite DATABASE_URL")
            if url.rstrip("/").endswith(":memory:"):
                raise ValueError("standalone 必须使用单一 SQLite 文件，不能使用 :memory:")
            if provider != "wsl":
                raise ValueError("standalone 当前只支持 SANDBOX_PROVIDER=wsl")
            if self.redis_required:
                raise ValueError("standalone 不能开启 REDIS_REQUIRED")
            if self.database_url and self.sqlite_path:
                database_path = make_url(self.database_url).database
                if database_path and Path(database_path).expanduser().resolve() != (
                    self.standalone_database_path
                ):
                    raise ValueError(
                        "DATABASE_URL 与 SQLITE_PATH 必须指向同一个 SQLite 文件"
                    )
            return
        if not url.startswith("postgresql"):
            raise ValueError("multi 必须使用 PostgreSQL DATABASE_URL")
        if not self.redis_url:
            raise ValueError("multi 必须配置 REDIS_URL")
        if provider != "opensandbox":
            raise ValueError("multi 当前只支持 SANDBOX_PROVIDER=opensandbox")

@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
