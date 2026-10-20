"""模型配置接口的共享解析、探测与快照辅助。"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

import httpx
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.api.schemas import (
    CodingGroupRole,
    ModelConfigEffective,
    ModelConfigRow,
    ModelConfigSnapshot,
    ProviderModelItem,
    ProviderModelList,
    ProviderOption,
    SectionOption,
)
from ai_coding_harness.config import (
    CODING_GROUP,
    MODEL_AGENT_EFFECTIVE_ROLE,
    MODEL_AGENT_PROVIDER_LOCK,
    MODEL_AGENT_ROLES,
    MODEL_AGENT_ROWS,
    MODEL_AGENT_SECTION,
    MODEL_SECTIONS,
    ROLE_LABELS,
    ProviderMeta,
    Settings,
    model_metas_of,
    provider_key_for,
    provider_metas_of,
    sort_provider_metas,
)
from ai_coding_harness.core.model_types import is_text_model, model_type_of
from ai_coding_harness.db import dao
from ai_coding_harness.db.models import ModelEndpoint

logger = logging.getLogger(__name__)

#: 探测厂商 ``/models`` 的超时：这只是界面上的一个按钮，不该让请求挂在那儿
_PROBE_TIMEOUT_SECONDS = 10.0

#: ``/models`` 返回里可能装窗口的字段（各家叫法不一，能读到就读，读不到就算了）。
#: 实测：deepseek 用 ``context_window``、glm / kimi 用 ``context_length``、openai 什么都不给。
_WINDOW_KEYS = ("context_length", "max_context_length", "max_input_tokens", "context_window")

#: 模型条目里可能装名字的字段（各家叫法不一；同样**任意层级**里找）
_NAME_KEYS = ("id", "model", "model_name", "name")

#: 提供商的键只允许小写字母 / 数字 / 下划线 / 连字符（模型行与 agent 行的外键目标）
_PROVIDER_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def mask_key(value: str | None) -> str:
    """凭据打码：``sk-…e2b7``。太短就整段星号 —— 界面只用来「知道填没填」，不回明文。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…{value[-4:]}"


