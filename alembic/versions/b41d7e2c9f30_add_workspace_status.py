"""add workspace status to sessions

Revision ID: b41d7e2c9f30
Revises: 8cc9adc2c4ba
Create Date: 2026-09-17 16:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b41d7e2c9f30'
down_revision: str | Sequence[str] | None = '8cc9adc2c4ba'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'sessions',
        sa.Column(
            'workspace_status',
            sa.Text(),
            server_default='pending',
            nullable=False,
            comment='代码工作区状态：pending / provisioning / ready / failed',
        ),
    )
    op.add_column(
        'sessions',
        sa.Column(
            'workspace_error',
            sa.Text(),
            nullable=True,
            comment='代码工作区失败原因（原始异常文本，仅运维看）',
        ),
    )
    # 存量会话的工作区早已就绪（沙箱是按旧同步流程建完才返回 session_id 的），
    # 但那次建的容器此刻可能已被回收——标 ready 会让前端不触发 ensure 就直接发消息。
    # 统一回落 pending：首次使用时由 ensure_runtime 用 reconnect 优先探一次真身，
    # 既不会漏建，也不会白建。
    op.execute("UPDATE sessions SET workspace_status = 'pending'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sessions', 'workspace_error')
    op.drop_column('sessions', 'workspace_status')
