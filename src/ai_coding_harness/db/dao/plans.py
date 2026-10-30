"""方案对象与步骤状态的 DAO。"""

from __future__ import annotations

from sqlalchemy import Text, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import (
    Plan,
)

from ._common import _now

_CURRENT_PLAN_STATUSES = ("pending", "failed")


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


async def get_replaceable_plan(db: AsyncSession, *, session_id: int) -> Plan | None:
    """取当前可由新方案替代的那一份（最新 pending / failed）。

    一个会话的当前进度最多保留一份方案；``done`` 是已完成任务的历史，``executing``
    正在执行，三者都不自动替代。
    """
    return await db.scalar(
        select(Plan)
        .where(
            Plan.session_id == session_id,
            Plan.status.in_(_CURRENT_PLAN_STATUSES),
        )
        .order_by(Plan.id.desc())
        .limit(1)
        .with_for_update()
    )


async def get_latest_plan(db: AsyncSession, *, session_id: int) -> Plan | None:
    """取该会话最新的一条当前方案（给「展示当前进度」用）。

    ``get_unexecuted_plan`` 只认 pending，方案一开工就取不到了；面板要的是正在
    推进或刚完成的那一份。按 id 倒序，避免同秒创建的两份方案靠 created_at 分不出先后。
    用户软删（``closed``）和已被新方案替代（``superseded``）的方案不参与：
    它们不再是当前进度，但行仍留在库里供 PR / 方案 ID 追溯。
    """
    return await db.scalar(
        select(Plan)
        .where(
            Plan.session_id == session_id,
            Plan.status.not_in(("closed", "superseded")),
        )
        .order_by(Plan.id.desc())
        .limit(1)
    )


async def get_plan(db: AsyncSession, *, session_id: int, plan_id: int) -> Plan | None:
    """按 id 取本会话的一条方案。**含 closed / superseded** —— 归档只是状态变了。

    ``WHERE`` 里限定 ``session_id``：模型给的 plan_id 可能来自别的会话（或纯属猜的），
    跨会话读写方案是不该发生的事。
    """
    return await db.scalar(
        select(Plan).where(Plan.id == plan_id, Plan.session_id == session_id)
    )


async def plan_status(
    db: AsyncSession, *, session_id: int, plan_id: int
) -> str | None:
    """只要**状态那一列**（方案不存在返回 ``None``）。

    判「这份方案还能不能拿去实施」不该把方案正文拖回来 —— 正文是大对象（实测一份一万字），
    而调用方要的只是一个布尔。少 SELECT 一列就少传一万字，这一条是给主控在派 coder 之前
    反复判定用的（见 ``tools/plan.py`` 的 ``planExists``）。
    """
    return await db.scalar(
        select(Plan.status).where(Plan.id == plan_id, Plan.session_id == session_id)
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

    ``closed`` 是用户软删除，``superseded`` 是被新方案替代；两者都留在库里，
    但面板与「未执行方案」都不再认它们。
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


async def reclaim_executing_plan(
    db: AsyncSession,
    *,
    session_id: int,
    has_pr: bool,
    plan_id: int | None = None,
) -> int | None:
    """回收上一轮遗留的 executing 方案。

    有 PR：方案视为已完成，所有步骤收口为 done。
    无 PR：coder 未交付 PR，方案恢复 pending，running 步骤退回 pending。

    只匹配 ``status='executing'``，所以重复调用、multi-worker 竞争都是幂等的。
    """
    query = select(Plan).where(
        Plan.session_id == session_id,
        Plan.status == "executing",
    )
    if plan_id is not None:
        query = query.where(Plan.id == plan_id)
    plan = await db.scalar(query.order_by(Plan.id.desc()).limit(1))
    if plan is None:
        return None

    steps = [
        dict(step) if isinstance(step, dict) else step
        for step in (plan.steps or [])
    ]
    now = _now()
    if has_pr:
        status = "done"
        for step in steps:
            if isinstance(step, dict):
                step["status"] = "done"
        values: dict = {
            "status": status,
            "steps": steps,
            "updated_at": now,
            "finished_at": now,
        }
    else:
        status = "pending"
        for step in steps:
            if isinstance(step, dict) and step.get("status") == "running":
                step["status"] = "pending"
        values = {
            "status": status,
            "steps": steps,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
        }
    result = await db.execute(
        update(Plan)
        .where(
            Plan.id == plan.id,
            Plan.session_id == session_id,
            Plan.status == "executing",
        )
        .values(**values)
        .returning(Plan.id)
    )
    return result.scalar_one_or_none()


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
    if db.get_bind().dialect.name == "sqlite":
        row = await db.scalar(
            select(Plan).where(
                Plan.id == plan_id,
                Plan.session_id == session_id,
            )
        )
        if row is None or not isinstance(row.steps, list) or position >= len(row.steps):
            return None
        steps = [dict(item) if isinstance(item, dict) else item for item in row.steps]
        step = steps[position]
        if not isinstance(step, dict):
            return None
        step["status"] = status
        row.steps = steps
        row.updated_at = _now()
        await db.flush()
        return steps
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
