"""add model_providers

模型提供商目录（界面「提供商」那一栏）：显示名 / 默认地址 / 分组 / 排序 / 启停 /
是否拉实时模型列表 / 拉列表的路径与鉴权头 / 内置候选清单。**配置的轴仍是智能体**
（见 ``model_endpoints``），这张表只决定界面怎么挑、以及去哪拉模型列表。

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-10-13 02:20:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: str | Sequence[str] | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'model_providers',
        sa.Column('provider', sa.String(length=32), nullable=False,
                  comment='稳定键（deepseek / kimi / custom …）'),
        sa.Column('display_name', sa.Text(), nullable=False, comment='界面显示名'),
        sa.Column('default_base_url', sa.Text(), nullable=True,
                  comment='选中它时带出的地址（可改；空＝要用户自己填）'),
        sa.Column('ui_group', sa.Text(), nullable=True, comment='下拉里的分组名'),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False,
                  comment='组内排序（小的在前）'),
        sa.Column('status', sa.Integer(), server_default='1', nullable=False,
                  comment='1 启用 / 0 停用（界面不出现）'),
        sa.Column('is_dynamic_model_list', sa.Boolean(), server_default=sa.true(),
                  nullable=False,
                  comment='true＝界面给「测试连接」按钮去拉实时列表；false＝直接用候选清单'),
        sa.Column('models_path', sa.Text(), nullable=True,
                  comment='拉列表的路径（默认 /models；有的网关是 /v1/models）'),
        sa.Column('auth_header', sa.Text(), nullable=True, comment='鉴权头名（默认 Authorization）'),
        sa.Column('auth_prefix', sa.Text(), nullable=True,
                  comment="鉴权头前缀（默认 'Bearer '）"),
        sa.Column('default_models', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='内置候选模型清单（拉不到实时列表时给用户选）'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False, comment='更新时间'),
        sa.PrimaryKeyConstraint('provider'),
        comment='模型提供商目录：界面「提供商」那一栏 + 拉模型列表的方式',
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 目录可以从 config/model_providers.toml 重新灌，但用户后来在库里改过的值会丢。
    op.drop_table('model_providers')
