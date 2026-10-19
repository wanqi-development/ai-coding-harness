"""业务库 DAO —— 各业务表的增删改查。

约定：所有函数接收调用方传入的 ``AsyncSession``，不自行提交；提交/回滚由
``Database.session()`` 上下文统一处理。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Text, cast, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.codeplatform import Platform
from ai_coding_harness.db.models import (
    AgentSession,
    AppSetting,
    Clarification,
    ModelEndpoint,
    ModelProvider,
    Plan,
    ProviderModel,
    Repository,
    SessionRecord,
    SessionRecordDetail,
)

if TYPE_CHECKING:  # 只为标注：真 import 会把 config 拖进来，DAO 不该依赖它
    from ai_coding_harness.config import ProviderMeta


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


# --------------------------------------------------------------------------- 模型连接（2026-10-13）


async def list_model_endpoints(db: AsyncSession) -> list[ModelEndpoint]:
    """模型连接表的全部行（键排序，便于逐行对照界面）。"""
    rows = await db.scalars(select(ModelEndpoint).order_by(ModelEndpoint.agent))
    return list(rows)


async def upsert_model_endpoint(
    db: AsyncSession,
    *,
    agent: str,
    base_url: str | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
    context_window: int | None = None,
) -> None:
    """写一行模型连接（没有就插入）。

    ``None`` ＝ **不动这一格**（界面上留空就是不改、也不会把已有的 key 抹掉）；
    空串是合法值（用户想清掉 base_url / 模型名时传空串）。
    """
    row = await db.scalar(select(ModelEndpoint).where(ModelEndpoint.agent == agent))
    if row is None:
        row = ModelEndpoint(agent=agent)
        db.add(row)
    for field, value in (
        ("base_url", base_url),
        ("model_name", model_name),
        ("api_key", api_key),
        ("context_window", context_window),
    ):
        if value is not None:
            setattr(row, field, value)
    row.updated_at = _now()


async def delete_model_endpoint(db: AsyncSession, *, agent: str) -> bool:
    """删掉一行的覆盖值（＝恢复出厂默认），返回是否真删了。"""
    result = await db.execute(delete(ModelEndpoint).where(ModelEndpoint.agent == agent))
    return bool(result.rowcount)


# ------------------------------------------------------------------- 运行期配置（2026-10-14）


async def list_app_settings(db: AsyncSession) -> dict[str, Any]:
    """运行期配置的**全部覆盖值** ``{键: 值}``（库里没写过的键不出现＝用 ``.env`` 那个默认值）。"""
    rows = await db.scalars(select(AppSetting).order_by(AppSetting.key))
    return {row.key: row.value for row in rows}


async def upsert_app_setting(db: AsyncSession, *, key: str, value: Any) -> None:
    """写一格运行期配置（没有就插入）。**整格覆盖** —— 不存在「只改一半」的语义。"""
    row = await db.scalar(select(AppSetting).where(AppSetting.key == key))
    if row is None:
        row = AppSetting(key=key)
        db.add(row)
    row.value = value
    row.updated_at = _now()


async def list_model_providers(db: AsyncSession) -> list[ModelProvider]:
    """提供商目录的全部行（启用／停用都在，排序交给上层）。"""
    rows = await db.scalars(select(ModelProvider).order_by(ModelProvider.provider))
    return list(rows)


async def count_model_providers(db: AsyncSession) -> int:
    """目录里有多少行（**只**用来判断「要不要灌种子」，别拿它当界面数据）。"""
    return int(await db.scalar(select(func.count()).select_from(ModelProvider)) or 0)


async def upsert_model_provider(
    db: AsyncSession,
    *,
    provider: str,
    display_name: str,
    default_base_url: str = "",
    ui_group: str = "",
    sort_order: int = 0,
    status: int = 1,
    is_dynamic_model_list: bool = True,
    models_path: str = "/models",
    auth_header: str = "Authorization",
    auth_prefix: str = "Bearer ",
) -> None:
    """写一行提供商（没有就插入）。**整行覆盖** —— 与 ``model_endpoints`` 的「留空＝不改」不同：

    目录有三个来源（种子文件 / reload 接口 / 设置页那一栏），没有「只改一格」的用法，
    所以这里宁可整行写，免得老值赖着不走。
    """
    row = await db.scalar(select(ModelProvider).where(ModelProvider.provider == provider))
    if row is None:
        row = ModelProvider(provider=provider)
        db.add(row)
    row.display_name = display_name
    row.default_base_url = default_base_url
    row.ui_group = ui_group
    row.sort_order = sort_order
    row.status = status
    row.is_dynamic_model_list = is_dynamic_model_list
    row.models_path = models_path
    row.auth_header = auth_header
    row.auth_prefix = auth_prefix
    row.updated_at = _now()


async def seed_model_providers(
    db: AsyncSession, metas: Sequence[ProviderMeta]
) -> int:
    """用种子文件灌一次目录，返回灌了几行。

    **只在表为空时调**（调用方先 ``count_model_providers``）——「之后以库为准」，用户的改动不该
    被下次启动覆盖回去。要重新对齐种子文件走 ``POST /model-config/providers/reload``。
    """
    for meta in metas:
        await upsert_model_provider(
            db,
            provider=meta.provider,
            display_name=meta.display_name,
            default_base_url=meta.default_base_url,
            ui_group=meta.ui_group,
            sort_order=meta.sort_order,
            status=meta.status,
            is_dynamic_model_list=meta.is_dynamic_model_list,
            models_path=meta.models_path,
            auth_header=meta.auth_header,
            auth_prefix=meta.auth_prefix,
        )
    return len(metas)


# --------------------------------------------------- 供应商的模型详情（2026-10-14）


async def list_provider_models(
    db: AsyncSession, *, provider: str | None = None
) -> list[ProviderModel]:
    """模型详情表的行；给了 ``provider`` 就只看这一家的。"""
    query = select(ProviderModel).order_by(ProviderModel.provider, ProviderModel.model)
    if provider is not None:
        query = query.where(ProviderModel.provider == provider)
    rows = await db.scalars(query)
    return list(rows)


async def upsert_provider_model(
    db: AsyncSession,
    *,
    provider: str,
    model: str,
    model_type: str = "chat",
    context_window: int = 0,
    source: str = "manual",
    status: int = 1,
) -> None:
    """写一条模型详情（没有就插入）。

    **窗口为 0 时不覆盖已有的非 0 值**：刷新清单时很多入口不回窗口（实测如此），
    照写会把用户手填/上次读到的那个抹成 0 —— 那是信息损失，不是更新。
    """
    row = await db.scalar(
        select(ProviderModel).where(
            ProviderModel.provider == provider, ProviderModel.model == model
        )
    )
    if row is None:
        row = ProviderModel(provider=provider, model=model)
        db.add(row)
    row.model_type = model_type
    if context_window > 0 or not row.context_window:
        row.context_window = context_window
    row.source = source
    row.status = status
    row.updated_at = _now()


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
    db: AsyncSession, *, session_id: int, limit: int = 4
) -> list[tuple[str, str]]:
    """最近几条用户/助手消息（按时间正序返回），供意图判定带“两轮对话”。

    ``limit=4`` 是两轮（user + assistant × 2）。用户消息由调用方保留全文，
    助手消息由调用方做首尾截断；这里只负责按时间线取原文。
    """
    rows = await db.execute(
        select(SessionRecord.type, SessionRecordDetail.content)
        .join(SessionRecordDetail, SessionRecordDetail.record_id == SessionRecord.id)
        .where(
            SessionRecord.session_id == session_id,
            SessionRecord.type.in_(("user", "assistant")),
        )
        .order_by(SessionRecord.id.desc())
        .limit(limit)
    )
    return [
        (str(kind), str(content))
        for kind, content in reversed(list(rows.all()))
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
        .where(SessionRecord.session_id == session_id)
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


# ---------------------------------------------------------------------------
# clarifications（意图澄清的一次性 pending 状态）
# ---------------------------------------------------------------------------
async def save_clarification(
    db: AsyncSession,
    *,
    session_id: int,
    original_message: str,
    question: str,
    options: list[dict[str, Any]],
) -> Clarification:
    """写入本会话的待澄清状态；旧的一条直接替换（每个会话最多一条）。"""
    await db.execute(delete(Clarification).where(Clarification.session_id == session_id))
    row = Clarification(
        id=str(uuid.uuid4()),
        session_id=session_id,
        original_message=original_message,
        question=question,
        options=options,
    )
    db.add(row)
    await db.flush()
    return row


async def get_clarification(
    db: AsyncSession, *, session_id: int
) -> Clarification | None:
    return await db.scalar(
        select(Clarification).where(Clarification.session_id == session_id)
    )


async def delete_clarification(db: AsyncSession, *, session_id: int) -> bool:
    result = await db.execute(
        delete(Clarification).where(Clarification.session_id == session_id)
    )
    return bool(result.rowcount)
