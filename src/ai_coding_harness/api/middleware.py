"""接口日志中间件。

用**纯 ASGI 中间件**（不用 `BaseHTTPMiddleware`，后者会影响 SSE 流式）；
为每个请求生成 trace_id，记录 方法 / 路径 / 状态码 / 耗时 / 客户端。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ai_coding_harness.observability.context import (
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)

logger = logging.getLogger(__name__)


class ApiLogMiddleware:
    """记录每个 HTTP 请求，并把 trace_id 注入本次请求的日志上下文。"""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        token = set_trace_id(new_trace_id())
        method = scope.get("method", "-")
        path = scope.get("path", "-")
        client = (scope.get("client") or ("-", 0))[0]
        start = time.perf_counter()
        status = {"code": 0}

        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = message.get("status", 0)
            await send(message)

        logger.info("→ %s %s (client=%s)", method, path, client)
        try:
            await self._app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("请求异常：%s %s", method, path)
            raise
        finally:
            cost_ms = (time.perf_counter() - start) * 1000
            logger.info("← %s %s %s %.0fms", method, path, status["code"], cost_ms)
            reset_trace_id(token)
