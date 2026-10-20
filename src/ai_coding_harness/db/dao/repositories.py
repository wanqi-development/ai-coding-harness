"""仓库绑定、活跃会话查找与仓库锁的 DAO。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import (
    AgentSession,
    Repository,
)


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
