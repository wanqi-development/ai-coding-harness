"""运行期配置接口：代码缓存配置 + 上下文窗口配置（2026-10-14）。

三条口径（用户拍过）：

* **改完立刻生效，不用重启**：值存在表 ``app_settings`` 里，读回时直接赋到 ``Settings`` 实例上
  （读那些字段的地方一行都不用改），并配合「主智能体每轮现建、不缓存」
  （``session/manager.py::get_agent``）让窗口/模型这类建实例时读的值也跟着换；
* **只有登记过的键能写**：定义在 ``config.RUNTIME_SETTINGS`` 一处（分组、中文名、类型、落到哪个
  字段），这里只做校验与落库 —— 认不出的键回 400，不给「随便塞一格」的机会；
* **库里没写过那一格就用 `.env` / 代码默认值**（界面上照实回显当前生效值）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ai_coding_harness.api.schemas import (
    RuntimeSettingItem,
    RuntimeSettingsSnapshot,
    RuntimeSettingsUpdate,
)
from ai_coding_harness.config import (
    RUNTIME_SETTING_BY_KEY,
    RUNTIME_SETTINGS,
    Settings,
    apply_runtime_settings,
    coerce_runtime_setting,
)
from ai_coding_harness.db import dao

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app-settings", tags=["app-settings"])


def snapshot(settings: Settings, stored: dict[str, object]) -> RuntimeSettingsSnapshot:
    """当前生效值的全貌：每格都带上「现在这个值是哪来的」——db（界面改过）还是 default。"""
    return RuntimeSettingsSnapshot(
        items=[
            RuntimeSettingItem(
                key=item.key,
                label=item.label,
                group=item.group,
                kind=item.kind,
                value=getattr(settings, item.field, None),
                source="db" if item.key in stored else "default",
            )
            for item in RUNTIME_SETTINGS
        ]
    )


@router.get("", response_model=RuntimeSettingsSnapshot)
async def get_app_settings(request: Request) -> RuntimeSettingsSnapshot:
    """读全部：**顺带把库里的覆盖值重新落一遍**（直接改库之后打开这一页也是新的）。"""
    settings: Settings = request.app.state.settings
    async with request.app.state.database.session() as db:
        stored = await dao.list_app_settings(db)
    apply_runtime_settings(settings, stored)
    return snapshot(settings, stored)


@router.put("", response_model=RuntimeSettingsSnapshot)
async def put_app_settings(
    payload: RuntimeSettingsUpdate, request: Request
) -> RuntimeSettingsSnapshot:
    """保存：只写**给了的**那几格（不传＝不改）。值非法回 400，不落半格。"""
    settings: Settings = request.app.state.settings
    cleaned: dict[str, object] = {}
    for key, raw in payload.items.items():
        item = RUNTIME_SETTING_BY_KEY.get(key)
        if item is None:
            raise HTTPException(status_code=400, detail=f"unknown setting key: {key}")
        try:
            cleaned[key] = coerce_runtime_setting(item, raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{key} 的值不合法：{raw!r}") from exc
    async with request.app.state.database.session() as db:
        for key, value in cleaned.items():
            await dao.upsert_app_setting(db, key=key, value=value)
        stored = await dao.list_app_settings(db)
    apply_runtime_settings(settings, stored)
    logger.info("运行期配置已更新：%s", "、".join(sorted(cleaned)) or "（空）")
    return snapshot(settings, stored)
