"""chat 路由的接口层契约：帧号、409、以及**客户端断开不杀本轮**。

不走 ``httpx.ASGITransport``（它会把响应读完才返回，模拟不了「读到一半断开」），而是自己
造 scope / receive / send 直驱 ASGI —— 断开时机要精确可控，那正是浏览器刷新时发生的事。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import FastAPI

from ai_coding_harness.api.routes import chat as chat_route
from ai_coding_harness.server.runs import RunHub
from tests.run_fakes import GatedService, ScriptedService, StubSession, settle


def _app(service: Any) -> FastAPI:
    """只装 chat 路由的最小应用；会话放进内存注册表，免得 resolve_session 去连库。"""
    app = FastAPI()
    app.include_router(chat_route.router)
    app.state.sessions = {"s-1": StubSession()}
    app.state.runs = RunHub(service)
    return app


class _Client:
    """驱动一次 ASGI 请求。

    ``disconnect`` 决定请求体送到之后 ``receive`` 的走向：等着（正常收流）还是甩一句
    ``http.disconnect``（浏览器刷新）。
    """

    def __init__(self, method: str, path: str, query: str = "", body: bytes = b"",
                 disconnect: bool = False) -> None:
        headers = [(b"host", b"test")]
        if body:
            headers += [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ]
        self.scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": query.encode(),
            "root_path": "", "headers": headers,
            "server": ("test", 80), "client": ("test", 1),
        }
        self._body = body
        self._delivered = False
        self._disconnect = disconnect
        self.sent: list[dict] = []

    async def _receive(self) -> dict:
        if not self._delivered:
            self._delivered = True
            return {"type": "http.request", "body": self._body, "more_body": False}
        if self._disconnect:
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()  # 客户端一直挂着，不发消息
        raise AssertionError("unreachable")

    async def _send(self, message: dict) -> None:
        self.sent.append(message)

    async def run(self, app: FastAPI, timeout: float = 5.0) -> None:
        await asyncio.wait_for(app(self.scope, self._receive, self._send), timeout=timeout)

    @property
    def status(self) -> int:
        return next(m["status"] for m in self.sent if m["type"] == "http.response.start")

    @property
    def events(self) -> list[dict[str, Any]]:
        """SSE 响应拆成事件；注释（ping）与无 data 的块直接跳过。"""
        body = b"".join(
            m.get("body", b"") for m in self.sent if m["type"] == "http.response.body"
        )
        out: list[dict[str, Any]] = []
        for block in re.split(rb"\r?\n\r?\n", body):
            eid: str | None = None
            name = "message"
            data: list[str] = []
            for line in re.split(rb"\r?\n", block):
                if line.startswith(b"id: "):
                    eid = line[4:].decode()
                elif line.startswith(b"event: "):
                    name = line[7:].decode()
                elif line.startswith(b"data: "):
                    data.append(line[6:].decode())
            if data:
                out.append({"id": eid, "event": name, "data": json.loads("\n".join(data))})
        return out


async def test_post_streams_numbered_frames() -> None:
    app = _app(ScriptedService([{"type": "ai", "content": "a"}, {"type": "done"}]))
    client = _Client("POST", "/chat/s-1", body=b'{"message":"hi"}')
    await client.run(app)
    assert client.status == 200
    assert [e["id"] for e in client.events] == ["1", "2"]
    assert [e["data"]["type"] for e in client.events] == ["ai", "done"]


async def test_error_frames_also_carry_an_id() -> None:
    app = _app(ScriptedService([{"type": "error", "error": "炸了"}]))
    client = _Client("POST", "/chat/s-1", body=b'{"message":"hi"}')
    await client.run(app)
    assert client.events == [{"id": "1", "event": "error", "data": {"error": "炸了"}}]


async def test_a_client_disconnect_does_not_kill_the_round() -> None:
    """本轮修的 bug：刷新断开的只是订阅，这一轮必须跑完（成果落库靠它，不是那个连接）。"""
    svc = GatedService([{"type": "ai", "content": c} for c in "abc"])
    app = _app(svc)
    client = _Client("POST", "/chat/s-1", body=b'{"message":"hi"}', disconnect=True)
    await client.run(app)

    assert await app.state.runs.is_running("s-1")
    await svc.advance(3)
    await settle()
    assert not await app.state.runs.is_running("s-1")
    assert [seq async for seq, _ in app.state.runs.subscribe("s-1", 0)] == [1, 2, 3]


async def test_get_stream_replays_from_the_cursor() -> None:
    svc = ScriptedService([{"type": "ai", "content": str(i)} for i in range(3)])
    app = _app(svc)
    await app.state.runs.start("s-1", StubSession(), "hi")
    await settle()

    client = _Client("GET", "/chat/s-1/stream", query="after=2")
    await client.run(app)
    assert [(e["id"], e["data"]["content"]) for e in client.events] == [("3", "2")]


async def test_get_stream_on_an_idle_session_ends_at_once() -> None:
    app = _app(ScriptedService([]))
    client = _Client("GET", "/chat/s-1/stream")
    await client.run(app)
    assert client.status == 200
    assert client.events == []


async def test_a_second_post_while_running_is_rejected_with_409() -> None:
    """前端刷新后绕过禁用态再发一条：后端必须硬拒，否则两轮会踩同一个 thread_id。"""
    svc = GatedService([{"type": "done"}, {"type": "done"}])
    app = _app(svc)
    await app.state.runs.start("s-1", StubSession(), "hi")

    client = _Client("POST", "/chat/s-1", body=b'{"message":"again"}')
    await client.run(app)
    assert client.status == 409
    await svc.advance(2)
