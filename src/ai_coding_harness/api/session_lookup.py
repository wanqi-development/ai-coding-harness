"""会话描述（``Session``）的解析：内存注册表优先，否则由库里的行组装。

**不碰工作区**：本模块只回答「这个 session_id 是谁」，建沙箱是
``SessionManager.ensure_runtime`` 的事。这样「消息含凭据被拒收」这类请求根本不会
触发建容器（用户删掉的那个「先建沙箱再检查消息」的浪费就在这儿）。
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, Request

from ai_coding_harness.db import dao
from ai_coding_harness.observability.context import set_session_id
from ai_coding_harness.session.manager import Session
from ai_coding_harness.session.session_cache import SessionSnapshot
from ai_coding_harness.session.user_runtime import load_user_runtime_settings


def current_user_id(request: Request) -> int | None:
    """由全局认证中间件放入请求上下文；旧路由单测可以不带认证夹具。"""
    state = getattr(request, "state", None)
    user = getattr(state, "auth_user", None)
    return int(user.id) if user is not None else None


async def resolve_session(request: Request, session_id: str) -> Session:
    """取会话描述；不存在 / 已失效 / 缺仓库信息时抛对应的 HTTP 错误。

    内存注册表（``app.state.sessions``）只存本进程创建的会话，后端重启后会空；库里的
    会话仍可用 —— 这里据库里的行组装描述，工作区到真正要用时再按需重连/重建。

    **这里也是「会话」这个日志维度的唯一设置点**：每个会话级请求都从这里过，所以它下面
    的所有日志（含 ``/workspace`` 触发的 provisioning）都带上会话 id；``RunHub`` 起的那一轮
    跑在 ``create_task`` 里，创建时会把当前上下文复制过去，于是那一轮的日志也带着它。
    请求各自在自己的 task 里跑，所以这个 set 不会串到别的请求上。
    """
    set_session_id(session_id)
    user_id = current_user_id(request)
    state = request.app.state
    cache = getattr(state, "session_cache", None)
    cached: Session | None = None
    if cache is not None:
        snapshot = await cache.get(session_id)
        if snapshot is not None:
            cached = snapshot.to_session()
    legacy = getattr(state, "sessions", None)
    if cached is None and isinstance(legacy, dict):
        cached = legacy.get(session_id)
    if cached is not None:
        if user_id is not None and cached.user_id != user_id:
            raise HTTPException(status_code=404, detail="session not found")
        if getattr(cached, "status", "active") != "active":
            raise HTTPException(
                status_code=409,
                detail=f"会话已失效（{getattr(cached, 'status', 'unknown')}），请新建会话。",
            )
        if hasattr(state, "settings") and hasattr(state, "database"):
            cached.runtime_settings = await load_user_runtime_settings(
                state.settings,
                state.database,
                cached.user_id,
            )
        return cached

    db = request.app.state.database
    try:
        public_id = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id") from None

    async with db.session() as s:
        found = await dao.get_session_with_repo(s, public_id=public_id)
    if found is None:
        raise HTTPException(status_code=404, detail="session not found")
    row, repo = found
    if user_id is not None and getattr(row, "user_id", None) != user_id:
        raise HTTPException(status_code=404, detail="session not found")
    if row.status != "active":
        raise HTTPException(
            status_code=409, detail=f"会话已失效（{row.status}），请新建会话。"
        )
    if repo is None:
        raise HTTPException(status_code=409, detail="会话缺少仓库信息，请新建会话。")

    runtime_settings = None
    state = request.app.state
    if hasattr(state, "settings") and hasattr(state, "database"):
        runtime_settings = await load_user_runtime_settings(
            state.settings,
            state.database,
            getattr(row, "user_id", None),
        )
    session = request.app.state.manager.session_from_row(
        public_id=session_id,
        db_id=row.id,
        repo_url=repo.url,
        owner=repo.owner,
        name=repo.name,
        gitee_token=row.gitee_token,
        gitee_username=row.gitee_username,
        git_platform=row.git_platform,
        user_id=getattr(row, "user_id", None),
        runtime_settings=runtime_settings,
        status=row.status,
        credential_version=getattr(row, "credential_version", 0),
        session_version=getattr(row, "session_version", 0),
    )
    if cache is not None:
        await cache.put_if_absent(
            SessionSnapshot.from_parts(
                public_id=session.public_id,
                db_id=session.db_id,
                user_id=session.user_id,
                status=session.status,
                repo_url=session.repo_url,
                repo=session.repo,
                credential_version=session.credential_version,
                session_version=session.session_version,
            )
        )
    elif isinstance(legacy, dict):
        legacy[session_id] = session
    return session