def _positive_int(value: Any) -> int:
    """把窗口取成正整数（有的网关回字符串 ``"1048576"``）；读不出回 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return max(0, number)
    return 0


def _deep_window(obj: Any) -> int:
    """在**任意层级**里找窗口（读不到返回 0）。

    为什么要往下钻：各家把窗口塞的位置不一样 —— deepseek / glm / kimi 平铺在条目上，
    qwen 的原生 ``/api/v1/models`` 埋在 ``output.models[].model_info.context_window``
    （2026-10-15 用户实测）。同一层先按 ``_WINDOW_KEYS`` 的顺序找，再往下递归 ——
    顺序固定，读到的值才可复现。
    """
    if isinstance(obj, dict):
        for key in _WINDOW_KEYS:
            value = _positive_int(obj.get(key))
            if value:
                return value
        for value in obj.values():
            found = _deep_window(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _deep_window(value)
            if found:
                return found
    return 0


def _window_of(item: Any) -> int:
    """从一个模型条目里取窗口（读不到返回 0）。"""
    return _deep_window(item)


def _name_of(item: Any) -> str:
    """从一个模型条目里取模型名（任意层级；读不到回空串）。"""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in _NAME_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in item.values():
            found = _name_of(value)
            if found:
                return found
    return ""


def _model_items(payload: Any) -> list[Any]:
    """把响应掰成「一个模型一项」的列表 —— 认两种形状（2026-10-15）：

    * OpenAI 兼容：``{"data": [{"id": …} | "…"]}``（deepseek / glm / kimi / openai…）；
    * qwen 原生：``{"output": {"models": [{"model_info": {...}}]}}``（``/api/v1/models``）。

    认不出的形状回空列表：**不猜** —— 猜错会把别的列表当成模型清单（那是更难查的错）。
    """
    if not isinstance(payload, dict):
        return []
    for candidate in (payload.get("data"), payload.get("models")):
        if isinstance(candidate, list):
            return candidate
    output = payload.get("output")
    if isinstance(output, dict):
        for candidate in (output.get("models"), output.get("data")):
            if isinstance(candidate, list):
                return candidate
    return []


async def fetch_provider_models(
    base_url: str,
    api_key: str,
    *,
    path: str = "/models",
    auth_header: str = "Authorization",
    auth_prefix: str = "Bearer ",
) -> tuple[list[str], int]:
    """问厂商的模型清单接口：返回 ``(模型名清单, 窗口)``。

    返回的清单**已经滤成文本模型**（见 ``parse_models``）。

    路径与鉴权头来自那家提供商的目录行（``path`` / ``auth_header`` / ``auth_prefix``）——有的网关是
    ``/v1/models``、有的用 ``x-api-key``，不都是 ``Authorization: Bearer``。没给 key 时不带鉴权头
    （多数网关会回 401，少数内网/匿名入口能直接列出 —— 这两种结果界面都认）。
    """
    return parse_models(
        await _fetch_models_payload(
            base_url, api_key, path=path, auth_header=auth_header, auth_prefix=auth_prefix
        )
    )


async def fetch_provider_model_entries(
    base_url: str,
    api_key: str,
    *,
    path: str = "/models",
    auth_header: str = "Authorization",
    auth_prefix: str = "Bearer ",
) -> list[dict[str, Any]]:
    """同一条路，但**全都要**（带类型与窗口）——「刷新模型清单」写表用这个。"""
    return parse_model_entries(
        await _fetch_models_payload(
            base_url, api_key, path=path, auth_header=auth_header, auth_prefix=auth_prefix
        )
    )


def _models_url(base_url: str, path: str) -> str:
    """清单接口的完整地址：``path`` 写绝对 URL 就用它，否则拼在 ``base_url`` 后面。

    两层都用得上：多数家是 ``base + /models``；**qwen 的清单在另一个根下面**
    （``https://dashscope.aliyuncs.com/api/v1/models``），所以目录行里允许写绝对地址。
    """
    if path.startswith("http"):
        return path
    return base_url.rstrip("/") + "/" + path.lstrip("/")


async def _fetch_models_payload(
    base_url: str,
    api_key: str,
    *,
    path: str,
    auth_header: str,
    auth_prefix: str,
) -> Any:
    """把清单接口的原始 JSON 取回来（URL 与鉴权头的拼法只有这一处）。"""
    url = _models_url(base_url, path)
    headers = {auth_header: f"{auth_prefix}{api_key}"} if api_key and auth_header else {}
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


def parse_models(payload: Any) -> tuple[list[str], int]:
    """把 ``/models`` 的返回掰成 ``(文本模型名清单, 窗口)``。

    形状见 ``_model_items``（OpenAI 兼容的 ``data[]`` 与 qwen 原生的 ``output.models[]`` 都认）。
    窗口是**尽力而为**：openai 那条接口压根不回窗口（实测），读不到就返回 0，界面会让用户自己填。
    **非文本模型在这里滤掉**（图像 / 视频 / 语音 / 向量 / 重排），界面只给文本模型选 ——
    它们仍然会被「刷新模型清单」收进表里（带类型），只是不进这一个下拉。
    """
    names: list[str] = []
    window = 0
    for item in _model_items(payload):
        name = _name_of(item)
        if name:
            names.append(name)
            window = window or _window_of(item)
    return [name for name in names if is_text_model(name)], window


def parse_model_entries(payload: Any) -> list[dict[str, Any]]:
    """同样认那两种形状，但**全都要**：每条带 ``model`` / ``model_type`` / ``context_window``。

    「刷新模型清单」用它 —— 清单要完整（非文本的也留着、标好类型），界面自己决定显示哪一类。
    """
    out: list[dict[str, Any]] = []
    for item in _model_items(payload):
        name = _name_of(item)
        if not name:
            continue
        out.append(
            {
                "model": name,
                "model_type": model_type_of(name),
                "context_window": _window_of(item),
            }
        )
    return out


async def _provider_metas(
    settings: Settings, db: AsyncSession, *, only_enabled: bool = True
) -> tuple[ProviderMeta, ...]:
    """界面上的提供商目录：**现读库**（改库 / 设置页改完 + 刷新即生效，不用重启后端）。

    ``only_enabled=True``（默认）＝ 下拉用的那份（停用的家不出现）；
    ``only_enabled=False`` ＝ 设置页「提供商」那一栏编辑用的**全量**（停用的也要看得到才能改回来）。
    读库失败（表坏了 / 连不上）就退回启动时那一份（``settings.providers``）——
    一个下拉框不该让整页打不开。
    """
    try:
        rows = await dao.list_model_providers(db)
    except Exception:
        logger.exception("读提供商目录失败，退回启动时那份")
        return settings.provider_metas()
    metas = provider_metas_of(rows)
    if only_enabled:
        return sort_provider_metas(metas)
    return tuple(sorted(metas, key=lambda item: (item.ui_group, item.sort_order, item.provider)))


def _row_payload(
    settings: Settings,
    key: str,
    label: str,
    row: ModelEndpoint | None,
    metas: Sequence[ProviderMeta],
) -> ModelConfigRow:
    """一行的对外形状：**库里填了什么**（可能什么都没有）+ **现在真的用谁**（``effective``）。

    2026-10-15 起没有「出厂默认」：行内没配就是空的（``source=unset``），不再回填 ``.env``
    那套值 —— 要回答「现在到底用什么」只有 ``effective`` 一份口径（``llm.effective_for``）。
    """
    from ai_coding_harness.agent.llm import effective_for  # 局部 import：避免装配期循环

    stored = row is not None
    lock = MODEL_AGENT_PROVIDER_LOCK.get(key, "")
    role = MODEL_AGENT_EFFECTIVE_ROLE.get(key, "")
    return ModelConfigRow(
        key=key,
        label=label,
        section=MODEL_AGENT_SECTION.get(key, "agents"),
        roles=list(MODEL_AGENT_ROLES.get(key, ())),
        # 供应商：锁死的行（JEV）就是它；其余按 base_url 反推（纯界面提示，不存库）
        provider=lock or provider_key_for((row.base_url or "") if stored else "", metas),
        provider_locked=bool(lock),
        base_url=(row.base_url or "") if stored else "",
        model=(row.model_name or "") if stored else "",
        api_key_masked=mask_key(row.api_key) if stored else "",
        has_key=bool(stored and row.api_key),
        context_window=int((row.context_window or 0) if stored else 0),
        source="db" if stored else "unset",
        effective=ModelConfigEffective(**effective_for(settings, role)) if role else None,
    )


def _snapshot(
    settings: Settings,
    stored: dict[str, ModelEndpoint],
    metas: tuple[ProviderMeta, ...],
) -> ModelConfigSnapshot:
    rows = [
        _row_payload(settings, key, label, stored.get(key), metas)
        for key, label in MODEL_AGENT_ROWS
    ]
    mode_row = stored.get("intent_mode")
    mode = (mode_row.model_name if mode_row is not None else None) or settings.intent_mode
    return ModelConfigSnapshot(
        rows=rows,
        providers=[
            _option_of(meta) for meta in metas
        ],
        sections=[SectionOption(key=key, label=label) for key, label in MODEL_SECTIONS],
        coding_group=[
            CodingGroupRole(role=role, label=ROLE_LABELS.get(role, role), row=key, reused=reused)
            for role, key, reused in CODING_GROUP
        ],
        intent_mode=mode if mode in ("jev", "llm") else "jev",
        # JEV 这一侧配齐了没有（地址 + key 两样）—— 没配齐时选了 JEV 会落关键字口径（严格二选一）
        jev_configured=bool(settings.typesafe_api_key and settings.typesafe_base_url)
        and settings.intent_jev_enabled,
    )


async def _stored_key(settings: Settings, request: Request, agent: str) -> str:
    """某一行的 key（**现读库**；JEV 那行走 ``typesafe_*`` 那份）。明文永远不回前端。

    读库而不是读内存那份：行里可能只填了地址与 key、还没填模型名 —— 那样的行不在
    ``settings.agent_models`` 里（那份只装「配了模型」的行），但它的 key 照样能用。
    """
    if agent == "jev":
        return settings.typesafe_api_key
    from ai_coding_harness.db import dao

    async with request.app.state.database.session() as db:
        rows = await dao.list_model_endpoints(db)
    row = next((item for item in rows if item.agent == agent), None)
    return (row.api_key or "") if row is not None else ""


def _borrowed_key(settings: Settings, provider: str, metas: Sequence[ProviderMeta]) -> str:
    """刷新模型清单时用的 key —— **借用现有 agent 行的**（用户口径：借用 agent 的就行）。

    谁那一行的 ``base_url`` 落在这家就用谁的；一行都没有（或那几行都没填 key）时再看 ``.env``
    里这家的凭据。两处都借不到就回空串，由调用方给一句人话。
    """
    for row in settings.agent_models.values():
        if row.api_key and provider_key_for(row.base_url or "", metas) == provider:
            return row.api_key
    env_key = str(getattr(settings, f"{provider}_api_key", "") or "")
    return env_key


async def _reload_provider_models(settings: Settings, database: Any) -> None:
    """把表里的模型详情重新读进内存（刷新 / 手改之后都要来一次）。"""
    from ai_coding_harness.db import dao  # 局部 import：避免装配期循环

    async with database.session() as db:
        rows = await dao.list_provider_models(db)
    settings.provider_models = model_metas_of(rows)


async def _models_payload_fresh(
    database: Any, provider: str, **kw: Any
) -> ProviderModelList:
    """给界面用的那份：**现读库**（内存镜像只在刷新/手改/启动时对齐，直接改库它不知道）。"""
    from ai_coding_harness.db import dao

    async with database.session() as db:
        rows = await dao.list_provider_models(db, provider=provider)
    return ProviderModelList(
        provider=provider,
        items=[
            ProviderModelItem(
                provider=row.provider,
                model=row.model,
                model_type=row.model_type,
                context_window=int(row.context_window or 0),
                source=row.source,
                status=row.status,
            )
            for row in rows
        ],
        **kw,
    )


async def _known_provider(settings: Settings, request: Request, provider: str) -> ProviderMeta:
    """这一家的目录行 —— **现读库**（含停用的）。

    为什么不是 ``settings.provider_metas()``：那是**启动时**的快照。用它的话，
    在设置页改了 ``models_path`` / 鉴权头之后，「刷新清单」「测试连接」还会拿着旧值去请求，
    「改完即生效」就成了假的（2026-10-15 修）。停用的家也认得出 —— 停用只影响下拉，
    不影响已经写好的那些行继续刷新自己的清单。
    """
    async with request.app.state.database.session() as db:
        metas = await _provider_metas(settings, db, only_enabled=False)
    meta = next((item for item in metas if item.provider == provider), None)
    if meta is None:
        raise HTTPException(status_code=400, detail=f"认不出的提供商：{provider}")
    return meta


def _option_of(meta: ProviderMeta) -> ProviderOption:
    """``ProviderMeta`` → 界面那一行。目录的对外形状只有这一处拼。"""
    return ProviderOption(
        key=meta.provider,
        label=meta.display_name,
        base_url=meta.default_base_url,
        ui_group=meta.ui_group,
        sort_order=meta.sort_order,
        models_path=meta.models_path,
        auth_header=meta.auth_header,
        auth_prefix=meta.auth_prefix,
        status=meta.status,
        is_dynamic_model_list=meta.is_dynamic_model_list,
    )


async def _all_options(request: Request) -> list[ProviderOption]:
    """目录全量（**含停用**）—— 设置页「提供商」那一栏用它，快照里那份只给下拉。"""
    settings: Settings = request.app.state.settings
    async with request.app.state.database.session() as db:
        metas = await _provider_metas(settings, db, only_enabled=False)
    return [_option_of(meta) for meta in metas]
