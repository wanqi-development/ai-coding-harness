"""会话记录（轻表 + 详情）与对话摘要的 DAO。"""

from __future__ import annotations

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import (
    Plan,
    SessionRecord,
    SessionRecordDetail,
)


# ---------------------------------------------------------------------------
# session records（页面展示：轻表 + 详情表）
# ---------------------------------------------------------------------------
async def plan_record_exists(
    db: AsyncSession, *, session_id: int, plan_id: int
) -> bool:
    """这个会话里有没有**这一份**方案的记录（``type='plan'`` + ``ref_id=plan_id``）。

    给「方案只在首次生成时进聊天流」当判据：一次新的运行里内存标记从零起步，跨轮去重
    只能靠库里查（同一份方案被 updatePlan / updatePlanSteps 改过多少次都只该有一条记录）。
    """
    found = await db.scalar(
        select(SessionRecord.id)
        .where(SessionRecord.session_id == session_id)
        .where(SessionRecord.type == "plan")
        .where(SessionRecord.ref_id == plan_id)
        .limit(1)
    )
    return found is not None


async def delete_plan_record(
    db: AsyncSession, *, session_id: int, plan_id: int
) -> int:
    """删除某份方案在聊天流里的展示记录（方案行本身不动）。"""
    record_ids = select(SessionRecord.id).where(
        SessionRecord.session_id == session_id,
        SessionRecord.type == "plan",
        SessionRecord.ref_id == plan_id,
    )
    await db.execute(
        delete(SessionRecordDetail).where(
            SessionRecordDetail.record_id.in_(record_ids)
        )
    )
    result = await db.execute(
        delete(SessionRecord).where(
            SessionRecord.session_id == session_id,
            SessionRecord.type == "plan",
            SessionRecord.ref_id == plan_id,
        )
    )
    return int(result.rowcount or 0)


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
    db: AsyncSession, *, session_id: int, limit: int = 500
) -> list[SessionRecord]:
    rows = await db.scalars(
        select(SessionRecord)
        .where(SessionRecord.session_id == session_id)
        # 先取最近 N 条，再翻回时间正序。按正序 limit 会丢掉历史末尾的几轮，
        # 而不是像「最近 N 条」那样只丢掉更早的那一段。
        .order_by(SessionRecord.created_at.desc(), SessionRecord.id.desc())
        .limit(limit)
    )
    return list(reversed(list(rows)))


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


async def recent_dialogue(
    db: AsyncSession, *, session_id: int, rounds: int = 3
) -> list[tuple[str, str]]:
    """最近 ``rounds`` 个用户轮次内的完整会话记录（按时间正序）。

    轮次以 ``user`` 记录为边界；返回期间的全部 user / assistant / **plan** 记录。
    plan 是会话时间线里的独立记录，不是 assistant 详情附件，必须一起带上。
    正文不在这里截断，调用方按自己的预算处理。
    """
    user_ids = list(
        (
            await db.scalars(
                select(SessionRecord.id)
                .where(
                    SessionRecord.session_id == session_id,
                    SessionRecord.type == "user",
                )
                .order_by(SessionRecord.id.desc())
                .limit(max(1, rounds))
            )
        ).all()
    )
    if not user_ids:
        return []
    rows = await db.execute(
        select(SessionRecord.type, SessionRecordDetail.content)
        .join(SessionRecordDetail, SessionRecordDetail.record_id == SessionRecord.id)
        .where(
            SessionRecord.session_id == session_id,
            SessionRecord.id >= min(user_ids),
            SessionRecord.type.in_(("user", "assistant", "plan")),
        )
        .order_by(SessionRecord.id.asc())
    )
    return [
        (str(kind), str(content))
        for kind, content in rows.all()
        if isinstance(content, str) and content.strip()
    ]


async def latest_assistant_tail(
    db: AsyncSession, *, session_id: int, chars: int = 400
) -> str | None:
    """最近一条**助手回复**的末尾 ``chars`` 字（没有就返回 ``None``）。

    判定意图要看的是「助手上一轮问了用户什么」—— 那句反问几乎总在回复的**末尾**，所以取
    尾部而不是开头（记录的 ``summary`` 是 ``content[:200]``，正好会把反问截掉，不能用）。

    只认 ``type == "assistant"``：``plan`` / 工具那些记录也在时间线上，但它们不承载「助手
    对用户说了什么」。截断在 Python 里做（不用 PG 专属的 ``right()``）：一行最多几千字，
    取回再切比多一条数据库方言依赖划算。
    """
    text = await db.scalar(
        select(SessionRecordDetail.content)
        .join(SessionRecord, SessionRecord.id == SessionRecordDetail.record_id)
        .where(SessionRecord.session_id == session_id, SessionRecord.type == "assistant")
        .order_by(SessionRecord.id.desc())
        .limit(1)
    )
    if not isinstance(text, str) or not text.strip():
        return None
    return text[-max(1, chars) :]


