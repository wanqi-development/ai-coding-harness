"""Session lifecycle routes."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request

from ai_coding_harness.api.schemas import (
    CreateSessionRequest,
    PlanInfo,
    RecordItem,
    SessionDetail,
    SessionListItem,
    SessionResponse,
)
from ai_coding_harness.api.session_lookup import resolve_session
from ai_coding_harness.db import dao
from ai_coding_harness.server.service import WORKSPACE_ERROR_MESSAGE, plan_payload
from ai_coding_harness.session.manager import (
    WORKSPACE_READY,
    WorkspaceError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse)
async def create_session(
    req: CreateSessionRequest, request: Request
) -> SessionResponse:
    """创建一次会话（只落库，绑定 Gitee / GitHub 仓库）。

    同一仓库已有活跃会话时**复用**它：不新建、也不踢工作区（它可能早就是 ready），回执里
    的 ``reused`` 让前端直接落到既有会话上 —— 一个仓库同时只该有一份工作区，否则两个会话
    各占一个沙箱，各改各的代码。

    平台由 ``repo_url`` 判定（gitee.com / github.com），令牌按平台取（``gitee_token`` /
    ``github_token``），都没有就回落 .env 里该平台配置的那份。

    代码工作区在后台建（~10s），接口立即返回，前端按 ``workspace_status`` 显示进度，
    打开会话时再 ``POST /sessions/{id}/workspace`` 等它就绪。
    """
    manager = request.app.state.manager
    created = await manager.create_session(
        req.repo_url, req.gitee_token, req.github_token
    )
    request.app.state.sessions[created.session.public_id] = created.session
    if not created.reused:
        manager.start_provisioning(created.session)
    return SessionResponse(
        session_id=created.session.public_id,
        workspace_status=created.workspace_status,
        reused=created.reused,
    )


@router.get("", response_model=list[SessionListItem])
async def list_sessions(request: Request, limit: int = 50) -> list[SessionListItem]:
    """会话列表（供前端历史侧栏）。"""
    db = request.app.state.database
    async with db.session() as s:
        rows = await dao.list_sessions_with_repo(s, limit=limit)
    return [
        SessionListItem(
            session_id=str(sess.public_id),
            title=sess.title,
            owner=(repo.owner if repo else None),
            repo=(repo.name if repo else None),
            repo_url=(repo.url if repo else None),
            status=sess.status,
            created_at=sess.created_at,
        )
        for sess, repo in rows
    ]


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session_detail(session_id: str, request: Request) -> SessionDetail:
    """会话详情（含代码工作区状态）；前端轮询它显示「准备中 / 异常」。

    只读状态、**不触发**建工作区 —— 建是 ``POST /sessions/{id}/workspace`` 的事，
    把两者混在一起会让轮询变成「每隔 1.5 秒踢一次沙箱」。
    """
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
    return SessionDetail(
        session_id=str(row.public_id),
        title=row.title,
        owner=(repo.owner if repo else None),
        repo=(repo.name if repo else None),
        repo_url=(repo.url if repo else None),
        status=row.status,
        workspace_status=row.workspace_status,
        running=await request.app.state.runs.is_running(session_id),
        created_at=row.created_at,
    )


@router.post("/{session_id}/workspace", response_model=SessionResponse)
async def ensure_workspace(session_id: str, request: Request) -> SessionResponse:
    """准备代码工作区并**等它就绪**；幂等，已就绪直接返回。

    用户打开会话时调用。失败返回 409 与固定措辞（原始异常只进日志与库），前端据此
    提示「请重试」——重试就是再调一次本接口。
    """
    session = await resolve_session(request, session_id)
    try:
        await request.app.state.manager.ensure_runtime(session)
    except WorkspaceError as exc:
        # 固定措辞：原始异常可能夹带沙箱侧输出，只进日志与 sessions.workspace_error
        raise HTTPException(
            status_code=409, detail=WORKSPACE_ERROR_MESSAGE
        ) from exc
    return SessionResponse(session_id=session_id, workspace_status=WORKSPACE_READY)


@router.get("/{session_id}/records", response_model=list[RecordItem])
async def get_records(
    session_id: str, request: Request, limit: int = 200
) -> list[RecordItem]:
    """会话时间线（记录 + 详情），供前端展示。"""
    db = request.app.state.database
    try:
        public_id = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id") from None
    async with db.session() as s:
        sess = await dao.get_session_by_public_id(s, public_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        rows = await dao.list_records_with_detail(s, session_id=sess.id, limit=limit)
    return [
        RecordItem(
            id=rec.id,
            type=rec.type,
            summary=rec.summary,
            content=(det.content if det else None),
            extra=(det.extra if det else None),
            created_at=rec.created_at,
        )
        for rec, det in rows
    ]


@router.get("/{session_id}/plan", response_model=PlanInfo | None)
async def get_plan(session_id: str, request: Request) -> PlanInfo | None:
    """该会话最新方案的快照；没有方案返回 null。

    给「打开历史会话 / 刷新页面」还原进度面板用 —— SSE 的 plan 事件只在对话进行中推。
    """
    db = request.app.state.database
    try:
        public_id = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id") from None
    async with db.session() as s:
        sess = await dao.get_session_by_public_id(s, public_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        plan = await dao.get_latest_plan(s, session_id=sess.id)
    return None if plan is None else PlanInfo(**plan_payload(plan))


@router.post("/{session_id}/cancel")
async def cancel_run(session_id: str, request: Request) -> dict[str, bool]:
    """终止该会话正在跑的那一轮（前端「终止」按钮）。

    终止的是「那一轮运行」：本进程在跑的立刻 cancel；挂在别的 worker 上的留终止标记，
    由那边自己取消（配了 Redis 才有多 worker，见 ``server/run_store.py``）。已挂着的
    订阅者随之看到流结束、不再产帧。**不查库、不改会话状态** —— 会话与历史都留着
    （要拆会话走 ``DELETE``），所以没在跑就回 ``{"cancelled": false}``，重按一次也不算错误。
    """
    return {"cancelled": await request.app.state.runs.cancel(session_id)}


@router.delete("/{session_id}")
async def destroy_session(session_id: str, request: Request) -> dict[str, bool]:
    """销毁会话：软取消在飞的构建、终止代码工作区、库里标记 destroyed。

    会话不在内存（后端重启过）时也能删干净 —— 管理器据库里记的 sandbox_id 终止容器，
    不再像以前那样只改个状态、把容器漏在 OpenSandbox 侧。
    """
    manager = request.app.state.manager
    request.app.state.sessions.pop(session_id, None)
    # 会话要拆了，正在跑的那一轮留着只会对着废掉的工作区写库
    await request.app.state.runs.cancel(session_id)

    db = request.app.state.database
    try:
        public_id = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id") from None
    async with db.session() as s:
        row = await dao.get_session_by_public_id(s, public_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    await manager.destroy_session(public_id=session_id, db_id=row.id)
    return {"ok": True}
