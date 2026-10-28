"""Session lifecycle routes."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ai_coding_harness.api.schemas import (
    ClarificationInfo,
    CreateSessionRequest,
    LogLineItem,
    LogPage,
    MemorySnapshot,
    PlanInfo,
    PullItem,
    PullList,
    RecordItem,
    RecordPage,
    SessionDetail,
    SessionListItem,
    SessionResponse,
    UsageActor,
    UsageSnapshot,
)
from ai_coding_harness.api.session_lookup import current_user_id, resolve_session
from ai_coding_harness.backends.composite import memory_path, read_memory
from ai_coding_harness.db import dao
from ai_coding_harness.observability.log_tail import read_session_logs
from ai_coding_harness.observability.run_log import account_key
from ai_coding_harness.safety.redact import redact, secret_terms
from ai_coding_harness.server.service import WORKSPACE_ERROR_MESSAGE, plan_payload
from ai_coding_harness.session.manager import WORKSPACE_READY, Session, WorkspaceError
from ai_coding_harness.session.session_cache import SessionSnapshot
from ai_coding_harness.tools.git import build_git_client
from ai_coding_harness.tools.pr_client import PULL_OPEN, PrLookupError, pull_summary
from ai_coding_harness.tools.repo_access import RepoAccessError

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

    平台由 ``repo_url`` 判定（gitee.com / github.com），令牌取该平台在设置页
    「代码平台」那一栏配的那份（``.env`` 的同名键只是初始值）。

    代码工作区在后台建（~10s），接口立即返回，前端按 ``workspace_status`` 显示进度，
    打开会话时再 ``POST /sessions/{id}/workspace`` 等它就绪。
    """
    manager = request.app.state.manager
    try:
        created = await manager.create_session(
            req.repo_url,
            user_id=current_user_id(request),
        )
    except RepoAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    cache = getattr(request.app.state, "session_cache", None)
    if cache is not None:
        session = created.session
        await cache.put(
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
    else:
        legacy = getattr(request.app.state, "sessions", None)
        if isinstance(legacy, dict):
            legacy[created.session.public_id] = created.session
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
        rows = await dao.list_sessions_with_repo(
            s, limit=limit, user_id=current_user_id(request)
        )
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
    user_id = current_user_id(request)
    if user_id is not None and getattr(row, "user_id", None) != user_id:
        raise HTTPException(status_code=404, detail="session not found")
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


@router.get("/{session_id}/records", response_model=RecordPage)
async def get_records(
    session_id: str,
    request: Request,
    limit: int = 20,
    before_id: int | None = None,
) -> RecordPage:
    """会话时间线的一页（默认最近 20 条），``before_id`` 向前翻。"""
    session = await resolve_session(request, session_id)
    db = request.app.state.database
    async with db.session() as s:
        rows, has_more, next_before_id = await dao.list_records_page(
            s,
            session_id=session.db_id,
            before_id=before_id,
            limit=max(1, min(limit, 100)),
        )
    return RecordPage(
        items=[
            RecordItem(
                id=rec.id,
                type=rec.type,
                summary=rec.summary,
                content=(det.content if det else None),
                extra=(det.extra if det else None),
                usage=_usage_detail(det),
                created_at=rec.created_at,
            )
            for rec, det in rows
        ],
        has_more=has_more,
        next_before_id=next_before_id,
    )


def _usage_detail(detail: Any) -> dict[str, Any] | None:
    """从记录详情里取出那一轮的 token 账；没有 / 形状不对就返回 ``None``。

    单独提出来是因为它是**唯一**一个前端要按字段读的 ``extra`` 子结构（其余 extra 都是
    各记录类型自己的私事）。形状不对**不报错**：老记录、脏数据都只该让角标不显示。

    主角名顺手归一次（与聚合那条路同一个 ``account_key``）：本功能刚上线的那几轮把主控
    记在了真名下，不归的话消息角标展开后是个英文名，与面板里那行「主控」对不上。
    """
    extra = getattr(detail, "extra", None) if detail is not None else None
    usage = extra.get("usage") if isinstance(extra, dict) else None
    if not isinstance(usage, dict):
        return None
    by_actor = usage.get("by_actor")
    if not isinstance(by_actor, dict):
        return usage
    return {
        **usage,
        "by_actor": {account_key(str(key)): item for key, item in by_actor.items()},
    }


@router.get("/{session_id}/plan", response_model=PlanInfo | None)
async def get_plan(session_id: str, request: Request) -> PlanInfo | None:
    """该会话最新方案的快照；没有方案返回 null。

    给「打开历史会话 / 刷新页面」还原进度面板用 —— SSE 的 plan 事件只在对话进行中推。
    """
    session = await resolve_session(request, session_id)
    db = request.app.state.database
    async with db.session() as s:
        plan = await dao.get_latest_plan(s, session_id=session.db_id)
    return None if plan is None else PlanInfo(**plan_payload(plan))


@router.get("/{session_id}/clarification", response_model=ClarificationInfo | None)
async def get_pending_clarification(
    session_id: str, request: Request
) -> ClarificationInfo | None:
    """当前待回答的意图澄清；刷新页面后前端据此恢复弹窗。"""
    session = await resolve_session(request, session_id)
    data = await request.app.state.runs.pending_clarification(session)
    return None if data is None else ClarificationInfo(**data)


def _pull_number(text: str) -> int:
    """编号转数字用于排序；转不出来的当 0（平台理论上只给数字，这里不冒抛异常的险）。"""
    try:
        return int(text)
    except ValueError:
        return 0


def _usage_int(value: Any) -> int:
    """从库里那份 usage 里取一个计数：认不出的当 0。

    只读展示路径，**不冒抛异常的险** —— 一条脏记录不该把整个面板打挂。
    """
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _aggregate_usage(rows: Iterable[Any]) -> UsageSnapshot:
    """把各轮记录里的 ``extra["usage"]`` 汇总成一份会话级快照。

    ``rows`` 是 ``dao.list_records_with_detail`` 的 ``(记录, 详情)`` 二元组序列。
    形状不对的（老数据、别的记录类型、``extra`` 不是 dict）一律跳过，
    但**计入 rounds**：界面要据此说「有 N 轮没有用量数据」。

    主角名走 ``observability.run_log.account_key``：写账与读账共用一个归类规则，
    本功能刚上线的那几轮把主控记在真名下，这里顺手归一次（否则界面上会冒出个英文名）。

    ``by_form`` 是**主控按形态**拆出来的一份（2026-10-20 用户口径）：主控在事件流里只有
    一个名字，不拆的话 qa / analysis / plan / review / coding 全压在同一行里。形态取自
    每轮记录的 ``extra.intent``；子智能体不进这一格（它们本来就是独立的执行主角）。
    """
    per_actor: dict[str, list[int]] = {}
    per_form: dict[str, list[int]] = {}
    totals = [0, 0, 0]
    missing = 0
    rounds = 0
    with_usage = 0
    interrupted = 0
    for _row, detail in rows:
        extra = getattr(detail, "extra", None) if detail is not None else None
        if getattr(_row, "type", "") != "assistant":
            continue
        rounds += 1
        usage = extra.get("usage") if isinstance(extra, dict) else None
        if not isinstance(usage, dict):
            continue
        with_usage += 1
        if usage.get("interrupted"):
            interrupted += 1
        missing += _usage_int(usage.get("missing"))
        round_total = usage.get("totals") if isinstance(usage.get("totals"), dict) else {}
        totals[0] += _usage_int(round_total.get("input"))
        totals[1] += _usage_int(round_total.get("output"))
        by_actor = usage.get("by_actor")
        if not isinstance(by_actor, dict):
            continue
        intent = str(extra.get("intent") or "").strip()
        for key, item in by_actor.items():
            if not isinstance(item, dict):
                continue
            actor = account_key(str(key))
            lines = per_actor.setdefault(actor, [0, 0, 0])
            lines[0] += _usage_int(item.get("input"))
            lines[1] += _usage_int(item.get("output"))
            lines[2] += _usage_int(item.get("calls"))
            totals[2] += _usage_int(item.get("calls"))
            if actor == "main" and intent:
                form = per_form.setdefault(intent, [0, 0, 0])
                form[0] += _usage_int(item.get("input"))
                form[1] += _usage_int(item.get("output"))
                form[2] += _usage_int(item.get("calls"))

    actors = [
        UsageActor(
            key=key,
            input=lines[0],
            output=lines[1],
            calls=lines[2],
            total=lines[0] + lines[1],
        )
        for key, lines in per_actor.items()
        # 花得多的排前面：面板第一眼要看到「钱花在哪」，与 PR 面板「未合并排最前」同一考虑
    ]
    actors.sort(key=lambda item: (-item.total, item.key))
    forms = {
        key: UsageActor(
            key=key,
            input=lines[0],
            output=lines[1],
            calls=lines[2],
            total=lines[0] + lines[1],
        )
        for key, lines in per_form.items()
    }
    return UsageSnapshot(
        totals=UsageActor(
            key="total",
            input=totals[0],
            output=totals[1],
            calls=totals[2],
            total=totals[0] + totals[1],
        ),
        actors=actors,
        by_form=forms,
        rounds=rounds,
        rounds_with_usage=with_usage,
        rounds_interrupted=interrupted,
        missing=missing,
    )


def _log_paths(request: Request) -> list[Path]:
    """要看/要下载的日志文件。顺序固定（先接口后运行时），合并时间线时由时间戳决定先后。"""
    settings = request.app.state.settings
    log_dir = Path(settings.log_dir)
    return [log_dir / settings.log_api_file, log_dir / settings.log_agent_file]


def _redaction_terms(session: Session, request: Request) -> tuple[str, ...]:
    """读日志时用的凭据词表：会话令牌 + 该平台的兜底令牌。

    日志**写盘时**已经过一遍脱敏；但读出来是第五条出口，多过一道不吃亏（词表在这里算得出
    来，是因为我们手上有这个会话）。
    """
    settings = request.app.state.settings
    return secret_terms(
        session.repo.token,
        settings.token_for(session.repo.platform),
        # 模型凭据同样要脱敏（2026-10-13）：日志接口读的就是日志，词表少一格就漏一格
        *settings.model_secret_values(),
    )


@router.get("/{session_id}/logs", response_model=LogPage)
async def get_logs(
    session_id: str,
    request: Request,
    limit: int = 300,
    level: str | None = None,
    q: str | None = None,
    with_http: bool = False,
) -> LogPage:
    """该会话的日志（尾部 ``limit`` 行，按时间合并成一条线）。

    **按会话筛**而不是把整份日志倒出来：命中 ``[<会话id>]``（格式里的会话格）、
    ``/sessions/<id>`` ``/chat/<id>``（接口请求行只带路径）、``public_id=<id>``（老行兼容）
    这四种写法之一。``level`` 是**阈值**（给 WARNING 就是 WARNING 及以上），``q`` 是关键字
    （大小写不敏感的子串），``with_http=true`` 才把 HTTP 请求噪声算进来（默认滤掉，见
    ``observability/log_tail.py`` 的两条噪声口径 —— 其中「日志接口自己」那类永远不显示）。

    只读当前那份文件，不追 ``.1``/``.2`` 备份 —— 「尾部 N 行」的含义不该取决于轮转状态；
    要看更早的整份，用同目录的下载接口。
    """
    session = await resolve_session(request, session_id)
    terms = _redaction_terms(session, request)
    picked = read_session_logs(
        _log_paths(request),
        session_id,
        limit=max(1, min(limit, 5000)),
        level=level,
        query=q,
        with_http=with_http,
    )
    return LogPage(
        total=picked.total,
        lines=[
            LogLineItem(
                ts=item.ts, level=item.level, logger=item.logger, text=redact(item.text, terms)
            )
            for item in picked.lines
        ],
    )


@router.get("/{session_id}/logs/download", response_class=PlainTextResponse)
async def download_logs(session_id: str, request: Request) -> PlainTextResponse:
    """把**这个会话的**日志整份下载下来（``.txt`` 附件）。

    面板里那个「下载」按钮走它：界面只显示尾部若干行，这里给全（同样只读当前文件）。
    内容一样过一遍脱敏。
    """
    session = await resolve_session(request, session_id)
    terms = _redaction_terms(session, request)
    # 下载与面板同一套噪声口径（HTTP 请求默认不算），但不带 limit / 关键字 —— 它给的是整份
    picked = read_session_logs(_log_paths(request), session_id)
    body = redact("\n".join(item.text for item in picked.lines), terms)
    owner, name = session.repo.owner, session.repo.repo
    return PlainTextResponse(
        body + ("\n" if body else ""),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{owner}-{name}-logs.txt"'
        },
    )


