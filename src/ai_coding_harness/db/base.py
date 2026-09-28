"""SQLAlchemy 声明式基类。"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有业务表模型的基类（Alembic 以 ``Base.metadata`` 为迁移目标）。"""
