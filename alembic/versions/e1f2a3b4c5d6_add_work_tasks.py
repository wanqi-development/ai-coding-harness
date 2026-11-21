"""Add work tasks and immutable coder submissions.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("requirement_summary", sa.Text(), nullable=False),
        sa.Column("current_summary", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("branch", sa.Text(), server_default="", nullable=False),
        sa.Column("base_ref", sa.Text(), server_default="", nullable=False),
        sa.Column("latest_commit_sha", sa.Text(), server_default="", nullable=False),
        sa.Column("latest_head_sha", sa.Text(), server_default="", nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["pull_request_id"], ["pull_requests.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="代码任务：主控维护的累计需求、分支和交付状态",
    )
    op.create_index(
        "work_tasks_session_status_idx", "work_tasks", ["session_id", "status"]
    )
    op.create_index("work_tasks_pr_idx", "work_tasks", ["pull_request_id"])

    op.create_table(
        "task_submissions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("base_ref", sa.Text(), server_default="", nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("changed_files", sa.JSON(), nullable=True),
        sa.Column("verification", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["task_id"], ["work_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "commit_sha", name="task_submissions_task_commit_uq"
        ),
        comment="代码任务提交：coder 每次 commit/push 的不可变记录",
    )
    op.create_index(
        "task_submissions_task_created_idx",
        "task_submissions",
        ["task_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "task_submissions_task_created_idx", table_name="task_submissions"
    )
    op.drop_table("task_submissions")
    op.drop_index("work_tasks_pr_idx", table_name="work_tasks")
    op.drop_index("work_tasks_session_status_idx", table_name="work_tasks")
    op.drop_table("work_tasks")
