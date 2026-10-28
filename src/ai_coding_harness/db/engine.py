"""业务库引擎与会话工厂（SQLAlchemy async）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_coding_harness.config import Settings


class Database:
    """持有业务库的异步引擎与会话工厂。"""

    def __init__(self, settings: Settings) -> None:
        self._engine = create_async_engine(
            settings.postgres_async_url,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": max(1, settings.postgres_connect_timeout_seconds)
            },
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """事务性会话：正常提交、异常回滚。"""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """关闭引擎（应用退出时调用）。"""
        await self._engine.dispose()
