"""DAO 共用的小工具。"""

from __future__ import annotations

from datetime import UTC, datetime


def _now() -> datetime:
    return datetime.now(UTC)
