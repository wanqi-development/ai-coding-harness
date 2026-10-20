"""「测试连接」：用某一行借来的 key 去拉一次模型清单。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ai_coding_harness.api.schemas import (
    ProviderProbe,
    ProviderProbeResult,
)
from ai_coding_harness.config import (
    Settings,
)

logger = logging.getLogger(__name__)

from .common import _provider_metas, _stored_key, fetch_provider_models

router = APIRouter(prefix="/model-config", tags=["model-config"])


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
