"""add model_endpoints

按**智能体**存模型连接（base_url / 模型名 / key / 窗口）：5 个主控形态各一行，外加
``unified``（统一模型）/ ``fallback``（兜底）/ ``classifier``（意图识别的大模型那一关）
与 ``intent_mode``（意图识别走 JEV 还是大模型）。原先是「按供应商」的映射，凭据只在 .env。

Revision ID: c1d2e3f4a5b6
Revises: b5c6d7e8f9a0
Create Date: 2026-10-13 01:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: str | Sequence[str] | None = 'b5c6d7e8f9a0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'model_endpoints',
        sa.Column('agent', sa.String(length=32), nullable=False,
                  comment='智能体键（形态名 / unified / fallback / …）'),
        sa.Column('base_url', sa.Text(), nullable=True,
                  comment='OpenAI 兼容入口（留空＝用出厂默认）'),
        sa.Column('model_name', sa.Text(), nullable=True,
                  comment='模型名（留空＝用出厂默认）'),
        sa.Column('api_key', sa.Text(), nullable=True,
                  comment='令牌（明文，与 git token 同口径；接口只回打码值）'),
        sa.Column('context_window', sa.Integer(), server_default='0', nullable=False,
                  comment='上下文窗口（tokens）；0 ＝ 没填，按「读到 / 128k」兜'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False, comment='更新时间'),
        sa.PrimaryKeyConstraint('agent'),
        comment='模型连接表：按智能体存 base_url / 模型 / key / 窗口',
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 这几行是用户当场填的凭据，drop 就是丢数据（.env 里那份仍是可用兜底）。
    op.drop_table('model_endpoints')
