import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# psycopg 异步不支持 Windows 默认的 ProactorEventLoop（仅本地开发需处理）
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 让 alembic 能 import 到 src 布局下的包
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_coding_harness.config import get_settings  # noqa: E402
from ai_coding_harness.db import Base  # noqa: E402
import ai_coding_harness.db.models  # noqa: E402,F401  确保模型注册到 metadata

# Alembic Config 对象
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 连接串来自应用配置（.env）；'%' 需转义以避开 configparser 插值
_url = get_settings().postgres_async_url.replace("%", "%%")
config.set_main_option("sqlalchemy.url", _url)

# autogenerate 目标
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    """只管理本项目定义的表，忽略数据库里其他表（如 LangGraph checkpointer / store）。"""
    managed = target_metadata.tables
    if type_ == "table":
        return name in managed
    table = getattr(obj, "table", None)
    if table is not None:
        return table.name in managed
    return True


def run_migrations_offline() -> None:
    """离线模式：仅用 URL，不建 Engine。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步模式：建 Engine 并绑定连接。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
