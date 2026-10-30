"""按智能体读写模型连接。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ai_coding_harness.api.schemas import (
    ModelConfigSnapshot,
    ModelConfigUpdate,
)
from ai_coding_harness.config import (
    MODEL_AGENT_ROWS,
    ProviderMeta,
    Settings,
    provider_key_for,
)
from ai_coding_harness.db import dao

logger = logging.getLogger(__name__)

from .common import _provider_metas, _snapshot, request_user_id

router = APIRouter(prefix="/model-config", tags=["model-config"])


def _merged_value(
    fields: dict[str, object],
    current: object | None,
    field: str,
    column: str,
) -> str:
    if field in fields:
        return str(fields[field] or "").strip()
    return str(getattr(current, column, None) or "").strip()


def _same_key_scope(
    left_base_url: str,
    right_base_url: str,
    metas: tuple[ProviderMeta, ...],
) -> bool:
    """Whether two rows point at the same provider endpoint."""
    left = left_base_url.strip().rstrip("/")
    right = right_base_url.strip().rstrip("/")
    if not left or left != right:
        return False
    return provider_key_for(left, metas) == provider_key_for(right, metas)


def _reusable_key_source(
    existing: dict[str, object],
    *,
    target_key: str,
    base_url: str,
    metas: tuple[ProviderMeta, ...],
) -> object | None:
    for agent, source in existing.items():
        if (
            agent != target_key
            and getattr(source, "api_key", "")
            and _same_key_scope(
                getattr(source, "base_url", "") or "",
                base_url,
                metas,
            )
        ):
            return source
    return None


@router.get("", response_model=ModelConfigSnapshot)
async def get_model_config(request: Request) -> ModelConfigSnapshot:
    """当前配置：每行「库里填了什么」（``source`` / ``model`` …）+「现在真的用谁」（``effective``）
    + 意图识别走哪一关。

    ``effective`` 读的是**内存那份** ``Settings.agent_models``（启动时、以及每次保存/恢复
    默认时从库里落进去的，见 ``api/app.py::apply_model_endpoints``）—— 所以**直接改库之后
    要重启后端**才会反映到生效值上（提供商目录不一样：那份是每条请求现读库的）。
    """
    settings: Settings = request.app.state.settings
    user_id = request_user_id(request)
    from ai_coding_harness.api.app import apply_model_endpoints

    await apply_model_endpoints(settings, request.app.state.database)
    async with request.app.state.database.session() as db:
        stored = {
            row.agent: row
            for row in await dao.list_model_endpoints(db, user_id=user_id)
        }
        metas = await _provider_metas(settings, db)
    return _snapshot(settings, stored, metas, user_id)


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
    labels = dict(MODEL_AGENT_ROWS)
    user_id = request_user_id(request)
    async with request.app.state.database.session() as db:
        existing = {
            row.agent: row
            for row in await dao.list_model_endpoints(db, user_id=user_id)
        }
        metas = await _provider_metas(request.app.state.settings, db)
        for key, item in payload.rows.items():
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"unknown agent key: {key}")
            fields = item.model_dump(exclude_unset=True)
            current = existing.get(key)

            values = {
                "model": _merged_value(fields, current, "model", "model_name"),
                "base_url": _merged_value(fields, current, "base_url", "base_url"),
                "api_key": _merged_value(fields, current, "api_key", "api_key"),
            }
            api_key_write = fields.get("api_key")
            api_key_from = str(fields.get("api_key_from") or "").strip()
            source: object | None = None
            if api_key_from:
                if api_key_write not in (None, ""):
                    raise HTTPException(
                        status_code=400,
                        detail="不能同时填写新 api_key 和 api_key_from。",
                    )
                source = existing.get(api_key_from)
                if api_key_from == key or source is None or not getattr(source, "api_key", ""):
                    raise HTTPException(status_code=400, detail="要复用的 api_key 不存在。")
                if not _same_key_scope(
                    getattr(source, "base_url", "") or "",
                    values["base_url"],
                    metas,
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="只能复用同一供应商、同一 base_url 下已保存的 api_key。",
                    )
            elif "api_key" not in fields and not values["api_key"] and values["base_url"]:
                source = _reusable_key_source(
                    existing,
                    target_key=key,
                    base_url=values["base_url"],
                    metas=metas,
                )
            if source is not None:
                values["api_key"] = source.api_key
                api_key_write = source.api_key
            if any(values.values()):
                required = ("base_url", "api_key")
                if key != "jev":
                    required = ("model", *required)
                missing = [
                    name
                    for name in required
                    if not values[name]
                ]
                if missing:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"「{labels.get(key, key)}」这一行配置不完整："
                            f"缺少 {' / '.join(missing)}。要停用这一行请用「清空这一行」。"
                        ),
                    )
            await dao.upsert_model_endpoint(
                db,
                user_id=user_id,
                agent=key,
                base_url=fields.get("base_url"),
                model_name=fields.get("model"),
                api_key=api_key_write,
                context_window=fields.get("context_window"),
            )
        if payload.intent_mode is not None:
            if payload.intent_mode not in ("jev", "llm"):
                raise HTTPException(status_code=400, detail="intent_mode 只能是 jev / llm")
            await dao.upsert_model_endpoint(
                db,
                user_id=user_id,
                agent="intent_mode",
                model_name=payload.intent_mode,
            )
        stored = {
            row.agent: row
            for row in await dao.list_model_endpoints(db, user_id=user_id)
        }
    from ai_coding_harness.api.app import apply_model_endpoints  # 局部 import：避免装配期循环

    await apply_model_endpoints(request.app.state.settings, request.app.state.database)
    if user_id is not None:
        await request.app.state.service.invalidate_model_cache(user_id)
    return _snapshot(request.app.state.settings, stored, metas, user_id)


@router.delete("/{agent}", response_model=ModelConfigSnapshot)
async def delete_model_config(agent: str, request: Request) -> ModelConfigSnapshot:
    """清掉一行的覆盖值 ＝ 恢复出厂默认（``.env`` 那套行为）；同样**当场刷新内存那份**。"""
    allowed = {key for key, _ in MODEL_AGENT_ROWS} | {"intent_mode"}
    if agent not in allowed:
        raise HTTPException(status_code=400, detail=f"unknown agent key: {agent}")
    user_id = request_user_id(request)
    async with request.app.state.database.session() as db:
        await dao.delete_model_endpoint(db, user_id=user_id, agent=agent)
        stored = {
            row.agent: row
            for row in await dao.list_model_endpoints(db, user_id=user_id)
        }
        metas = await _provider_metas(request.app.state.settings, db)
    from ai_coding_harness.api.app import apply_model_endpoints  # 局部 import：避免装配期循环

    await apply_model_endpoints(request.app.state.settings, request.app.state.database)
    if user_id is not None:
        await request.app.state.service.invalidate_model_cache(user_id)
    return _snapshot(request.app.state.settings, stored, metas, user_id)
