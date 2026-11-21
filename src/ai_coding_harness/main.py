"""Application entrypoint for the harness service."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import uvicorn

from ai_coding_harness.api.app import create_app
from ai_coding_harness.config import get_settings


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Windows 上 psycopg 异步要求 SelectorEventLoop。

    uvicorn 0.36+ 在 Windows 默认用 ProactorEventLoop（`asyncio_loop_factory`），
    会忽略事件循环策略，故通过 ``loop="模块:函数"`` 显式指定。
    """
    return asyncio.SelectorEventLoop()


def main() -> None:
    settings = get_settings()
    settings.validate_deployment()
    app = create_app(settings)
    config = uvicorn.Config(app, host=settings.host, port=settings.port)
    server = uvicorn.Server(config)
    shutdown_file = Path(
        os.environ.get("HARNESS_SHUTDOWN_FILE", "")
    ) if os.environ.get("HARNESS_SHUTDOWN_FILE") else None

    async def serve() -> None:
        async def watch_shutdown() -> None:
            if shutdown_file is None:
                return
            while not shutdown_file.exists():
                await asyncio.sleep(0.2)
            runs = getattr(app.state, "runs", None)
            try:
                if runs is not None:
                    await runs.shutdown()
            finally:
                server.should_exit = True

        watcher = asyncio.create_task(watch_shutdown(), name="watch-shutdown")
        try:
            await server.serve()
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    if sys.platform == "win32":
        loop = selector_loop_factory()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(serve())
        finally:
            loop.close()
    else:
        asyncio.run(serve())


if __name__ == "__main__":
    main()
