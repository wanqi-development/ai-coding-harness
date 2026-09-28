"""会话描述（``Session``）的解析：内存注册表优先，否则由库里的行组装。

**不碰工作区**：本模块只回答「这个 session_id 是谁」，建沙箱是
``SessionManager.ensure_runtime`` 的事。这样「消息含凭据被拒收」这类请求根本不会
触发建容器（用户删掉的那个「先建沙箱再检查消息」的浪费就在这儿）。
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, Request

from ai_coding_harness.db import dao
from ai_coding_harness.session.manager import Session


async def resolve_session(request: Request, session_id: str) -> Session:
    """取会话描述；不存在 / 已失效 / 缺仓库信息时抛对应的 HTTP 错误。

    内存注册表（``app.state.sessions``）只存本进程创建的会话，后端重启后会空；库里的
    会话仍可用 —— 这里据库里的行组装描述，工作区到真正要用时再按需重连/重建。
    """
    cached: Session | None = request.app.state.sessions.get(session_id)
    if cached is not None:
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
    if row.status != "active":
        raise HTTPException(
            status_code=409, detail=f"会话已失效（{row.status}），请新建会话。"
        )
    if repo is None:
        raise HTTPException(status_code=409, detail="会话缺少仓库信息，请新建会话。")

    session = request.app.state.manager.session_from_row(
        public_id=session_id,
        db_id=row.id,
        repo_url=repo.url,
        owner=repo.owner,
        name=repo.name,
        gitee_token=row.gitee_token,
        gitee_username=row.gitee_username,
        git_platform=row.git_platform,
    )
    request.app.state.sessions[session_id] = session
    return session
