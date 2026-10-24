"""Chat route — SSE streaming; 业务逻辑在 server 层。

运行本身在 ``RunHub`` 里，HTTP 只是订阅者：请求断了断的是订阅，不是这一轮。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ai_coding_harness.api.schemas import ChatRequest
from ai_coding_harness.api.session_lookup import resolve_session
from ai_coding_harness.server.runs import RunHub, RunInProgress

router = APIRouter(prefix="/chat", tags=["chat"])


def _sse(seq: int, event: dict[str, Any]) -> dict[str, str]:
    """一帧 → 一条 SSE 消息。

    ``id`` 是帧号，客户端拿它当续传游标（重连时回传 ``after``）。让服务端编号而不是让
    客户端自己数：某帧 JSON 解析失败被跳过时，本地计数会错位、重连后重复渲染一段。
    """
    if event.get("type") == "error":
        return {
            "id": str(seq),
            "event": "error",
            "data": json.dumps({"error": event.get("error")}, ensure_ascii=False),
        }
    return {"id": str(seq), "data": json.dumps(event, ensure_ascii=False)}


async def _stream(
    runs: RunHub, session_id: str, after: int
) -> AsyncIterator[dict[str, str]]:
    async for seq, event in runs.subscribe(session_id, after):
        yield _sse(seq, event)


@router.post("/{session_id}")
async def chat(
    session_id: str, req: ChatRequest, request: Request
) -> EventSourceResponse:
    """向会话发送消息，SSE 流式返回。

    这里只解析出会话描述（不建工作区）；工作区在该用的时候由 server 层按需准备，
    这样「消息含凭据被拒收」这类请求不会白建一个容器。

    本轮已在跑时返回 409 —— 运行不再挂在请求上，同一个会话并发两轮会互相踩
    （同一个 thread_id、同一个工作区）。
    """
    session = await resolve_session(request, session_id)
    runs: RunHub = request.app.state.runs
    try:
        await runs.start(
            session_id,
            session,
            req.message,
            clarification_id=req.clarification_id,
            choice=req.choice,
            answers=(
                [answer.model_dump() for answer in req.answers]
                if req.answers
                else None
            ),
        )
    except RunInProgress:
        raise HTTPException(
            status_code=409, detail="本轮仍在进行中，等它结束或刷新页面接回。"
        ) from None
    return EventSourceResponse(_stream(runs, session_id, 0))


@router.get("/{session_id}/stream")
async def stream(
    session_id: str, request: Request, after: int = 0
) -> EventSourceResponse:
    """只订阅、不启动：刷新或断线后带着游标接回本轮正在跑的流。

    本轮已结束（缓冲还在）也能用 —— 拿到的是完整重放，前端据此把这一轮补全。
    """
    await resolve_session(request, session_id)
    runs: RunHub = request.app.state.runs
    return EventSourceResponse(_stream(runs, session_id, after))
