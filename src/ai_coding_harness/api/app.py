"""FastAPI application assembly."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_coding_harness.agent.llm import build_fallback, build_model
from ai_coding_harness.api.middleware import ApiLogMiddleware
from ai_coding_harness.api.routes import (
    app_settings,
    auth,
    chat,
    health,
    me,
    model_config,
    repo,
    sessions,
    users,
)
from ai_coding_harness.auth.middleware import AuthMiddleware
from ai_coding_harness.auth.service import AuthService
from ai_coding_harness.auth.user_service import UserAdminService
from ai_coding_harness.config import (
    PROVIDER_SEED_PATH,
    ModelConfig,
    Settings,
    apply_runtime_settings,
    load_provider_seed,
    model_metas_of,
    provider_metas_of,
)
from ai_coding_harness.db import Database
from ai_coding_harness.infra.circuit import RedisCircuitStore
from ai_coding_harness.infra.redis_cache import build_redis, command_guard_seconds
from ai_coding_harness.observability.logging import setup_logging
from ai_coding_harness.observability.tracing import setup_tracing
from ai_coding_harness.server.run_store import RedisRunBuffer, RedisRunLock
from ai_coding_harness.server.runs import RunHub
from ai_coding_harness.server.service import HarnessService
from ai_coding_harness.session.manager import SessionManager
from ai_coding_harness.session.session_cache import build_session_cache
from ai_coding_harness.session.state import build_checkpointer, build_store
from ai_coding_harness.tools.web_search import build_web_search_tool

logger = logging.getLogger(__name__)


async def apply_model_endpoints(settings: Settings, database: Database) -> None:
    """把库里「按智能体」的模型连接读进 Settings。

    读库失败只告警、照常用 `.env` 那套起 —— 配置表坏了不该让整个平台起不来。

    **先清空再落**：只增不清的话，「恢复默认」（删掉库里那一行）在内存里会留下幽灵
    （旧连接照旧生效），而且新写入的那几行也看不出来。所以这里是一份完整刷新。

    ``intent_mode`` 那一行只记「意图识别走 JEV 还是大模型」；其余行按 ``user_id``
    装进各自的 ``user_agent_models``，绝不跨用户回落。``user_id`` 为空的行只保留给
    无用户的老测试夹具。

    **什么时候调**：启动时一次；界面上保存/恢复默认之后**再调一次** —— 主智能体每轮现建
    （``session/manager.py::get_agent``），所以下一轮就用新连接了，不用重启后端（2026-10-14）。
    正在跑的那一轮仍用它自己那份 agent（这一轮不会中途换模型）。
    """
    from ai_coding_harness.db import dao  # 局部 import：装配期用一次，不必进模块头

    try:
        async with database.session() as db:
            rows = await dao.list_model_endpoints(db)
    except Exception:
        logger.exception("读模型连接表失败，本次启动只用 .env 里的模型配置")
        return
    # 读成功才清空：读库失败时保留上一份（清空等于把上一份配置也弄没了）
    settings.agent_models = {}
    settings.user_agent_models = {}
    settings.user_intent_modes = {}
    settings.intent_mode = "jev"
    settings.typesafe_api_key = ""
    settings.typesafe_base_url = ""
    settings.typesafe_model_name = "jev-latest"
    for row in rows:
        user_id = getattr(row, "user_id", None)
        if user_id is None:
            if row.agent == "intent_mode":
                mode = (row.model_name or "").strip().lower()
                if mode in ("jev", "llm"):
                    settings.intent_mode = mode
                continue
            if row.agent == "jev":
                settings.typesafe_api_key = row.api_key or ""
                settings.typesafe_base_url = row.base_url or ""
                settings.typesafe_model_name = (row.model_name or "").strip() or "jev-latest"
                continue
            if not (row.model_name or "").strip():
                continue
            settings.agent_models[row.agent] = ModelConfig(
                model=row.model_name or "",
                api_key=row.api_key or "",
                base_url=row.base_url or "",
                context_window=int(row.context_window or 0),
            )
            continue
        if row.agent == "intent_mode":
            mode = (row.model_name or "").strip().lower()
            if mode in ("jev", "llm"):
                settings.user_intent_modes[int(user_id)] = mode
            continue
        if row.agent != "jev" and not (row.model_name or "").strip():
            continue
        settings.user_agent_models.setdefault(int(user_id), {})[row.agent] = ModelConfig(
            model=(row.model_name or "").strip()
            or ("jev-latest" if row.agent == "jev" else ""),
            api_key=row.api_key or "",
            base_url=row.base_url or "",
            context_window=int(row.context_window or 0),
        )
    logger.info(
        "模型连接已从库里加载：%d 个用户各自一份配置",
        len(settings.user_agent_models),
    )


async def apply_model_providers(settings: Settings, database: Database) -> None:
    """把库里「提供商目录」读进 Settings（**启动时一次**）。

    **表为空才灌种子**（``config/model_providers.toml``）：之后以库为准 —— 用户在库里的改动不该
    被下次启动覆盖回去；要重新对齐种子文件走 ``POST /model-config/providers/reload``。
    读库失败 / 种子也读不到都只告警：界面退回内置那份 ``PROVIDER_OPTIONS``，行为与以前一致。
    """
    from ai_coding_harness.db import dao  # 局部 import：装配期用一次，不必进模块头

    try:
        async with database.session() as db:
            if await dao.count_model_providers(db) == 0:
                seed = load_provider_seed()
                if seed:
                    count = await dao.seed_model_providers(db, seed)
                    logger.info("提供商目录为空，已按 %s 灌入 %d 家", PROVIDER_SEED_PATH, count)
                else:
                    logger.warning(
                        "提供商目录为空，种子文件也读不到（%s）——界面用内置那份", PROVIDER_SEED_PATH
                    )
            rows = await dao.list_model_providers(db)
    except Exception:
        logger.exception("读提供商目录失败，界面用内置那份")
        return
    settings.providers = provider_metas_of(rows)
    logger.info(
        "提供商目录已从库里加载：%d 家（启用 %d）",
        len(settings.providers),
        len(settings.provider_metas()),
    )


async def reload_model_providers(settings: Settings, database: Database) -> int:
    """按种子文件**对齐**目录：同名行整行覆盖、新家插入，表里多出来的行不动。返回对齐了几行。

    这是「改了 ``config/model_providers.toml`` 想让它生效」的唯一入口：启动时只在新库（表为空）灌，
    免得把用户在库里的改动手动改回去。
    """
    from ai_coding_harness.db import dao

    seed = load_provider_seed()
    if not seed:
        raise FileNotFoundError(f"种子文件读不到或为空：{PROVIDER_SEED_PATH}")
    async with database.session() as db:
        await dao.seed_model_providers(db, seed)
        rows = await dao.list_model_providers(db)
    settings.providers = provider_metas_of(rows)
    return len(seed)


async def apply_app_settings(settings: Settings, database: Database) -> None:
    """把库里那几格「运行期配置」落到 Settings 上（**启动时读一次**）。

    运行时改值走 ``PUT /app-settings``（它自己会落，不用重启）。这里只管启动那一下 ——
    读库失败只告警：配置表坏了不该让平台起不来，用 ``.env`` 那套照常跑。
    """
    from ai_coding_harness.db import dao  # 局部 import：装配期用一次，不必进模块头

    try:
        async with database.session() as db:
            stored = await dao.list_app_settings(db)
    except Exception:
        logger.exception("读运行期配置失败，本次启动只用 .env 里的值")
        return
    apply_runtime_settings(settings, stored)
    logger.info("运行期配置已从库里加载：%s", "、".join(sorted(stored)) or "（空，全用 .env）")


async def apply_provider_models(settings: Settings, database: Database) -> None:
    """把「供应商的模型详情」读进 Settings（启动时一次；界面刷新之后也会当场刷）。

    窗口按模型查就查这一份（``Settings.window_for_model``）——它是**内存镜像**，因为
    ``resolve_window`` 是同步函数（中间件与服务都在同步路径上用它）。
    读库失败只告警：查不到窗口就退回家那格与兜底值，不该让平台起不来。
    """
    from ai_coding_harness.db import dao  # 局部 import：装配期用一次，不必进模块头

    try:
        async with database.session() as db:
            rows = await dao.list_provider_models(db)
    except Exception:
        logger.exception("读供应商模型详情失败，本次启动不用它查窗口")
        return
    settings.provider_models = model_metas_of(rows)
    logger.info(
        "供应商模型详情已从库里加载：%d 条（%d 家）",
        len(settings.provider_models),
        len({item.provider for item in settings.provider_models}),
    )


def create_app(settings: Settings) -> FastAPI:
    """构建 FastAPI 应用，应用级共享依赖在 lifespan 中初始化。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(settings)
        setup_tracing(settings)
        app.state.settings = settings
        # 模型按**角色**现取（路由表见 agent/llm.py），不再是启动时建一个大家共用：
        # 主智能体按形态取、子智能体按角色取，两者共用一套角色名。兜底模型同理现取
        # （`None` ＝ 该角色没有兜底）。
        web_search = build_web_search_tool(settings)
        store = await build_store(settings)
        checkpointer = await build_checkpointer(settings)
        database = Database(settings)
        await apply_model_endpoints(settings, database)
        await apply_model_providers(settings, database)
        await apply_provider_models(settings, database)
        await apply_app_settings(settings, database)
        # Redis 是可选件：没配（或配了但连不上且没要求必需）时全部退回进程内实现。
        # 三样旁路状态一起换 —— 只换一半（比如缓冲在 Redis、运行权在进程内）反而更危险：
        # 同一会话会被两个 worker 同时跑。
        redis = await build_redis(settings)
        app.state.redis = redis
        app.state.session_cache = build_session_cache(redis, settings)
        app.state.manager = SessionManager(
            settings=settings,
            model_for=lambda role, user_id: build_model(
                settings, role=role, user_id=user_id
            ),
            fallback_for=lambda role, user_id: build_fallback(
                settings, role=role, user_id=user_id
            ),
            web_search_tool=web_search,
            store=store,
            checkpointer=checkpointer,
            database=database,
            circuit_store=(
                RedisCircuitStore(
                    redis,
                    prefix=settings.redis_prefix,
                    cooldown_seconds=settings.redis_degrade_cooldown_seconds,
                    guard_seconds=command_guard_seconds(settings),
                )
                if redis
                else None
            ),
        )
        app.state.store = store
        app.state.checkpointer = checkpointer
        app.state.database = database
        app.state.auth_service = AuthService(
            settings=settings,
            database=database,
        )
        app.state.user_admin_service = UserAdminService(database=database)
        # JEV 是用户级模型配置，按轮从 user_agent_models 懒建；这里不再放全局实例。
        app.state.intent_jev = None
        app.state.service = HarnessService(
            settings=settings,
            manager=app.state.manager,
            database=database,
            intent_jev=app.state.intent_jev,
        )
        app.state.runs = RunHub(
            app.state.service,
            buffer=(
                RedisRunBuffer(
                    redis,
                    prefix=settings.redis_prefix,
                    ttl_seconds=settings.run_buffer_ttl_seconds,
                    max_frames=settings.run_buffer_max_frames,
                    cooldown_seconds=settings.redis_degrade_cooldown_seconds,
                    guard_seconds=command_guard_seconds(settings),
                )
                if redis
                else None
            ),
            lock=(
                RedisRunLock(
                    redis,
                    prefix=settings.redis_prefix,
                    ttl_seconds=settings.run_lock_ttl_seconds,
                    cooldown_seconds=settings.redis_degrade_cooldown_seconds,
                    guard_seconds=command_guard_seconds(settings),
                )
                if redis
                else None
            ),
            poll_interval=settings.run_poll_interval_seconds,
            renew_interval=max(settings.run_lock_ttl_seconds / 4, 1.0),
        )
        yield
        # 清理：先收掉在跑的轮次（否则它们会在连接池拆掉之后还去写库），再销毁会话。
        await app.state.runs.shutdown()
        await app.state.service.aclose()
        manager: SessionManager = app.state.manager
        await manager.close_local_runtimes()
        await database.dispose()
        await store.conn.close()
        await checkpointer.conn.close()
        if app.state.redis is not None:
            await app.state.redis.aclose()

    app = FastAPI(title="ai-coding-harness", lifespan=lifespan)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(ApiLogMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(me.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(repo.router)
    app.include_router(model_config.router)
    app.include_router(app_settings.router)
    return app
