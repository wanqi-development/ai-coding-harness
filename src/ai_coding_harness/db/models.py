"""业务表 ORM 模型。

- ``AgentSession``（表 ``sessions``）：一次会话。
- ``Repository``（表 ``repositories``）：会话关联的仓库（1:1，``session_id`` 为外键）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_coding_harness.db.base import Base


class AgentSession(Base):
    """会话表：一次用户会话，关联一个仓库、一个沙箱。"""

    __tablename__ = "sessions"
    __table_args__ = {"comment": "会话表：一次用户会话"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="内部主键（自增）"
    )
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        unique=True,
        comment="对外会话 ID（uuid，应用生成，用于 API / 卷名 / 线程名）",
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

    repository: Mapped["Repository | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class Repository(Base):
    """仓库表：一个会话关联一个仓库（``UNIQUE(session_id)``）。"""

    __tablename__ = "repositories"
    __table_args__ = {"comment": "会话关联的仓库表（1:1）"}

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )

    session: Mapped[AgentSession] = relationship(back_populates="repository")


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
    __table_args__ = {"comment": "会话记录详情（垂直分表的重字段）"}

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
