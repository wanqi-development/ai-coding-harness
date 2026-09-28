"""add sandbox_env to sessions

Revision ID: d1e2b3c4a5f6
Revises: c7d1a54e8f21
Create Date: 2026-09-27 21:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd1e2b3c4a5f6'
down_revision: str | Sequence[str] | None = 'c7d1a54e8f21'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 用户提供的项目配置（键值）：可空，老会话零迁移 —— 没填就等于没有这一份。
    op.add_column(
        'sessions',
        sa.Column(
            'sandbox_env',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='用户提供的被测项目配置（键值，注入沙箱环境变量；明文，与令牌同口径）',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sessions', 'sandbox_env')