async def latest_assistant_intent(
    db: AsyncSession, *, session_id: int
) -> str | None:
    """最近一条助手记录的意图（``extra.intent``）；没有记录就返回 ``None``。

    与 ``latest_assistant_tail`` 取同一条记录：上一轮**说了什么**和**是什么意图**
    是同一份上下文，分开两次查询只是为了不把 ``extra`` 整份带进尾部节选的签名里。
    """
    extra = await db.scalar(
        select(SessionRecordDetail.extra)
        .join(SessionRecord, SessionRecord.id == SessionRecordDetail.record_id)
        .where(SessionRecord.session_id == session_id, SessionRecord.type == "assistant")
        .order_by(SessionRecord.id.desc())
        .limit(1)
    )
    if isinstance(extra, dict):
        intent = extra.get("intent")
        return str(intent) if intent else None
    return None


async def recent_assistant_intents(
    db: AsyncSession, *, session_id: int, limit: int = 10
) -> list[str]:
    """最近几条助手记录的意图（新 → 旧），供“未完成任务”回溯。

    最近一轮可能是 analysis/qa，但它不覆盖更早的 coding/rework；调用方按
    “最近的可执行意图”挑一个。
    """
    rows = await db.scalars(
        select(SessionRecordDetail.extra)
        .join(SessionRecord, SessionRecord.id == SessionRecordDetail.record_id)
        .where(SessionRecord.session_id == session_id, SessionRecord.type == "assistant")
        .order_by(SessionRecord.id.desc())
        .limit(limit)
    )
    intents: list[str] = []
    for extra in rows:
        if isinstance(extra, dict) and extra.get("intent"):
            intents.append(str(extra["intent"]))
    return intents


async def list_records_with_detail(
    db: AsyncSession, *, session_id: int, limit: int = 500
) -> list[tuple[SessionRecord, SessionRecordDetail | None]]:
    """会话时间线（记录 + 详情），用于前端展示。"""
    rows = await db.execute(
        select(SessionRecord, SessionRecordDetail)
        .outerjoin(SessionRecordDetail, SessionRecordDetail.record_id == SessionRecord.id)
        .where(SessionRecord.session_id == session_id)
        # 同 list_records：取最近 N 条，再翻回时间正序。否则 limit 会把最新的几轮
        # 直接截掉（207 条会话只回 200 条，用户看到的就是「少了几轮」）。
        .order_by(SessionRecord.id.desc())
        .limit(limit)
    )
    return list(reversed(rows.all()))


async def list_records_page(
    db: AsyncSession,
    *,
    session_id: int,
    before_id: int | None = None,
    limit: int = 20,
) -> tuple[list[tuple[SessionRecord, SessionRecordDetail | None]], bool, int | None]:
    """取一页**较新的历史**，返回 ``(按时间正序的条目, 是否还有更早, 下一页游标)``。

    用 ``id < before_id`` 向前翻；一次多取一条判断 ``has_more``，不额外 count。
    返回时把倒序结果翻回正序，前端可以直接 prepend。
    """
    query = (
        select(SessionRecord, SessionRecordDetail)
        .outerjoin(SessionRecordDetail, SessionRecordDetail.record_id == SessionRecord.id)
        .outerjoin(
            Plan,
            and_(
                SessionRecord.type == "plan",
                Plan.id == SessionRecord.ref_id,
                Plan.session_id == SessionRecord.session_id,
            ),
        )
        .where(SessionRecord.session_id == session_id)
        # 方案记录只是当前进度入口：软删或被新方案替代后，刷新页面也不再回放旧方案。
        .where(
            or_(
                SessionRecord.type != "plan",
                Plan.id.is_(None),
                Plan.status.not_in(("closed", "superseded")),
            )
        )
    )
    if before_id is not None:
        query = query.where(SessionRecord.id < before_id)
    rows = list(
        (
            await db.execute(
                query.order_by(SessionRecord.id.desc()).limit(max(1, limit) + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    page.reverse()
    next_before_id = page[0][0].id if page else None
    return page, has_more, next_before_id
