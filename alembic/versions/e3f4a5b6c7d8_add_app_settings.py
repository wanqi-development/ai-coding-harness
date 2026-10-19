"""add app_settings

运行期配置（键值对）：代码缓存路径 / 默认分支 / 上下文告警线与各家窗口。
这些值原本只在 `.env` 里，2026-10-14 起可以在设置页改、**不用重启**（读它们的地方读的是
`Settings` 实例上的字段，保存时直接赋回去）。

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-14 21:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: str | Sequence[str] | None = 'd2e3f4a5b6c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(length=64), nullable=False,
                  comment='稳定键（见 config.RUNTIME_SETTINGS）'),
        sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='值（字符串 / 整数 / 小数；类型由键的定义决定）'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False, comment='更新时间'),
        sa.PrimaryKeyConstraint('key'),
        comment='运行期配置：键值对，改完立刻生效（不用重启）',
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 值就是这几格配置，`.env` 里那份默认值一直在，删表只是回到「只能改 .env」。
    op.drop_table('app_settings')
