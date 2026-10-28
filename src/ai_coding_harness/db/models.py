"""业务表 ORM 模型。

- ``AgentSession``（表 ``sessions``）：一次会话。
- ``Repository``（表 ``repositories``）：会话关联的仓库（1:1，``session_id`` 为外键）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import (
    true as sa_true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_coding_harness.db.base import Base

_AUTO_BIGINT = BigInteger().with_variant(Integer, "sqlite")


class AgentSession(Base):
    """会话表：一次用户会话，关联一个仓库、一个沙箱。"""

    __tablename__ = "sessions"
    __table_args__: ClassVar[dict[str, str]] = {"comment": "会话表：一次用户会话"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="内部主键（自增）"
    )
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        unique=True,
        comment="对外会话 ID（uuid，应用生成，用于 API / 卷名 / 线程名）",
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="所属登录用户；旧会话在认证迁移后由 owner 认领",
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active", comment="状态：active / destroyed / expired"
    )
    gitee_token: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="会话 git 令牌（Gitee / GitHub 通用）"
    )
    gitee_username: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="git 认证用户名（oauth2 / x-access-token / 平台用户名）",
    )
    git_platform: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="gitee",
        comment="代码平台：gitee / github（见 codeplatform.Platform）",
    )
    credential_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
        comment="用户代码平台凭据版本；沙箱与 Session 缓存按它做懒同步",
    )
    session_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
        comment="会话快照版本；每次会话元数据或凭据变化时递增",
    )
    sandbox_id: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="OpenSandbox 沙箱 ID"
    )
    workspace_volume: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="工作区卷名"
    )
    workspace_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="pending",
        comment="代码工作区状态：pending / provisioning / ready / failed",
    )
    workspace_error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="代码工作区失败原因（原始异常文本，仅运维看）"
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True, comment="会话标题")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近活动时间"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="过期时间"
    )

    repository: Mapped[Repository | None] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class Repository(Base):
    """仓库表：一个会话关联一个仓库（``UNIQUE(session_id)``）。"""

    __tablename__ = "repositories"
    __table_args__: ClassVar[dict[str, str]] = {
        "comment": "会话关联的仓库表（1:1）"
    }

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="所属会话 ID（外键 → sessions.id）",
    )
    owner: Mapped[str] = mapped_column(Text, nullable=False, comment="仓库所属者")
    name: Mapped[str] = mapped_column(Text, nullable=False, comment="仓库名")
    url: Mapped[str] = mapped_column(Text, nullable=False, comment="仓库地址")
    platform: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="gitee",
        comment="代码平台：gitee / github；仓库长期数据按平台 + owner + name 共享",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )

    session: Mapped[AgentSession] = relationship(back_populates="repository")


class User(Base):
    """平台用户主体；认证标识与角色由平台维护。"""

    __tablename__ = "users"
    __table_args__ = (
        Index("users_active_role_idx", "is_active", "role"),
        {"comment": "平台用户：登录主体、角色与启停状态"},
    )

    id: Mapped[int] = mapped_column(
        _AUTO_BIGINT, primary_key=True, autoincrement=True, comment="主键"
    )
    username: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True, comment="归一化后的登录名"
    )
    display_name: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="显示名"
    )
    role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="admin", comment="角色：admin / member"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_true(), comment="是否允许登录"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class LocalCredential(Base):
    """本地密码凭据；未来切外部 IdP 时可以不参与认证。"""

    __tablename__ = "local_credentials"
    __table_args__: ClassVar[dict[str, str]] = {"comment": "本地用户名密码凭据"}

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="用户主键（1:1）",
    )
    password_hash: Mapped[str] = mapped_column(
        Text, nullable=False, comment="scrypt 密码哈希"
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", comment="下次登录是否强制改密"
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", comment="连续登录失败次数"
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="短时锁定截止时间"
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="密码最近修改时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class UserIdentity(Base):
    """外部身份映射；本地登录不需要写这张表。"""

    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider_key",
            "issuer",
            "subject",
            name="user_identities_provider_subject_uq",
        ),
        Index("user_identities_user_idx", "user_id"),
        {"comment": "用户与 OIDC / SAML 等外部身份提供商的映射"},
    )

    id: Mapped[int] = mapped_column(
        _AUTO_BIGINT, primary_key=True, autoincrement=True, comment="主键"
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="平台用户",
    )
    provider_key: Mapped[str] = mapped_column(
        Text, nullable=False, comment="提供商标识"
    )
    issuer: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="外部 issuer"
    )
    subject: Mapped[str] = mapped_column(
        Text, nullable=False, comment="外部稳定 subject"
    )
    username: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="外部登录名"
    )
    email: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="外部邮箱"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近登录时间"
    )


class AuthSession(Base):
    """平台登录会话；Cookie 只保存随机 token，表中只存哈希。"""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("auth_sessions_user_active_idx", "user_id", "revoked_at", "expires_at"),
        {"comment": "平台登录会话（多 worker 共享）"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, comment="会话主键"
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户",
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, comment="登录 token 的 SHA-256"
    )
    csrf_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="CSRF token 的 SHA-256"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="最近访问时间"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="绝对过期时间"
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="撤销时间"
    )
    ip: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="登录 IP"
    )
    user_agent: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="登录 User-Agent"
    )


class Plan(Base):
    """方案表：planner 产出的分步骤方案，供 coder 取用执行。"""

    __tablename__ = "plans"
    __table_args__ = (
        Index("plans_session_status_idx", "session_id", "status"),
        {"comment": "方案表：planner 产出的实施方案"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属会话 ID（外键 → sessions.id）",
    )
    owner: Mapped[str] = mapped_column(Text, nullable=False, comment="仓库 owner（冗余）")
    repo: Mapped[str] = mapped_column(Text, nullable=False, comment="仓库名（冗余）")
    title: Mapped[str | None] = mapped_column(Text, nullable=True, comment="方案标题")
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="方案全文（Markdown，含分步骤）"
    )
    steps: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="结构化步骤数组：[{index, goal, files, done_criteria}]",
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="pending",
        comment="状态：pending（未执行）/ executing / done / failed",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="开始执行时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="执行完成时间"
    )
    pull_requests: Mapped[list[PullRequest]] = relationship(
        back_populates="plan", passive_deletes=True
    )


class PullRequest(Base):
    """代码平台 PR 的稳定镜像，也是“是否由 Agent 创建”的权威归属表。"""

    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "repo_owner",
            "repo_name",
            "number",
            name="pull_requests_repo_number_uq",
        ),
        Index("pull_requests_repo_state_idx", "platform", "repo_owner", "repo_name", "state"),
        Index("pull_requests_plan_idx", "plan_id"),
        Index("pull_requests_session_idx", "session_id"),
        {"comment": "PR 镜像：归属、状态、方案关联与工作区同步水位"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    session_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        comment="首次登记该 PR 的会话；会话删除后保留 PR 审计",
    )
    plan_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("plans.id", ondelete="SET NULL"),
        nullable=True,
        comment="产出该 PR 的方案；一个方案允许对应多个替代 PR",
    )
    platform: Mapped[str] = mapped_column(
        Text, nullable=False, comment="代码平台：gitee / github"
    )
    repo_owner: Mapped[str] = mapped_column(Text, nullable=False, comment="仓库 owner")
    repo_name: Mapped[str] = mapped_column(Text, nullable=False, comment="仓库名")
    number: Mapped[int] = mapped_column(Integer, nullable=False, comment="平台 PR 编号")
    title: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="PR 标题"
    )
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="unknown", comment="open / merged / closed / unknown"
    )
    url: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="PR 网页链接"
    )
    author: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="PR 创建者登录名"
    )
    head_ref: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="源分支"
    )
    head_sha: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="远端最新 head SHA"
    )
    synced_head_sha: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="最后一次安全同步进沙箱的 head SHA"
    )
    base_ref: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="目标分支"
    )
    source: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="unknown",
        comment="归属：agent / legacy_agent / external / unknown",
    )
    remote_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="平台记录创建时间"
    )
    remote_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="平台记录更新时间"
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="最近一次从平台刷新该行的时间",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    plan: Mapped[Plan | None] = relationship(back_populates="pull_requests")


class SessionRecord(Base):
    """会话记录（基本信息）：用于页面时间线/列表展示的轻量事件表。

    垂直分表：本表存「轻」字段（类型/摘要/时间），详情在 ``session_record_details``。
    """

    __tablename__ = "session_records"
    __table_args__ = (
        Index("session_records_session_time_idx", "session_id", "created_at"),
        {"comment": "会话记录（基本信息）：页面展示用的事件时间线"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属会话 ID（外键 → sessions.id）",
    )
    type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="记录类型：user / assistant / plan / review / pr / system",
    )
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="摘要（列表展示用，短）"
    )
    ref_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="关联业务对象 ID（如 plans.id），按 type 解释"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )


class SessionRecordDetail(Base):
    """会话记录详情：存「重」字段（完整内容 / 结构化附加），1:1 关联 ``session_records``。"""

    __tablename__ = "session_record_details"
    __table_args__: ClassVar[dict[str, str]] = {
        "comment": "会话记录详情（垂直分表的重字段）"
    }

    record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("session_records.id", ondelete="CASCADE"),
        primary_key=True,
        comment="记录 ID（外键 → session_records.id，1:1）",
    )
    content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="完整内容（聊天文本 / 方案全文 / 审查报告）"
    )
    extra: Mapped[dict | list | None] = mapped_column(
        JSONB, nullable=True, comment="结构化附加（方案步骤 / findings / PR 链接等）"
    )


class ModelEndpoint(Base):
    """模型连接表：**按智能体**存一套连接（2026-10-13）。

    ``agent`` 是稳定键，与代码里的形态名对齐：

    * ``qa`` / ``analysis`` / ``plan`` / ``review`` / ``coding``：五个主控形态；
      三个子智能体（``planner`` / ``coder`` / ``reviewer``）**跟 ``coding`` 那一行**
      （它们只在 coding 形态下被派出去）；
    * ``unified``：所有智能体共用它（填了就它优先）；
    * ``fallback``：主模型重试耗尽后换它再试一次（留空＝没有兜底）；
    * ``classifier``：意图识别里「大模型」那一关用的模型；
    * ``intent_mode``：**不装连接**，只在 ``model_name`` 里存 ``jev`` / ``llm``
      （意图识别走哪一关；见 ``server/intent.py``）。

    **明文存 ``api_key``**：与 ``sessions`` 表那两个 git token 同口径（本地场景、单机自用）。
    接口只回打码值，写回时留空＝不修改；DB 里的 key 会一并进脱敏词表（``redact.secret_terms``）。

    ``context_window`` 为 0 ＝ 用户没填：解析时按「先读到的 → 128k 保守值」兜
    （见 ``agent/llm.resolve_window``）。
    """

    __tablename__ = "model_endpoints"
    # 用元组包一层：与 ``plans`` 那张表同形，也免得 dict 默认值引出 RUF012（既有告警别新增）
    __table_args__ = (
        Index("model_endpoints_user_idx", "user_id"),
        {"comment": "模型连接表：按智能体存 base_url / 模型 / key / 窗口"},
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="所属用户",
    )
    agent: Mapped[str] = mapped_column(
        String(32), primary_key=True, comment="智能体键（形态名 / unified / fallback / …）"
    )
    base_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="OpenAI 兼容入口（留空＝用出厂默认）"
    )
    model_name: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="模型名（留空＝用出厂默认）"
    )
    api_key: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="令牌（明文，与 git token 同口径；接口只回打码值）"
    )
    context_window: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
        comment="上下文窗口（tokens）；0 ＝ 没填，按「读到 / 128k」兜",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class UserSetting(Base):
    """用户级运行配置：代码缓存、默认分支与代码平台凭据。"""

    __tablename__ = "user_settings"
    __table_args__ = ({"comment": "用户级代码缓存、默认分支与代码平台凭据"},)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="所属用户",
    )
    repo_view_dir: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="代码缓存根目录；空＝~/.ai-coding-harness/users/<user_id>/repo_cache",
    )
    gitee_base_branch: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Gitee 默认分支；空＝按远程探测"
    )
    github_base_branch: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="GitHub 默认分支；空＝按远程探测"
    )
    gitee_access_token: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="当前用户的 Gitee access token"
    )
    gitee_username: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="当前用户的 Gitee git 用户名"
    )
    github_access_token: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="当前用户的 GitHub access token"
    )
    github_username: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="当前用户的 GitHub git 用户名"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class ModelProvider(Base):
    """模型提供商目录（界面「提供商」那一栏，2026-10-13 加）。

    **配置的轴仍然是智能体**（见 ``ModelEndpoint``）：这张表只管「界面上挑哪一家、选中带出
    哪个地址、拉模型列表时去哪拉、怎么鉴权、拉不到时给什么候选」——它不决定谁在用哪个模型。

    启动时读进 ``Settings.providers``；**表为空时**用 ``config/model_providers.toml`` 灌一次
    （之后以库为准 —— 改种子文件不会覆盖已有行，要重新灌走 ``POST /model-config/providers/reload``）。
    """

    __tablename__ = "model_providers"
    __table_args__ = (
        {"comment": "模型提供商目录：界面「提供商」那一栏 + 拉模型列表的方式"},
    )

    provider: Mapped[str] = mapped_column(
        String(32), primary_key=True, comment="稳定键（deepseek / kimi / custom …）"
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False, comment="界面显示名")
    default_base_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="选中它时带出的地址（可改；空＝要用户自己填）"
    )
    ui_group: Mapped[str | None] = mapped_column(Text, nullable=True, comment="下拉里的分组名")
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", comment="组内排序（小的在前）"
    )
    status: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", comment="1 启用 / 0 停用（界面不出现）"
    )
    is_dynamic_model_list: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_true(),
        comment="true＝界面给「测试连接」按钮去拉实时列表；false＝直接用候选清单",
    )
    models_path: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="拉列表的路径（默认 /models；有的网关是 /v1/models）"
    )
    auth_header: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="鉴权头名（默认 Authorization）"
    )
    auth_prefix: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="鉴权头前缀（默认 'Bearer '）"
    )
    default_models: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="**已废弃**（2026-10-15）：内置候选清单不再被读写，列留着免得迁移（模型名以「刷新清单」拉到的那份为准）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class AppSetting(Base):
    """**可以改完立刻生效**的运行期配置（2026-10-14 加）。

    键与类型是固定的那一小撮，定义只在 ``config.RUNTIME_SETTINGS`` 一处（界面上的分组、
    中文名、落到 ``Settings`` 哪个字段都在那儿）—— 这张表只存值，不认识的东西不进库。

    值的语义：**整行覆盖**。库里没有这一行 ＝ 用 ``.env`` / 代码默认值；有这一行就用它
    （取回来时会赋到 ``Settings`` 实例上，读那些字段的地方一行都不用改）。
    """

    __tablename__ = "app_settings"
    __table_args__ = ({"comment": "运行期配置：键值对，改完立刻生效（不用重启）"},)

    key: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="稳定键（见 config.RUNTIME_SETTINGS）"
    )
    value: Mapped[Any | None] = mapped_column(
        JSONB, nullable=True, comment="值（字符串 / 整数 / 小数；类型由键的定义决定）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class ProviderModel(Base):
    """**供应商的模型详情**（2026-10-14 加）：这家有哪些模型、各是哪一类、上下文多长。

    为什么单开一张表而不是在供应商那行塞一串名字（原来那个 ``default_models``）：**上下文长度
    是模型的属性，不是供应商的属性**（同一家 ``glm-5.3`` 是 1M、``glm-4.6`` 是 128k），
    窗口按模型查才不会把压缩线推错地方；而且名字一旦有了属性（类型 / 窗口 / 启停），它就是个实体。

    数据从哪来：① 「刷新模型清单」按供应商去调它的清单接口（``source=probe``）；
    ② 界面手填 / 手改（``source=manual``）；③ 供应商种子文件里那串名字灌进来（``source=seed``）。
    **只有点了按钮才刷**（用户口径：不主动刷新、保存时也不刷）。
    """

    __tablename__ = "provider_models"
    __table_args__ = ({"comment": "供应商的模型详情：名字 / 类型 / 上下文长度"},)

    provider: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("model_providers.provider", ondelete="CASCADE"),
        primary_key=True,
        comment="归属供应商（稳定键，指向 model_providers.provider）",
    )
    model: Mapped[str] = mapped_column(Text, primary_key=True, comment="模型名（请求里用的那个串）")
    model_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="chat",
        comment="类型：chat / embedding / rerank / image / video / audio / other（见 model_types.py）",
    )
    context_window: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", comment="上下文长度（tokens）；0 ＝ 还不知道"
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="manual",
        comment="这个窗口/类型哪来的：probe（刷新时读到的）/ manual / seed",
    )
    status: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", comment="1 启用 / 0 停用（界面下拉不列停用的）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class Clarification(Base):
    """一条**待回答**的意图澄清（每个会话最多一条）。

    只服务“qa 先问、前端弹选项”这一条窄路径：

    - ``original_message`` 是触发歧义的那句用户原话；用户选中候选后要用它去执行，
      不能拿“第一个”去执行；
    - ``options`` 是 qa 输出的结构化候选（intent/label/description）；
    - 任何新的用户消息都会先删掉本会话这一行；结构化选择则由服务层消费。

    不存 ``consumed_at``：消费就是删除。这样不会出现“已消费但忘记过滤”的脏状态。
    """

    __tablename__ = "clarifications"
    __table_args__ = (
        {"comment": "待回答的意图澄清：每个会话最多一条，下一条用户消息即失效"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="澄清 ID（uuid 字符串，前端回传）"
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="所属会话 ID（外键 → sessions.id；一个会话最多一条）",
    )
    original_message: Mapped[str] = mapped_column(
        Text, nullable=False, comment="触发澄清的用户原话（选中后用它执行）"
    )
    question: Mapped[str] = mapped_column(Text, nullable=False, comment="qa 生成的澄清问题")
    options: Mapped[list] = mapped_column(
        JSONB, nullable=False, comment="候选：[{intent,label,description}]，顺序即展示顺序"
    )
    steps: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default="[]",
        comment="多步澄清：[{id,question,options}]；旧记录为空，读取时回退 question/options",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )
