"""模型端点、运行期设置、供应商目录与模型详情的 DAO。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_coding_harness.db.models import (
    AgentSession,
    AppSetting,
    ModelEndpoint,
    ModelProvider,
    ProviderModel,
    Repository,
    UserSetting,
)

if TYPE_CHECKING:  # 只为标注：真 import 会把 config 拖进来，DAO 不该依赖它
    from ai_coding_harness.config import ProviderMeta

from ._common import _now

UNSET = object()

# --------------------------------------------------------------------------- 模型连接（2026-10-13）


async def list_model_endpoints(
    db: AsyncSession, *, user_id: int | None = None
) -> list[ModelEndpoint]:
    """模型连接行；给 ``user_id`` 时只读该用户自己的配置。"""
    query = select(ModelEndpoint).order_by(
        ModelEndpoint.user_id, ModelEndpoint.agent
    )
    if user_id is not None:
        query = query.where(ModelEndpoint.user_id == user_id)
    return list(await db.scalars(query))


async def upsert_model_endpoint(
    db: AsyncSession,
    *,
    user_id: int,
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
    row = await db.scalar(
        select(ModelEndpoint).where(
            ModelEndpoint.user_id == user_id,
            ModelEndpoint.agent == agent,
        )
    )
    if row is None:
        row = ModelEndpoint(user_id=user_id, agent=agent)
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


async def delete_model_endpoint(
    db: AsyncSession, *, user_id: int, agent: str
) -> bool:
    """删掉一行的覆盖值（＝恢复出厂默认），返回是否真删了。"""
    result = await db.execute(
        delete(ModelEndpoint).where(
            ModelEndpoint.user_id == user_id,
            ModelEndpoint.agent == agent,
        )
    )
    return bool(result.rowcount)


async def get_user_settings(
    db: AsyncSession, *, user_id: int
) -> UserSetting | None:
    return await db.get(UserSetting, user_id)


async def upsert_user_settings(
    db: AsyncSession,
    *,
    user_id: int,
    repo_view_dir: str | None | object = UNSET,
    gitee_base_branch: str | None | object = UNSET,
    github_base_branch: str | None | object = UNSET,
    gitee_access_token: str | None | object = UNSET,
    gitee_username: str | None | object = UNSET,
    github_access_token: str | None | object = UNSET,
    github_username: str | None | object = UNSET,
) -> UserSetting:
    row = await db.get(UserSetting, user_id)
    if row is None:
        row = UserSetting(user_id=user_id)
        db.add(row)
    for field, value in (
        ("repo_view_dir", repo_view_dir),
        ("gitee_base_branch", gitee_base_branch),
        ("github_base_branch", github_base_branch),
        ("gitee_access_token", gitee_access_token),
        ("gitee_username", gitee_username),
        ("github_access_token", github_access_token),
        ("github_username", github_username),
    ):
        if value is not UNSET:
            setattr(row, field, value)
    row.updated_at = _now()
    await db.flush()
    return row


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
