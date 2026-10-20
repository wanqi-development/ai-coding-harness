"""模型配置接口：/model-config 下的智能体、提供商与测试连接。"""

from __future__ import annotations

from fastapi import APIRouter

from ai_coding_harness.core.model_types import is_text_model

from .agents import router as agents_router
from .common import _models_url, mask_key, parse_model_entries, parse_models
from .probe import router as probe_router
from .providers import router as providers_router

router = APIRouter()
router.include_router(agents_router)
router.include_router(providers_router)
router.include_router(probe_router)

__all__ = [
    "_models_url",
    "is_text_model",
    "mask_key",
    "parse_model_entries",
    "parse_models",
    "router",
]
