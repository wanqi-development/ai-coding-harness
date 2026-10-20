"""提供商目录、模型清单与探测结果。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ai_coding_harness.api.schemas import (
    ModelConfigSnapshot,
    ProviderModelList,
    ProviderModelUpsert,
    ProviderOption,
    ProviderUpsert,
)
from ai_coding_harness.config import (
    Settings,
)
from ai_coding_harness.core.model_types import MODEL_TYPES, model_type_of
from ai_coding_harness.db import dao

logger = logging.getLogger(__name__)

from .common import (
    _PROVIDER_KEY_PATTERN,
    _all_options,
    _borrowed_key,
    _known_provider,
    _models_payload_fresh,
    _provider_metas,
    _reload_provider_models,
    _snapshot,
    fetch_provider_model_entries,
)

router = APIRouter(prefix="/model-config", tags=["model-config"])


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
