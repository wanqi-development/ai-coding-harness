"""Application entrypoint for the harness service."""

from __future__ import annotations

import asyncio
import sys

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
    loop = "ai_coding_harness.main:selector_loop_factory" if sys.platform == "win32" else "auto"
    uvicorn.run(app, host=settings.host, port=settings.port, loop=loop)


if __name__ == "__main__":
    main()
