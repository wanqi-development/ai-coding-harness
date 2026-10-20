"""模型配置接口：按**智能体**读写「用哪个模型」（2026-10-13）。

三条口径（用户拍过）：

* **配置的键是智能体**（qa / analysis / plan / review / coding），不是供应商；
  另加 ``unified``（统一模型）/ ``fallback``（兜底）/ ``classifier``（意图识别的大模型那一关）；
* **留空＝不改**：不预填、不给默认值；哪一行没填就用出厂默认（``.env`` 那套，行为不变）；
* **保存后重启生效**（不热切换，见 ``docs/backlog.md`` 1.1）。

``api_key`` **只回打码值**（``sk-…e2b7``），永远不回明文；写回时留空＝保持原值。

「提供商」那一栏是**目录**（表 ``model_providers``，见 ``config/model_providers.toml``）：它只决定
界面怎么分组、选中带出哪个地址、拉清单时去哪拉/怎么鉴权、拉不到时给什么候选 —— **不决定谁用哪个
模型**（那由上面的智能体轴决定）。清单只在点「测试连接」时拉一次，不轮询、保存时也不拉。

**这一栏是每条请求现读库的**（``_provider_metas``）：直接改库 + 刷新界面就生效，不用重启后端。
它只是选择项元数据、不参与运行时，所以缓存它没有意义；启动时那份（``settings.providers``）只在
读库失败时兜底。种子文件那条路不变：表为空时灌一次，改了文件走 ``/providers/reload`` 对齐。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.api.schemas import (
    CodingGroupRole,
    ModelConfigEffective,
    ModelConfigRow,
    ModelConfigSnapshot,
    ModelConfigUpdate,
    ProviderModelItem,
    ProviderModelList,
    ProviderModelUpsert,
    ProviderOption,
    ProviderProbe,
    ProviderProbeResult,
    ProviderUpsert,
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
from ai_coding_harness.core.model_types import MODEL_TYPES, is_text_model, model_type_of
from ai_coding_harness.db import dao
from ai_coding_harness.db.models import ModelEndpoint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model-config", tags=["model-config"])

#: 探测厂商 ``/models`` 的超时：这只是界面上的一个按钮，不该让请求挂在那儿
_PROBE_TIMEOUT_SECONDS = 10.0

#: ``/models`` 返回里可能装窗口的字段（各家叫法不一，能读到就读，读不到就算了）。
#: 实测：deepseek 用 ``context_window``、glm / kimi 用 ``context_length``、openai 什么都不给。
_WINDOW_KEYS = ("context_length", "max_context_length", "max_input_tokens", "context_window")

#: 模型条目里可能装名字的字段（各家叫法不一；同样**任意层级**里找）
_NAME_KEYS = ("id", "model", "model_name", "name")


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


@router.get("", response_model=ModelConfigSnapshot)
async def get_model_config(request: Request) -> ModelConfigSnapshot:
    """当前配置：每行「库里填了什么」（``source`` / ``model`` …）+「现在真的用谁」（``effective``）
    + 意图识别走哪一关。

    ``effective`` 读的是**内存那份** ``Settings.agent_models``（启动时、以及每次保存/恢复
    默认时从库里落进去的，见 ``api/app.py::apply_model_endpoints``）—— 所以**直接改库之后
    要重启后端**才会反映到生效值上（提供商目录不一样：那份是每条请求现读库的）。
    """
    settings: Settings = request.app.state.settings
    async with request.app.state.database.session() as db:
        stored = {row.agent: row for row in await dao.list_model_endpoints(db)}
        metas = await _provider_metas(settings, db)
    return _snapshot(settings, stored, metas)


@router.put("", response_model=ModelConfigSnapshot)
async def put_model_config(
    payload: ModelConfigUpdate, request: Request
) -> ModelConfigSnapshot:
    """保存：只写**给了的**字段（不传＝不改）。

    ``api_key`` 传空串＝清掉、不传＝保持原值；``model`` 传空串＝这一行退回出厂默认
    （解析层只看「有没有模型名」，没有就等于没配）。

    写完**当场刷新内存里那份**（`apply_model_endpoints`）：主智能体每轮现建，于是**下一轮
    就用新连接**，不用重启后端（2026-10-14 用户口径：配置热生效）。正在跑的那一轮不换。
    """
    allowed = {key for key, _ in MODEL_AGENT_ROWS}
    async with request.app.state.database.session() as db:
        for key, item in payload.rows.items():
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"unknown agent key: {key}")
            fields = item.model_dump(exclude_unset=True)
            await dao.upsert_model_endpoint(
                db,
                agent=key,
                base_url=fields.get("base_url"),
                model_name=fields.get("model"),
                api_key=fields.get("api_key"),
                context_window=fields.get("context_window"),
            )
        if payload.intent_mode is not None:
            if payload.intent_mode not in ("jev", "llm"):
                raise HTTPException(status_code=400, detail="intent_mode 只能是 jev / llm")
            await dao.upsert_model_endpoint(
                db, agent="intent_mode", model_name=payload.intent_mode
            )
        stored = {row.agent: row for row in await dao.list_model_endpoints(db)}
        metas = await _provider_metas(request.app.state.settings, db)
    from ai_coding_harness.api.app import apply_model_endpoints  # 局部 import：避免装配期循环

    await apply_model_endpoints(request.app.state.settings, request.app.state.database)
    return _snapshot(request.app.state.settings, stored, metas)


@router.delete("/{agent}", response_model=ModelConfigSnapshot)
async def delete_model_config(agent: str, request: Request) -> ModelConfigSnapshot:
    """清掉一行的覆盖值 ＝ 恢复出厂默认（``.env`` 那套行为）；同样**当场刷新内存那份**。"""
    allowed = {key for key, _ in MODEL_AGENT_ROWS} | {"intent_mode"}
    if agent not in allowed:
        raise HTTPException(status_code=400, detail=f"unknown agent key: {agent}")
    async with request.app.state.database.session() as db:
        await dao.delete_model_endpoint(db, agent=agent)
        stored = {row.agent: row for row in await dao.list_model_endpoints(db)}
        metas = await _provider_metas(request.app.state.settings, db)
    from ai_coding_harness.api.app import apply_model_endpoints  # 局部 import：避免装配期循环

    await apply_model_endpoints(request.app.state.settings, request.app.state.database)
    return _snapshot(request.app.state.settings, stored, metas)


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


@router.post("/probe", response_model=ProviderProbeResult)
async def probe_provider(payload: ProviderProbe, request: Request) -> ProviderProbeResult:
    """「测试连接」：用刚填的 key/base_url 拉一次那家的模型清单（**只在点这个按钮时拉**）。

    一次调用同时验两件事：**连通性**（key / base_url 对不对）与**模型清单**（给界面做下拉）。
    窗口能读到就一起回（读不到＝0，界面提示让用户填）。

    **能不能测只看两件事：地址有没有、key 有没有**（用户口径）——目录行怎么写都不拦。
    ``is_dynamic_model_list`` 只是「这家官方有没有 /models」的标注，不再决定要不要发请求
    （拉不到本来就是预期结果之一，见下面的退路）。

    结果里用 ``source`` 区分：拉到了 → ``live``（``models`` 就是实时清单）；
    拉不到 / 入口没回清单 → ``none`` 且 ``ok=false`` 带上 ``error`` 原文。

    2026-10-15 起**不再回内置候选**（那份种子清单已删）：模型名以「刷新清单」拉到的那份为准，
    拉不到就老实说拉不到。
    """
    settings: Settings = request.app.state.settings
    api_key = payload.api_key
    if api_key is None and payload.key_from:
        # 界面上没重填 key：用**库里那一行**的（明文永远不出后端）
        api_key = await _stored_key(settings, request, payload.key_from)
    async with request.app.state.database.session() as db:
        # 含停用的：用户正在编辑的那一行可能就是停用的（停用只影响下拉，不影响「测试连接」）
        metas = await _provider_metas(settings, db, only_enabled=False)
    meta = next((item for item in metas if item.provider == payload.provider), None)
    try:
        names, window = await fetch_provider_models(
            payload.base_url,
            api_key or "",
            path=meta.models_path if meta is not None else "/models",
            auth_header=meta.auth_header if meta is not None else "Authorization",
            auth_prefix=meta.auth_prefix if meta is not None else "Bearer ",
        )
    except Exception as exc:  # noqa: BLE001 —— 探测失败是**预期结果**之一，不能 500
        logger.info("探测厂商 /models 失败：%s: %s", type(exc).__name__, exc)
        return ProviderProbeResult(
            ok=False,
            models=[],
            source="none",
            error=f"{type(exc).__name__}: {exc}",
            note="拉不到实时清单：地址 / key / 路径对不对？",
        )
    if not names:
        return ProviderProbeResult(
            ok=False,
            models=[],
            source="none",
            context_window=window,
            error="这个入口没回模型清单",
            note="这个入口没回模型清单（可能需要 key，或清单路径不对）",
        )
    return ProviderProbeResult(
        ok=True,
        models=names[:200],
        source="live",
        context_window=window,
        note="" if window else "这个入口没回窗口，请自己填；留空按 128k 保守值兜",
    )


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


#: 提供商**稳定键**的写法：它对模型行与 agent 行来说都是外键目标，所以要能当键用
_PROVIDER_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


async def _all_options(request: Request) -> list[ProviderOption]:
    """目录全量（**含停用**）—— 设置页「提供商」那一栏用它，快照里那份只给下拉。"""
    settings: Settings = request.app.state.settings
    async with request.app.state.database.session() as db:
        metas = await _provider_metas(settings, db, only_enabled=False)
    return [_option_of(meta) for meta in metas]


@router.get("/providers", response_model=list[ProviderOption])
async def list_providers(request: Request) -> list[ProviderOption]:
    """提供商目录（全量，含停用）—— 设置页「提供商」栏的列表。"""
    return await _all_options(request)


@router.put("/providers/{provider}", response_model=list[ProviderOption])
async def upsert_provider(
    provider: str, payload: ProviderUpsert, request: Request
) -> list[ProviderOption]:
    """写一行提供商目录（**整行覆盖**，2026-10-15 用户口径）。

    键（路径里那个）**不能改** —— 模型行 / agent 行都按它挂；新增一行时由用户填。
    写完**当场刷新内存那份**（``apply_model_providers``）：目录本来就是每条请求现读库的，
    这一步是为了让 ``Settings.providers`` 那份（反推供应商、读库失败时兜底）也跟上，
    于是**不用重启后端**。
    """
    from ai_coding_harness.api.app import apply_model_providers  # 局部 import：避免装配期循环
    from ai_coding_harness.db import dao

    key = provider.strip().lower()
    if not _PROVIDER_KEY_PATTERN.match(key):
        raise HTTPException(
            status_code=400,
            detail="提供商的键只能用小写字母 / 数字 / 下划线 / 连字符（它是模型行与 agent 行的外键目标）",
        )
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="显示名不能为空")
    if payload.status not in (0, 1):
        raise HTTPException(status_code=400, detail="status 只能是 1（启用）/ 0（停用）")
    models_path = payload.models_path.strip()
    if not models_path:
        raise HTTPException(status_code=400, detail="清单路径不能为空（默认 /models）")
    settings: Settings = request.app.state.settings
    async with request.app.state.database.session() as db:
        await dao.upsert_model_provider(
            db,
            provider=key,
            display_name=label,
            default_base_url=payload.base_url.strip(),
            ui_group=payload.ui_group.strip(),
            sort_order=int(payload.sort_order),
            status=int(payload.status),
            is_dynamic_model_list=bool(payload.is_dynamic_model_list),
            models_path=models_path,
            auth_header=payload.auth_header.strip(),
            auth_prefix=payload.auth_prefix,
        )
    await apply_model_providers(settings, request.app.state.database)
    logger.info("提供商目录已改写：%s（%s）", key, label)
    return await _all_options(request)


@router.get("/providers/{provider}/models", response_model=ProviderModelList)
async def list_provider_models(provider: str, request: Request) -> ProviderModelList:
    """一家的模型清单（弹窗里的候选与窗口回显读它）—— **现读库**。"""
    settings: Settings = request.app.state.settings
    await _known_provider(settings, request, provider)
    return await _models_payload_fresh(request.app.state.database, provider)


@router.post("/providers/{provider}/models/refresh", response_model=ProviderModelList)
async def refresh_provider_models(
    provider: str, request: Request, base_url: str = ""
) -> ProviderModelList:
    """**点按钮才刷**：拿这家的地址去调它的清单接口，结果逐条写进模型详情表。

    用户口径（2026-10-14）：界面上给刷新按钮，**不主动刷新**（不轮询、保存时也不刷）。
    非文本模型**也落表**并标类型（``model_types.model_type_of``），界面上只显示 ``chat``；
    key 借用现有 agent 行的（见 ``_borrowed_key``）。

    ``base_url``（可选）是**这一行手填的地址**：界面上那一行如果是代理 / 自建网关，目录里的
    默认地址就拉不到东西（而「测试连接」用的正是你填的地址）—— 两处口径必须一致。
    不传才退回目录里的默认地址。
    """
    from ai_coding_harness.db import dao  # 局部 import：装配期之外才用

    settings: Settings = request.app.state.settings
    meta = await _known_provider(settings, request, provider)
    target = (base_url or meta.default_base_url or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail=f"{provider} 还没配地址，先在弹窗里填 base_url")
    # key 与「哪家」的反推都读**现读库**那份：设置页刚改完目录就用新值，不用重启
    async with request.app.state.database.session() as db:
        fresh_metas = await _provider_metas(settings, db, only_enabled=False)
    api_key = _borrowed_key(settings, provider, fresh_metas)
    try:
        entries = await fetch_provider_model_entries(
            target,
            api_key,
            path=meta.models_path,
            auth_header=meta.auth_header,
            auth_prefix=meta.auth_prefix,
        )
    except Exception as exc:  # 拉不到是预期结果之一，不是 500（回 409 并带上原因）
        logger.info("刷新模型清单失败：%s: %s", provider, exc)
        raise HTTPException(status_code=409, detail=f"拉不到清单：{type(exc).__name__}: {exc}") from exc
    if not entries:
        raise HTTPException(status_code=409, detail="这个入口没回模型清单（可能需要 key，或路径不对）")
    async with request.app.state.database.session() as db:
        for entry in entries:
            await dao.upsert_provider_model(
                db,
                provider=provider,
                model=entry["model"],
                model_type=entry["model_type"],
                context_window=int(entry["context_window"]),
                source="probe",
            )
    await _reload_provider_models(settings, request.app.state.database)
    logger.info("模型清单已刷新：%s +%d 条", provider, len(entries))
    windows = sum(1 for entry in entries if entry["context_window"])
    return await _models_payload_fresh(
        request.app.state.database,
        provider,
        refreshed=len(entries),
        note="" if windows else "这个入口没回窗口，需要的话手填",
    )


@router.put("/providers/{provider}/models", response_model=ProviderModelList)
async def upsert_provider_model(
    provider: str, payload: ProviderModelUpsert, request: Request
) -> ProviderModelList:
    """手填 / 手改一个模型（窗口 / 类型 / 启停）—— ``source`` 记成 ``manual``。"""
    from ai_coding_harness.db import dao

    settings: Settings = request.app.state.settings
    await _known_provider(settings, request, provider)
    model = payload.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="模型名不能为空")
    if payload.model_type is not None and payload.model_type not in MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"类型只能是：{'/'.join(MODEL_TYPES)}")
    async with request.app.state.database.session() as db:
        await dao.upsert_provider_model(
            db,
            provider=provider,
            model=model,
            model_type=payload.model_type or model_type_of(model),
            context_window=int(payload.context_window or 0),
            source="manual",
            status=1 if payload.status is None else int(payload.status),
        )
    await _reload_provider_models(settings, request.app.state.database)
    return await _models_payload_fresh(request.app.state.database, provider)


@router.post("/providers/reload", response_model=ModelConfigSnapshot)
async def reload_providers(request: Request) -> ModelConfigSnapshot:
    """按 ``config/model_providers.toml`` **对齐**提供商目录，然后把快照回给界面。

    启动时只在新库（表为空）灌一次 —— 改了种子文件、库又不为空时走这个接口（同名行整行覆盖，
    表里多出来的行不动）。**改完要重启后端**才反映到正在跑的装配上（与模型连接同口径）。
    """
    from ai_coding_harness.api.app import reload_model_providers  # 局部 import：避免装配期循环

    settings: Settings = request.app.state.settings
    try:
        count = await reload_model_providers(settings, request.app.state.database)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("提供商目录已按种子文件对齐：%d 家", count)
    # 种子对齐也会把那串名字灌进模型详情表，内存里那份跟着刷一遍
    await _reload_provider_models(settings, request.app.state.database)
    async with request.app.state.database.session() as db:
        stored = {row.agent: row for row in await dao.list_model_endpoints(db)}
        metas = await _provider_metas(settings, db)
    return _snapshot(settings, stored, metas)