@router.get("/{session_id}/pulls", response_model=PullList)
async def get_pulls(session_id: str, request: Request) -> PullList:
    """本仓库的 PR 列表（右栏「PR」标签；``open_count`` 顺带喂主区顶部的角标）。

    **查不到 ≠ 没有**：查询失败一律 502 + 原因，**绝不返回空列表** —— 「这个仓库一个 PR 都
    没有」和「这次没查成」在界面上必须是两句话。这条纪律与仓库记忆同源（见
    ``tools/pr_client.pulls_or_raise``）。

    状态码由 ``pull_summary`` 归一（Gitee 把 merged 写在 state 上、GitHub 看 merged_at），
    与闸门判「有没有未合入的 PR」用的是同一个判定。
    """
    session = await resolve_session(request, session_id)
    manager = getattr(request.app.state, "manager", None)
    settings = (
        manager.settings_for(session)
        if manager is not None
        else request.app.state.settings
    )
    client = build_git_client(settings, session.repo)
    try:
        raw = await client.all_pulls()
    except PrLookupError as exc:
        raise HTTPException(
            status_code=502, detail=f"查询本仓库 PR 失败：{exc}"
        ) from exc
    items = [
        PullItem(**pull_summary(pull))
        for pull in raw
        if isinstance(pull, Mapping) and str(pull.get("number") or "").strip()
    ]
    # 未合并的排最前，其余按编号倒序（新的在上）：打开面板第一眼要看到「还开着的」
    items.sort(key=lambda item: (item.state != PULL_OPEN, -_pull_number(item.number)))
    return PullList(
        open_count=sum(1 for item in items if item.state == PULL_OPEN), pulls=items
    )


