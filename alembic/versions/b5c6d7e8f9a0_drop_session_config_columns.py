"""drop session config columns

把「项目配置」这套（会话上的那份值 / 远端配置位置 / 确认时刻）整体撤掉 —— 配套代码
已回退，三列不再有任何读写。该功能以后重做时按新设计重新加列，不复活这几列。

Revision ID: b5c6d7e8f9a0
Revises: f3a4b5c6d7e8
Create Date: 2026-09-28 16:20:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b5c6d7e8f9a0'
down_revision: str | Sequence[str] | None = 'f3a4b5c6d7e8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('sessions', 'config_confirmed_at')
    op.drop_column('sessions', 'config_source')
    op.drop_column('sessions', 'sandbox_env')


def downgrade() -> None:
    """Downgrade schema."""
    # 列能加回来，**值回不来**（drop 就是丢数据）—— 这几列是用户当场填的测试库口令一类，
    # 没有别处存底。真要退回旧版本，得让用户重新填一次。
    op.add_column(
        'sessions',
        sa.Column(
            'sandbox_env',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='用户提供的被测项目配置（键值，注入沙箱环境变量；明文，与令牌同口径）',
        ),
    )
    op.add_column(
        'sessions',
        sa.Column(
            'config_source',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='远端配置来源（Nacos 地址 / 命名空间 / 分组 / dataId；空 = 全用平台默认）',
        ),
    )
    op.add_column(
        'sessions',
        sa.Column(
            'config_confirmed_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='用户确认过项目配置的时刻（含「这个项目不需要配置」）；空 = 还没确认过',
        ),
    )
