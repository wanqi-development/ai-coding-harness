"""业务库 DAO —— 各业务表的增删改查。

约定：所有函数接收调用方传入的 ``AsyncSession``，不自行提交；提交/回滚由
``Database.session()`` 上下文统一处理。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Text, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.codeplatform import Platform
from ai_coding_harness.db.models import (
    AgentSession,
    Plan,
    Repository,
    SessionRecord,
    SessionRecordDetail,
)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
async def create_session(
    db: AsyncSession,
    *,
    public_id: uuid.UUID,
    gitee_token: str | None = None,
    gitee_username: str | None = None,
    git_platform: str | None = None,
    title: str | None = None,
) -> AgentSession:
    row = AgentSession(
        public_id=public_id,
        gitee_token=gitee_token,
        gitee_username=gitee_username,
        # 认不出的取值一律回落 Gitee（与 Platform.of 同一口径），列本身非空
        git_platform=Platform.of(git_platform).value,
        title=title,
    )
    db.add(row)
    await db.flush()
    return row


async def get_session_by_public_id(db: AsyncSession, public_id: uuid.UUID) -> AgentSession | None:
    return await db.scalar(select(AgentSession).where(AgentSession.public_id == public_id))


async def get_session(db: AsyncSession, session_id: int) -> AgentSession | None:
    return await db.get(AgentSession, session_id)


async def list_sessions(db: AsyncSession, *, limit: int = 50) -> list[AgentSession]:
    rows = await db.scalars(
        select(AgentSession).order_by(AgentSession.created_at.desc()).limit(limit)
    )
    return list(rows)


async def list_sessions_with_repo(
    db: AsyncSession, *, limit: int = 50
) -> list[tuple[AgentSession, Repository | None]]:
    """会话列表（左连仓库），用于前端历史侧栏。

    删除是软删除（状态置 destroyed，保留记录供审计），故列表按状态排除已删除会话。
    """
    rows = await db.execute(
        select(AgentSession, Repository)
        .outerjoin(Repository, Repository.session_id == AgentSession.id)
        .where(AgentSession.status != "destroyed")
        .order_by(AgentSession.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())


async def get_session_with_repo(
    db: AsyncSession, *, public_id: uuid.UUID
) -> tuple[AgentSession, Repository | None] | None:
    """按对外 ID 取会话 + 其仓库（没有该会话返回 ``None``）。

    取会话描述（重建工作区、销毁、详情页）都要这两行，分两次查容易漏掉仓库那一半。
    """
    row = (
        await db.execute(
            select(AgentSession, Repository)
            .outerjoin(Repository, Repository.session_id == AgentSession.id)
            .where(AgentSession.public_id == public_id)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def set_session_sandbox(
    db: AsyncSession,
    session_id: int,
    *,
    sandbox_id: str | None,
    workspace_volume: str | None,
) -> None:
    now = _now()
    await db.execute(
        update(AgentSession)
        .where(AgentSession.id == session_id)
        .values(
            sandbox_id=sandbox_id,
            workspace_volume=workspace_volume,
            updated_at=now,
            last_active_at=now,
        )
    )


async def set_session_status(db: AsyncSession, session_id: int, status: str) -> None:
    await db.execute(
        update(AgentSession)
        .where(AgentSession.id == session_id)
        .values(status=status, updated_at=_now())
    )


async def set_workspace_status(
    db: AsyncSession,
    session_id: int,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """更新代码工作区状态（pending / provisioning / ready / failed）。

    ``error`` 每次整段覆盖：转 ready 时传 ``None`` 把上次的失败原因清掉，避免
    残留的旧错误让人以为这次也是坏的。
    """
    await db.execute(
        update(AgentSession)
        .where(AgentSession.id == session_id)
        .values(workspace_status=status, workspace_error=error, updated_at=_now())
    )


async def set_session_title_if_empty(db: AsyncSession, session_id: int, title: str) -> None:
    """给尚无标题的会话命名（会话标题取首条用户消息）。

    ``WHERE title IS NULL`` 让并发下也只有一条消息能命名成功，无需先查后写。
    """
    await db.execute(
        update(AgentSession)
        .where(AgentSession.id == session_id, AgentSession.title.is_(None))
        .values(title=title)
    )


async def delete_session(db: AsyncSession, session_id: int) -> None:
    await db.execute(AgentSession.__table__.delete().where(AgentSession.id == session_id))


# ---------------------------------------------------------------------------
# repositories
# ---------------------------------------------------------------------------
async def create_repository(
    db: AsyncSession,
    *,
    session_id: int,
    owner: str,
    name: str,
    url: str,
) -> Repository:
    row = Repository(session_id=session_id, owner=owner, name=name, url=url)
    db.add(row)
    await db.flush()
    return row


async def get_repository(db: AsyncSession, session_id: int) -> Repository | None:
    return await db.scalar(select(Repository).where(Repository.session_id == session_id))


async def find_active_session_by_repo(
    db: AsyncSession, *, owner: str, name: str
) -> tuple[AgentSession, Repository] | None:
    """取该仓库当前的活跃会话；没有返回 ``None``。

    仓库按 ``owner``/``name`` 小写比对 —— 同一个仓库换个写法（``.git``、末尾斜杠、大小写）
    不该绕开「一个仓库一个会话」。只认 ``active``：用户删掉的会话不再占位，删完可以重新建。
    """
    row = (
        await db.execute(
            select(AgentSession, Repository)
            .join(Repository, Repository.session_id == AgentSession.id)
            .where(
                func.lower(Repository.owner) == owner.lower(),
                func.lower(Repository.name) == name.lower(),
                AgentSession.status == "active",
            )
            .order_by(AgentSession.id)
            .limit(1)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def lock_repo(db: AsyncSession, *, owner: str, name: str) -> None:
    """按仓库加事务级咨询锁，把「查活跃会话 + 落库」串成一个原子段。

    没有它，两个请求同时建同一仓库会双双查空、各建一个会话（双击、两个标签页）。锁随本
    事务结束自动释放，不必显式解锁；锁**必须在查之前**拿，拿晚了查的还是旧快照。
    ``hashtext`` 撞了只是让不相干的仓库偶发排一下队，不影响正确性。
    """
    await db.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"{owner}/{name}".lower())))
    )


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------
async def store_plan(
    db: AsyncSession,
    *,
    session_id: int,
    owner: str,
    repo: str,
    content: str,
    steps: list,
    title: str | None = None,
) -> Plan:
    row = Plan(
        session_id=session_id,
        owner=owner,
        repo=repo,
        title=title,
        content=content,
        steps=steps,
    )
    db.add(row)
    await db.flush()
    return row


async def get_unexecuted_plan(db: AsyncSession, *, session_id: int) -> Plan | None:
    """取该会话下最新的一条未执行（pending）方案。"""
    return await db.scalar(
        select(Plan)
        .where(Plan.session_id == session_id, Plan.status == "pending")
        .order_by(Plan.created_at.desc())
        .limit(1)
    )


async def get_latest_plan(db: AsyncSession, *, session_id: int) -> Plan | None:
    """取该会话最新的一条方案（给「展示当前进度」用）：**已删除（closed）的不算**。

    ``get_unexecuted_plan`` 只认 pending，方案一开工就取不到了；面板要的是正在
    推进或刚完成的那一份。按 id 倒序，避免同秒创建的两份方案靠 created_at 分不出先后。
    软删除的方案（``status='closed'``）不参与：用户删掉它之后，面板该回落到上一条或空，
    而不是继续显示一份「已删除」的方案。
    """
    return await db.scalar(
        select(Plan)
        .where(Plan.session_id == session_id, Plan.status != "closed")
        .order_by(Plan.id.desc())
        .limit(1)
    )


async def get_plan(db: AsyncSession, *, session_id: int, plan_id: int) -> Plan | None:
    """按 id 取本会话的一条方案。**含已删除（closed）的** —— 软删只是状态变了。

    ``WHERE`` 里限定 ``session_id``：模型给的 plan_id 可能来自别的会话（或纯属猜的），
    跨会话读写方案是不该发生的事。
    """
    return await db.scalar(
        select(Plan).where(Plan.id == plan_id, Plan.session_id == session_id)
    )


async def update_plan_content(
    db: AsyncSession,
    *,
    plan_id: int,
    session_id: int,
    content: str | None = None,
    title: str | None = None,
) -> bool:
    """改方案的正文 / 标题（不动步骤与状态）。返回是否命中一行。

    调用方负责先确认「给了一个字段」「方案没被删」这两件事 —— 这里只管写。
    """
    values: dict = {"updated_at": _now()}
    if content is not None:
        values["content"] = content
    if title is not None:
        values["title"] = title
    result = await db.execute(
        update(Plan)
        .where(Plan.id == plan_id, Plan.session_id == session_id)
        .values(**values)
    )
    return bool(result.rowcount)


async def set_plan_steps(
    db: AsyncSession, *, plan_id: int, session_id: int, steps: list
) -> bool:
    """整份替换步骤数组（``index`` / ``status`` 由调用方规范化过）。返回是否命中一行。"""
    result = await db.execute(
        update(Plan)
        .where(Plan.id == plan_id, Plan.session_id == session_id)
        .values(steps=steps, updated_at=_now())
    )
    return bool(result.rowcount)


async def set_plan_status(
    db: AsyncSession, plan_id: int, status: str, *, session_id: int | None = None
) -> bool:
    """改方案状态。给了 ``session_id`` 就顺带限定会话（新工具都会给）。

    ``closed`` 是**软删除**用的状态：记录留在库里、面板与「未执行方案」都不再认它。
    """
    values: dict = {"status": status, "updated_at": _now()}
    if status == "executing":
        values["started_at"] = _now()
    elif status in ("done", "failed"):
        values["finished_at"] = _now()
    stmt = update(Plan).where(Plan.id == plan_id)
    if session_id is not None:
        stmt = stmt.where(Plan.session_id == session_id)
    result = await db.execute(stmt.values(**values))
    return bool(result.rowcount)


async def set_plan_step_status(
    db: AsyncSession,
    *,
    plan_id: int,
    session_id: int,
    index: int,
    status: str,
) -> list | None:
    """原子地更新方案某一步的状态，返回更新后的完整步骤数组；未命中返回 ``None``。

    用单条 ``UPDATE ... jsonb_set`` 完成，不在 Python 侧「读-改-写」：模型一个回合里
    会并发发多个工具调用，读-改-写会互相覆盖丢状态。

    ``index`` 是模型口径（从 1 开始），JSONB 数组路径从 0 开始，这里换算。
    ``WHERE`` 里限定 ``session_id`` 防跨会话改；``jsonb_array_length`` 兜住越界
    （``steps`` 为 NULL 时长度也是 NULL，条件不成立，同样落到未命中）。
    """
    if index < 1:
        # 负下标会让 jsonb_set 按 Python 语义回绕到末位（实测 -1 改写最后一步），
        # 静默改错数据比报错更糟，这里直接当未命中处理。
        return None
    position = index - 1
    path = cast(literal([str(position), "status"]), ARRAY(Text))
    result = await db.execute(
        update(Plan)
        .where(
            Plan.id == plan_id,
            Plan.session_id == session_id,
            func.jsonb_array_length(Plan.steps) > position,
        )
        .values(
            steps=func.jsonb_set(Plan.steps, path, func.to_jsonb(cast(status, Text))),
            updated_at=_now(),
        )
        .returning(Plan.steps)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# session records（页面展示：轻表 + 详情表）
# ---------------------------------------------------------------------------
async def add_record(
    db: AsyncSession,
    *,
    session_id: int,
    type: str,
    summary: str | None = None,
    ref_id: int | None = None,
    content: str | None = None,
    extra: dict | list | None = None,
) -> SessionRecord:
    """写入一条会话记录（基本信息 + 详情）。"""
    record = SessionRecord(
        session_id=session_id, type=type, summary=summary, ref_id=ref_id
    )
    db.add(record)
    await db.flush()
    db.add(SessionRecordDetail(record_id=record.id, content=content, extra=extra))
    await db.flush()
    return record


async def list_records(
    db: AsyncSession, *, session_id: int, limit: int = 200
) -> list[SessionRecord]:
    rows = await db.scalars(
        select(SessionRecord)
        .where(SessionRecord.session_id == session_id)
        .order_by(SessionRecord.created_at)
        .limit(limit)
    )
    return list(rows)


async def get_record_detail(db: AsyncSession, record_id: int) -> SessionRecordDetail | None:
    return await db.get(SessionRecordDetail, record_id)


async def recent_user_summaries(
    db: AsyncSession, *, session_id: int, limit: int = 4
) -> list[str]:
    """最近若干条用户消息（按时间正序返回），供意图分类当上下文。

    分类要判「就按你说的弄」这类指代，只看本轮消息判不出来；这里取的是历史用户
    消息摘要，本轮消息此时还没入库，天然不在其中。
    """
    rows = await db.scalars(
        select(SessionRecord.summary)
        .where(SessionRecord.session_id == session_id, SessionRecord.type == "user")
        .order_by(SessionRecord.id.desc())
        .limit(limit)
    )
    return [summary for summary in reversed(list(rows)) if summary]


async def list_records_with_detail(
    db: AsyncSession, *, session_id: int, limit: int = 200
) -> list[tuple[SessionRecord, SessionRecordDetail | None]]:
    """会话时间线（记录 + 详情），用于前端展示。"""
    rows = await db.execute(
        select(SessionRecord, SessionRecordDetail)
        .outerjoin(SessionRecordDetail, SessionRecordDetail.record_id == SessionRecord.id)
        .where(SessionRecord.session_id == session_id)
        .order_by(SessionRecord.id)
        .limit(limit)
    )
    return list(rows.all())