@router.get("/{session_id}/usage", response_model=UsageSnapshot)
async def get_usage(session_id: str, request: Request) -> UsageSnapshot:
    """本会话的 token 消耗（右栏「Token」标签）。

    **聚合视图，不改任何数据**：数据源是各轮 assistant 记录里的 ``extra["usage"]``
    （由 ``server/turn/records.py`` 在轮末写入，按执行主角分类）。

    两条口径都必须如实外传，界面才不会说谎：
    - 本功能上线前的记录**没有**那份数据 —— 回 ``rounds_with_usage`` 让界面说
      「其中 N 轮没有用量数据」，而不是把缺的轮次当 0 消耗；
    - ``missing`` 是「平台没回用量的调用次数」。
    """
    session = await resolve_session(request, session_id)
    db = request.app.state.database
    async with db.session() as s:
        rows = await dao.list_records_with_detail(s, session_id=session.db_id)
    return _aggregate_usage(rows)


@router.get("/{session_id}/memory", response_model=MemorySnapshot)
async def get_memory(session_id: str, request: Request) -> MemorySnapshot:
    """仓库记忆全文（**只读**）。

    记忆原先只活在系统提示词里 —— 模型看得到、用户看不到。这里把它显示出来（右栏「记忆」
    标签）：读的是 StoreBackend 上那一份，与中间件读写的是**同一个 key**。

    **不提供写入、也不提供手动刷新**：正文只在 coding / review 的收尾由主控写，文件头由平台
    每轮开工前按 PR 实况重算（见 AGENTS.md 的写入口径）。在这里开一个「改一下」的口子，
    就把那条约束破了；要更新记忆就走对话（刷新仓库记忆）或跑一轮编码/审查。
    """
    session = await resolve_session(request, session_id)
    repo = session.repo
    prefix = request.app.state.settings.memories_prefix
    return MemorySnapshot(
        path=memory_path(repo.platform.value, repo.owner, repo.repo, prefix),
        content=await read_memory(
                request.app.state.store,
                repo.platform.value,
                repo.owner,
                repo.repo,
                prefix,
        ),
    )


