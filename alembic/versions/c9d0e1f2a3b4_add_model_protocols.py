"""Add provider protocols and explicit model endpoint providers.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_providers",
        sa.Column(
            "protocol",
            sa.String(length=32),
            nullable=False,
            server_default="openai_compatible",
            comment="运行时协议：openai_compatible / anthropic_messages",
        ),
    )
    op.add_column(
        "model_providers",
        sa.Column(
            "extra_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="模型列表等平台请求的额外请求头",
        ),
    )
    op.add_column(
        "model_endpoints",
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=True,
            comment="提供商键；空＝旧数据，运行时按 base_url 兼容反推",
        ),
    )
    op.execute(
        """
        UPDATE model_providers
        SET
            protocol = 'anthropic_messages',
            default_base_url = COALESCE(
                NULLIF(default_base_url, ''),
                'https://api.anthropic.com'
            ),
            models_path = CASE
                WHEN models_path IS NULL OR models_path = '' OR models_path = '/models'
                THEN '/v1/models'
                ELSE models_path
            END,
            auth_header = 'x-api-key',
            auth_prefix = '',
            extra_headers = COALESCE(
                extra_headers,
                '{"anthropic-version":"2023-06-01"}'::jsonb
            )
        WHERE provider = 'anthropic'
        """
    )


def downgrade() -> None:
    op.drop_column("model_endpoints", "provider")
    op.drop_column("model_providers", "extra_headers")
    op.drop_column("model_providers", "protocol")
