"""Add independent versioned code reviews and findings.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("session_id", sa.BigInteger(), nullable=False, comment="所属会话"),
        sa.Column("plan_id", sa.BigInteger(), nullable=True, comment="关联方案"),
        sa.Column(
            "pull_request_id", sa.BigInteger(), nullable=True, comment="PR 阶段审查关联的 PR"
        ),
        sa.Column(
            "stage", sa.Text(), server_default="local", nullable=False, comment="local / pr"
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default="running",
            nullable=False,
            comment="running / completed / failed / stale / cancelled",
        ),
        sa.Column("base_ref", sa.Text(), server_default="", nullable=False),
        sa.Column("base_sha", sa.Text(), server_default="", nullable=False),
        sa.Column("head_ref", sa.Text(), server_default="", nullable=False),
        sa.Column("head_sha", sa.Text(), server_default="", nullable=False),
        sa.Column("code_revision", sa.Text(), server_default="", nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("report_markdown", sa.Text(), server_default="", nullable=False),
        sa.Column("reviewer_agent", sa.Text(), server_default="reviewer", nullable=False),
        sa.Column("provider", sa.Text(), server_default="", nullable=False),
        sa.Column("model_name", sa.Text(), server_default="", nullable=False),
        sa.Column("rules_version", sa.Text(), server_default="v1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["pull_request_id"], ["pull_requests.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="独立代码审查：绑定代码版本，可为本地候选或 PR 最终版本",
    )
    op.create_index("reviews_session_created_idx", "reviews", ["session_id", "created_at"])
    op.create_index("reviews_pull_idx", "reviews", ["pull_request_id"])
    op.create_index("reviews_revision_idx", "reviews", ["code_revision"])

    op.create_table(
        "review_findings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("review_id", sa.BigInteger(), nullable=False, comment="所属审查"),
        sa.Column("severity", sa.String(length=8), nullable=False, comment="P0 / P1 / P2 / P3"),
        sa.Column("file_path", sa.Text(), server_default="", nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="代码审查问题：P0-P3、位置与处理状态",
    )
    op.create_index("review_findings_review_idx", "review_findings", ["review_id"])
    op.create_index("review_findings_status_idx", "review_findings", ["status"])


def downgrade() -> None:
    op.drop_index("review_findings_status_idx", table_name="review_findings")
    op.drop_index("review_findings_review_idx", table_name="review_findings")
    op.drop_table("review_findings")
    op.drop_index("reviews_revision_idx", table_name="reviews")
    op.drop_index("reviews_pull_idx", table_name="reviews")
    op.drop_index("reviews_session_created_idx", table_name="reviews")
    op.drop_table("reviews")
