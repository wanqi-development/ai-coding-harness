"""Move code-platform credentials to users and add session cache versions.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_settings", sa.Column("gitee_access_token", sa.Text(), nullable=True)
    )
    op.add_column("user_settings", sa.Column("gitee_username", sa.Text(), nullable=True))
    op.add_column(
        "user_settings", sa.Column("github_access_token", sa.Text(), nullable=True)
    )
    op.add_column(
        "user_settings", sa.Column("github_username", sa.Text(), nullable=True)
    )

    # Existing deployment-wide credentials belong to the current administrator. Other
    # users must configure their own token; no cross-user fallback is kept.
    op.execute(
        """
        UPDATE user_settings AS us
        SET
            gitee_access_token = (
                SELECT value #>> '{}' FROM app_settings
                WHERE key = 'gitee_access_token'
            ),
            gitee_username = (
                SELECT value #>> '{}' FROM app_settings
                WHERE key = 'gitee_username'
            ),
            github_access_token = (
                SELECT value #>> '{}' FROM app_settings
                WHERE key = 'github_access_token'
            ),
            github_username = (
                SELECT value #>> '{}' FROM app_settings
                WHERE key = 'github_username'
            ),
            updated_at = now()
        WHERE us.user_id = (
            SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1
        )
        """
    )
    op.execute(
        "DELETE FROM app_settings WHERE key IN "
        "('gitee_access_token', 'gitee_username', "
        "'github_access_token', 'github_username')"
    )

    op.add_column(
        "sessions",
        sa.Column(
            "credential_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "session_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "repositories",
        sa.Column(
            "platform",
            sa.Text(),
            nullable=False,
            server_default="gitee",
        ),
    )
    op.execute(
        """
        UPDATE repositories AS r
        SET platform = s.git_platform
        FROM sessions AS s
        WHERE s.id = r.session_id
        """
    )


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'gitee_access_token', to_jsonb(gitee_access_token)
        FROM user_settings
        WHERE gitee_access_token IS NOT NULL
        ORDER BY user_id
        LIMIT 1
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'gitee_username', to_jsonb(gitee_username)
        FROM user_settings
        WHERE gitee_username IS NOT NULL
        ORDER BY user_id
        LIMIT 1
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'github_access_token', to_jsonb(github_access_token)
        FROM user_settings
        WHERE github_access_token IS NOT NULL
        ORDER BY user_id
        LIMIT 1
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'github_username', to_jsonb(github_username)
        FROM user_settings
        WHERE github_username IS NOT NULL
        ORDER BY user_id
        LIMIT 1
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.drop_column("repositories", "platform")
    op.drop_column("sessions", "session_version")
    op.drop_column("sessions", "credential_version")
    op.drop_column("user_settings", "github_username")
    op.drop_column("user_settings", "github_access_token")
    op.drop_column("user_settings", "gitee_username")
    op.drop_column("user_settings", "gitee_access_token")
