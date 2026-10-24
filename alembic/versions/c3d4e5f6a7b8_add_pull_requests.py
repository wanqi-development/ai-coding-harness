"""add pull request registry and plan relation

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=True),
        sa.Column("plan_id", sa.BigInteger(), nullable=True),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("repo_owner", sa.Text(), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("state", sa.Text(), server_default="unknown", nullable=False),
        sa.Column("url", sa.Text(), server_default="", nullable=False),
        sa.Column("author", sa.Text(), server_default="", nullable=False),
        sa.Column("head_ref", sa.Text(), server_default="", nullable=False),
        sa.Column("head_sha", sa.Text(), server_default="", nullable=False),
        sa.Column("synced_head_sha", sa.Text(), nullable=True),
        sa.Column("base_ref", sa.Text(), server_default="", nullable=False),
        sa.Column("source", sa.Text(), server_default="unknown", nullable=False),
        sa.Column("remote_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remote_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
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
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform",
            "repo_owner",
            "repo_name",
            "number",
            name="pull_requests_repo_number_uq",
        ),
        comment="PR 镜像：归属、状态、方案关联与工作区同步水位",
    )
    op.create_index(
        "pull_requests_repo_state_idx",
        "pull_requests",
        ["platform", "repo_owner", "repo_name", "state"],
    )
    op.create_index("pull_requests_plan_idx", "pull_requests", ["plan_id"])
    op.create_index("pull_requests_session_idx", "pull_requests", ["session_id"])


def downgrade() -> None:
    op.drop_index("pull_requests_session_idx", table_name="pull_requests")
    op.drop_index("pull_requests_plan_idx", table_name="pull_requests")
    op.drop_index("pull_requests_repo_state_idx", table_name="pull_requests")
    op.drop_table("pull_requests")
