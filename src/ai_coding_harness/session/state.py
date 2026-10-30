"""LangGraph persistence — PostgreSQL in multi mode, one SQLite file in standalone."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from ai_coding_harness.config import Settings

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

_SQLITE_CONNECTIONS: dict[str, Any] = {}

# 连接空闲超过该秒数即回收。库经 SSH 隧道访问，隧道抖动后池里的连接会「开着但已死」，
# 定期回收能减少取到这类连接的窗口。
_POOL_MAX_IDLE = 300.0


async def _open_pool(conninfo: str) -> AsyncConnectionPool:
    """打开一个 psycopg 异步连接池。

    表已由 setup() 建好，常规读写走事务（``autocommit=False``）。
    ``prepare_threshold=0`` 避免连接池下 prepared statement 复用的坑。

    ``check`` 在每次取用前验活（打一个真实往返）：没有它时，隧道断连后的死连接会被
    原样交给调用方，首个查询即抛 ``OperationalError``，把整轮运行打断。

    注意：若将来 langgraph 升级新增含 ``CREATE INDEX CONCURRENTLY`` 的迁移，
    ``setup()`` 在非 autocommit 下会失败，届时需临时用 autocommit 连接跑迁移。
    """
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=conninfo,
        open=False,
        max_idle=_POOL_MAX_IDLE,
        check=AsyncConnectionPool.check_connection,
        kwargs={"autocommit": False, "prepare_threshold": 0},
    )
    await pool.open()
    return pool


async def _sqlite_connection(path: Path) -> Any:
    import aiosqlite

    key = str(path.expanduser().resolve())
    existing = _SQLITE_CONNECTIONS.get(key)
    if existing is not None:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(key, check_same_thread=False)
    await connection.execute("PRAGMA journal_mode=WAL")
    await connection.execute("PRAGMA synchronous=NORMAL")
    await connection.execute("PRAGMA busy_timeout=5000")
    _SQLITE_CONNECTIONS[key] = connection
    return connection


async def build_checkpointer(settings: Settings) -> BaseCheckpointSaver:
    """Build checkpoint persistence for the active deployment mode."""
    if settings.is_standalone:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        connection = await _sqlite_connection(settings.standalone_database_path)
        saver = AsyncSqliteSaver(connection)
        await saver.setup()
        saver._harness_closeable = connection  # type: ignore[attr-defined]
        saver._harness_sqlite_path = str(  # type: ignore[attr-defined]
            settings.standalone_database_path.expanduser().resolve()
        )
        return saver

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    pool = await _open_pool(settings.postgres_url)
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    saver._harness_closeable = pool  # type: ignore[attr-defined]
    return saver


async def build_store(settings: Settings) -> BaseStore:
    """Build long-term Store persistence for the active deployment mode."""
    if settings.is_standalone:
        from langgraph.store.sqlite.aio import AsyncSqliteStore

        connection = await _sqlite_connection(settings.standalone_database_path)
        store = AsyncSqliteStore(connection)
        await store.setup()
        store._harness_closeable = connection  # type: ignore[attr-defined]
        store._harness_sqlite_path = str(  # type: ignore[attr-defined]
            settings.standalone_database_path.expanduser().resolve()
        )
        return store

    from langgraph.store.postgres.aio import AsyncPostgresStore

    pool = await _open_pool(settings.postgres_url)
    store = AsyncPostgresStore(pool)
    await store.setup()
    store._harness_closeable = pool  # type: ignore[attr-defined]
    return store


async def close_persistence(*objects: BaseCheckpointSaver | BaseStore) -> None:
    """Close SQLite connections / PostgreSQL pools tied to persistence objects."""
    seen: set[int] = set()
    for item in objects:
        closeable = getattr(item, "_harness_closeable", None)
        if closeable is None or id(closeable) in seen:
            continue
        seen.add(id(closeable))
        closer = getattr(closeable, "close", None)
        if closer is None:
            continue
        sqlite_path = getattr(item, "_harness_sqlite_path", None)
        if sqlite_path is not None:
            _SQLITE_CONNECTIONS.pop(str(sqlite_path), None)
        result = closer()
        if hasattr(result, "__await__"):
            await result
