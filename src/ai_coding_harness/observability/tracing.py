"""Trace 预留（LangSmith / OpenTelemetry）。

当前未接入；等需要链路追踪时，在此补充 LangSmith 或 OTel 的初始化逻辑。
"""

from __future__ import annotations

from ai_coding_harness.config import Settings


def setup_tracing(settings: Settings) -> None:
    """预留：接入 LangSmith / OpenTelemetry 的入口点。"""
    return None
