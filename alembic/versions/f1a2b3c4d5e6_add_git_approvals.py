"""Add interrupt-style Git command approvals.

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "git_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), server_default="", nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=64), server_default="", nullable=False),
        sa.Column("risk", sa.String(length=64), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("choice", sa.String(length=32), nullable=True),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "request_hash",
            name="git_approvals_session_request_uq",
        ),
        comment="Git 危险命令的一次性中断审批",
    )
    op.create_index(
        "git_approvals_session_status_idx",
        "git_approvals",
        ["session_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "git_approvals_session_status_idx",
        table_name="git_approvals",
    )
    op.drop_table("git_approvals")
