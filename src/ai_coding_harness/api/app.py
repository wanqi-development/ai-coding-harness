"""FastAPI application assembly."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_coding_harness.agent.circuit import RedisCircuitStore
from ai_coding_harness.agent.llm import build_fallback, build_model
from ai_coding_harness.api.middleware import ApiLogMiddleware
from ai_coding_harness.api.routes import auth, chat, health, repo, sessions
from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database
from ai_coding_harness.observability.logging import setup_logging
from ai_coding_harness.observability.tracing import setup_tracing
from ai_coding_harness.redis_cache import build_redis
from ai_coding_harness.server.intent import build_jev_decider
from ai_coding_harness.server.run_store import RedisRunBuffer, RedisRunLock
from ai_coding_harness.server.runs import RunHub
from ai_coding_harness.server.service import HarnessService
from ai_coding_harness.session.manager import Session, SessionManager
from ai_coding_harness.session.state import build_checkpointer, build_store
from ai_coding_harness.tools.web_search import build_web_search_tool


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
        # Redis 是可选件：没配（或配了但连不上且没要求必需）时全部退回进程内实现。
        # 三样旁路状态一起换 —— 只换一半（比如缓冲在 Redis、运行权在进程内）反而更危险：
        # 同一会话会被两个 worker 同时跑。
        redis = await build_redis(settings)
        app.state.redis = redis
        app.state.manager = SessionManager(
            settings=settings,
            model_for=lambda role: build_model(settings, role=role),
            fallback_for=lambda role: build_fallback(settings, role=role),
            web_search_tool=web_search,
            store=store,
            checkpointer=checkpointer,
            database=database,
            circuit_store=(
                RedisCircuitStore(redis, prefix=settings.redis_prefix) if redis else None
            ),
        )
        app.state.sessions: dict[str, Session] = {}
        app.state.store = store
        app.state.checkpointer = checkpointer
        app.state.database = database
        # 意图判定的第二关（JEV）：构造失败/没配 key 时是 None，判定自动退到下一关
        app.state.intent_jev = build_jev_decider(settings)
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
                )
                if redis
                else None
            ),
            lock=(
                RedisRunLock(
                    redis,
                    prefix=settings.redis_prefix,
                    ttl_seconds=settings.run_lock_ttl_seconds,
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
        manager: SessionManager = app.state.manager
        for session in list(app.state.sessions.values()):
            await manager.destroy_session(
                public_id=session.public_id, db_id=session.db_id
            )
        app.state.sessions.clear()
        await database.dispose()
        await store.conn.close()
        await checkpointer.conn.close()
        if app.state.redis is not None:
            await app.state.redis.aclose()
        if app.state.intent_jev is not None:
            await app.state.intent_jev.aclose()

    app = FastAPI(title="ai-coding-harness", lifespan=lifespan)
    app.add_middleware(ApiLogMiddleware)
    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(repo.router)
    return app
