"""LangGraph persistence — async PostgreSQL checkpointer + store."""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool

from ai_coding_harness.config import Settings

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
    pool = AsyncConnectionPool(
        conninfo=conninfo,
        open=False,
        max_idle=_POOL_MAX_IDLE,
        check=AsyncConnectionPool.check_connection,
        kwargs={"autocommit": False, "prepare_threshold": 0},
    )
    await pool.open()
    return pool


async def build_checkpointer(settings: Settings) -> AsyncPostgresSaver:
    """构建并初始化 PostgreSQL checkpointer（会话状态/checkpoint）。"""
    pool = await _open_pool(settings.postgres_url)
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    return saver


async def build_store(settings: Settings) -> AsyncPostgresStore:
    """构建并初始化 PostgreSQL store（/memories/ 长期记忆）。"""
    pool = await _open_pool(settings.postgres_url)
    store = AsyncPostgresStore(pool)
    await store.setup()
    return store
