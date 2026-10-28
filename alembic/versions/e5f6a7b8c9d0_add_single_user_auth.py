"""add single-user authentication tables

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), server_default="", nullable=False),
        sa.Column("role", sa.Text(), server_default="admin", nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        comment="平台用户：登录主体、角色与启停状态",
    )
    op.create_index("users_active_role_idx", "users", ["is_active", "role"])

    op.create_table(
        "local_credentials",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "failed_login_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        comment="本地用户名密码凭据",
    )

    op.create_table(
        "user_identities",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_key", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), server_default="", nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), server_default="", nullable=False),
        sa.Column("email", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_key",
            "issuer",
            "subject",
            name="user_identities_provider_subject_uq",
        ),
        comment="用户与 OIDC / SAML 等外部身份提供商的映射",
    )
    op.create_index("user_identities_user_idx", "user_identities", ["user_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.Text(), server_default="", nullable=False),
        sa.Column("user_agent", sa.Text(), server_default="", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        comment="平台登录会话（多 worker 共享）",
    )
    op.create_index(
        "auth_sessions_user_active_idx",
        "auth_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )
    op.add_column("sessions", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "sessions_user_id_fkey",
        "sessions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_constraint("sessions_user_id_fkey", "sessions", type_="foreignkey")
    op.drop_column("sessions", "user_id")
    op.drop_index("auth_sessions_user_active_idx", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("user_identities_user_idx", table_name="user_identities")
    op.drop_table("user_identities")
    op.drop_table("local_credentials")
    op.drop_index("users_active_role_idx", table_name="users")
    op.drop_table("users")
