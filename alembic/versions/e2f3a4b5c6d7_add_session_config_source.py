"""add config_source to sessions

Revision ID: e2f3a4b5c6d7
Revises: d1e2b3c4a5f6
Create Date: 2026-09-28 09:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: str | Sequence[str] | None = 'd1e2b3c4a5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 远端配置来源（Nacos 地址 / 命名空间 / 分组 / dataId）：可空。
    # 空 = 全用平台默认（namespace 按 <平台>__<owner> 推、group 按仓库名、dataId 固定）。
    op.add_column(
        'sessions',
        sa.Column(
            'config_source',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='远端配置来源（Nacos 地址 / 命名空间 / 分组 / dataId；空 = 全用平台默认）',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sessions', 'config_source')
