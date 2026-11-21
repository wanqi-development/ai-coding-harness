"""Standalone entrypoint for a distributed Agent worker process."""

from __future__ import annotations

import asyncio
import logging
import signal
from functools import partial

from ai_coding_harness.api.app import (
    apply_app_settings,
    apply_model_endpoints,
    apply_model_providers,
    apply_provider_models,
    create_app,
)
from ai_coding_harness.config import get_settings
from ai_coding_harness.server.agent_worker import AgentWorker
from ai_coding_harness.server.session_loader import load_worker_session

logger = logging.getLogger(__name__)


async def serve_worker() -> None:
    settings = get_settings()
    settings.validate_deployment()
    if settings.run_execution_mode != "distributed":
        raise RuntimeError("Agent worker 必须配置 RUN_EXECUTION_MODE=distributed")
    app = create_app(settings)
    stop = asyncio.Event()

    def request_stop() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            pass

    async with app.router.lifespan_context(app):
        async def refresh_settings() -> None:
            await apply_model_endpoints(settings, app.state.database)
            await apply_model_providers(settings, app.state.database)
            await apply_provider_models(settings, app.state.database)
            await apply_app_settings(settings, app.state.database)

        worker = AgentWorker(
            settings=settings,
            runs=app.state.runs,
            queue=app.state.run_jobs,
            session_loader=partial(
                load_worker_session,
                settings=settings,
                database=app.state.database,
                manager=app.state.manager,
                cache=app.state.session_cache,
            ),
            refresh_settings=refresh_settings,
        )
        await worker.run(stop)


def main() -> None:
    try:
        asyncio.run(serve_worker())
    except KeyboardInterrupt:
        logger.info("Agent worker 收到 KeyboardInterrupt")


if __name__ == "__main__":
    main()


__all__ = ["main", "serve_worker"]