@router.post("/{session_id}/cancel")
async def cancel_run(session_id: str, request: Request) -> dict[str, bool]:
    """终止该会话正在跑的那一轮（前端「终止」按钮）。

    终止的是「那一轮运行」：本进程在跑的立刻 cancel；挂在别的 worker 上的留终止标记，
    由那边自己取消（配了 Redis 才有多 worker，见 ``server/run_store.py``）。已挂着的
    订阅者随之看到流结束、不再产帧。**不查库、不改会话状态** —— 会话与历史都留着
    （要拆会话走 ``DELETE``），所以没在跑就回 ``{"cancelled": false}``，重按一次也不算错误。
    """
    await resolve_session(request, session_id)
    return {"cancelled": await request.app.state.runs.cancel(session_id)}


@router.delete("/{session_id}")
async def destroy_session(session_id: str, request: Request) -> dict[str, bool]:
    """销毁会话：软取消在飞的构建、终止代码工作区、库里标记 destroyed。

    会话不在内存（后端重启过）时也能删干净 —— 管理器据库里记的 sandbox_id 终止容器，
    不再像以前那样只改个状态、把容器漏在 OpenSandbox 侧。
    """
    session = await resolve_session(request, session_id)
    manager = request.app.state.manager
    cache = getattr(request.app.state, "session_cache", None)
    if cache is not None:
        await cache.invalidate(session_id)
    legacy = getattr(request.app.state, "sessions", None)
    if isinstance(legacy, dict):
        legacy.pop(session_id, None)
    # 会话要拆了，正在跑的那一轮留着只会对着废掉的工作区写库
    await request.app.state.runs.cancel(session_id)

    await manager.destroy_session(public_id=session_id, db_id=session.db_id)
    return {"ok": True}
