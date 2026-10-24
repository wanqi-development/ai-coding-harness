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
from ai_coding_harness.safety.redact import mask_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app-settings", tags=["app-settings"])


def snapshot(
    settings: Settings, stored: dict[str, object], *, echo_secrets: bool
) -> RuntimeSettingsSnapshot:
    """当前生效值的全貌：每格都带上「现在这个值是哪来的」——db（界面改过）还是 default。

    ``secret`` 那几格两种口径（2026-10-20 用户口径）：

    * 打开设置页（GET）：回**掩码**，让用户看得出「这里配过一个值」；
    * 保存之后的响应（PUT）：回**空串**，只带 ``has_value`` —— 保存完不再回显。
    """
    items: list[RuntimeSettingItem] = []
    for item in RUNTIME_SETTINGS:
        raw = getattr(settings, item.field, None)
        if item.kind == "secret":
            items.append(
                RuntimeSettingItem(
                    key=item.key,
                    label=item.label,
                    group=item.group,
                    kind=item.kind,
                    value=mask_secret(raw) if echo_secrets else "",
                    has_value=bool(raw),
                    source="db" if item.key in stored else "default",
                )
            )
            continue
        items.append(
            RuntimeSettingItem(
                key=item.key,
                label=item.label,
                group=item.group,
                kind=item.kind,
                value=raw,
                source="db" if item.key in stored else "default",
            )
        )
    return RuntimeSettingsSnapshot(
        items=items
    )


@router.get("", response_model=RuntimeSettingsSnapshot)
async def get_app_settings(request: Request) -> RuntimeSettingsSnapshot:
    """读全部：**顺带把库里的覆盖值重新落一遍**（直接改库之后打开这一页也是新的）。"""
    settings: Settings = request.app.state.settings
    async with request.app.state.database.session() as db:
        stored = await dao.list_app_settings(db)
    apply_runtime_settings(settings, stored)
    return snapshot(settings, stored, echo_secrets=True)


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
            value = coerce_runtime_setting(item, raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{key} 的值不合法：{raw!r}") from exc
        # 「把掩码原样提交回来」＝没改（界面上的掩码只是个提示，不是真值）。
        # 空串照常落库＝清空 —— 用户要清掉时把那一格删空即可。
        if item.kind == "secret" and value and value == mask_secret(
            getattr(settings, item.field, "")
        ):
            continue
        cleaned[key] = value
    if not cleaned:
        async with request.app.state.database.session() as db:
            stored = await dao.list_app_settings(db)
        return snapshot(settings, stored, echo_secrets=False)
    async with request.app.state.database.session() as db:
        for key, value in cleaned.items():
            await dao.upsert_app_setting(db, key=key, value=value)
        stored = await dao.list_app_settings(db)
    apply_runtime_settings(settings, stored)
    logger.info("运行期配置已更新：%s", "、".join(sorted(cleaned)) or "（空）")
    return snapshot(settings, stored, echo_secrets=False)
