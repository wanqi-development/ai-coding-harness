"""add provider_models

供应商的模型详情（名字 / 类型 / 上下文长度 / 来源 / 启停）。

**顺手把老数据搬过来**：`model_providers.default_models` 那串名字（原来只存名字）逐条灌进新表，
类型一律 `chat`、窗口未知记 0、来源 `seed` —— 这一列的东西不该丢，它是用户手写的清单。
搬完**不删那一列**（回滚要能回去），但它从此不再被读（界面上以新表为准）。

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-14 22:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4a5b6c7d8e9'
down_revision: str | Sequence[str] | None = 'e3f4a5b6c7d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'provider_models',
        sa.Column('provider', sa.String(length=32), nullable=False,
                  comment='归属供应商（稳定键，指向 model_providers.provider）'),
        sa.Column('model', sa.Text(), nullable=False, comment='模型名（请求里用的那个串）'),
        sa.Column('model_type', sa.String(length=16), server_default='chat', nullable=False,
                  comment='类型：chat / embedding / rerank / image / video / audio / other'),
        sa.Column('context_window', sa.Integer(), server_default='0', nullable=False,
                  comment='上下文长度（tokens）；0 ＝ 还不知道'),
        sa.Column('source', sa.String(length=16), server_default='manual', nullable=False,
                  comment='这个窗口/类型哪来的：probe / manual / seed'),
        sa.Column('status', sa.Integer(), server_default='1', nullable=False,
                  comment='1 启用 / 0 停用（界面下拉不列停用的）'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False, comment='更新时间'),
        sa.ForeignKeyConstraint(['provider'], ['model_providers.provider'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('provider', 'model'),
        comment='供应商的模型详情：名字 / 类型 / 上下文长度',
    )

    # 老列里那串名字逐条搬过来（jsonb 数组 → 多行）。空/缺的跳过。
    op.execute(
        """
        INSERT INTO provider_models (provider, model, model_type, context_window, source, status)
        SELECT p.provider, name, 'chat', 0, 'seed', 1
        FROM model_providers AS p,
             LATERAL jsonb_array_elements_text(
                 CASE WHEN jsonb_typeof(p.default_models) = 'array'
                      THEN p.default_models ELSE '[]'::jsonb END
             ) AS name
        WHERE name IS NOT NULL AND name <> ''
        ON CONFLICT (provider, model) DO NOTHING
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 手填/探测到的窗口只在这张表里（`default_models` 那列搬完就没再更新），删表会丢。
    op.drop_table('provider_models')
