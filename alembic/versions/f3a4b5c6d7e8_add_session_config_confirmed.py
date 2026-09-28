"""add config_confirmed_at to sessions

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-28 10:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: str | Sequence[str] | None = 'e2f3a4b5c6d7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 用户确认过项目配置的时刻（含「这个项目不需要配置」这一次确认）。
    # 空 = 还没确认过：coding / rework 轮次开始前会拦下并弹框（见 service._pending_config_request）。
    op.add_column(
        'sessions',
        sa.Column(
            'config_confirmed_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='用户确认过项目配置的时刻（含「这个项目不需要配置」）；空 = 还没确认过',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sessions', 'config_confirmed_at')
