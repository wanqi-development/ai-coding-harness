"""add git_platform to sessions

Revision ID: c7d1a54e8f21
Revises: b41d7e2c9f30
Create Date: 2026-09-27 18:20:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7d1a54e8f21'
down_revision: str | Sequence[str] | None = 'b41d7e2c9f30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default 让存量会话直接落 'gitee'：老会话都是 Gitee 建的，零数据迁移。
    op.add_column(
        'sessions',
        sa.Column(
            'git_platform',
            sa.Text(),
            server_default='gitee',
            nullable=False,
            comment='代码平台：gitee / github（见 codeplatform.Platform）',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sessions', 'git_platform')
