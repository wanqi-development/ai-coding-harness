"""业务库引擎与会话工厂（SQLAlchemy async）。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_coding_harness.config import Settings
from ai_coding_harness.db.standalone_schema import repair_standalone_schema

logger = logging.getLogger(__name__)


class Database:
    """持有业务库的异步引擎与会话工厂。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._url = settings.resolved_database_url
        if self._url.startswith("sqlite"):
            settings.standalone_database_path.parent.mkdir(
                parents=True, exist_ok=True
            )
            self._engine = create_async_engine(
                self._url,
                connect_args={"timeout": 5.0},
            )
            event.listen(self._engine.sync_engine, "connect", _sqlite_pragmas)
        else:
            self._engine = create_async_engine(
                self._url,
                pool_pre_ping=True,
                connect_args={
                    "connect_timeout": max(
                        1, settings.postgres_connect_timeout_seconds
                    )
                },
            )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def prepare(self) -> None:
        """standalone 启动前建表并修复旧库；multi 仍由 Alembic 管理。"""
        if not self._settings.is_standalone:
            return
        report = repair_standalone_schema(
            self._settings.standalone_database_path,
            backup_dir=self._settings.standalone_backup_path,
        )
        if report.backup_path is not None:
            logger.warning(
                "standalone SQLite 已备份并修复：backup=%s created=%s columns=%s",
                report.backup_path,
                report.created_tables,
                report.added_columns,
            )
        if not report.plan.healthy:
            detail = "；".join(
                (
                    *report.plan.blockers,
                    *(
                        f"缺表 {name}"
                        for name in report.plan.missing_tables
                    ),
                    *(
                        f"缺列 {fix.table}.{fix.column}"
                        for fix in report.plan.column_fixes
                    ),
                )
            )
            raise RuntimeError(
                f"standalone SQLite schema 修复失败：{detail or '未知结构错误'}"
            )

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


def _sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
    """统一 SQLite 文件的行为；单 writer 不等于没有写锁。"""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()
