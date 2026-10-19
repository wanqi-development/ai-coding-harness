"""待回答的意图澄清：每个会话最多一条，下一条用户消息即失效。

Revision ID: a1b2c3d4e5f6
Revises: f4a5b6c7d8e9
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "clarifications",
        sa.Column(
            "id",
            sa.String(length=36),
            nullable=False,
            comment="澄清 ID（uuid 字符串，前端回传）",
        ),
        sa.Column(
            "session_id",
            sa.BigInteger(),
            nullable=False,
            comment="所属会话 ID（外键 → sessions.id；一个会话最多一条）",
        ),
        sa.Column(
            "original_message",
            sa.Text(),
            nullable=False,
            comment="触发澄清的用户原话（选中后用它执行）",
        ),
        sa.Column(
            "question",
            sa.Text(),
            nullable=False,
            comment="qa 生成的澄清问题",
        ),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="候选：[{intent,label,description}]，顺序即展示顺序",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时间",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
        comment="待回答的意图澄清：每个会话最多一条，下一条用户消息即失效",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("clarifications")
