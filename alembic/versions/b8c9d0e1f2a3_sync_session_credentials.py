"""Align existing session credential snapshots with their owning user.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Before this feature every session token came from one global setting. Only the
    # administrator inherited that setting in a7b8c9d0e1f2, so other users' session
    # snapshots must not retain the old shared token.
    op.execute(
        """
        UPDATE sessions AS s
        SET
            gitee_token = us.gitee_access_token,
            gitee_username = COALESCE(us.gitee_username, ''),
            credential_version = credential_version + 1,
            session_version = session_version + 1
        FROM user_settings AS us
        WHERE s.user_id = us.user_id
        """
    )
    op.execute(
        """
        UPDATE sessions AS s
        SET
            gitee_token = NULL,
            gitee_username = '',
            credential_version = credential_version + 1,
            session_version = session_version + 1
        WHERE s.user_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM user_settings AS us WHERE us.user_id = s.user_id
          )
        """
    )
    op.execute(
        """
        UPDATE sessions AS s
        SET
            gitee_token = us.gitee_access_token,
            gitee_username = COALESCE(us.gitee_username, ''),
            credential_version = credential_version + 1,
            session_version = session_version + 1
        FROM user_settings AS us
        WHERE s.user_id IS NULL
          AND us.user_id = (
              SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1
          )
        """
    )


def downgrade() -> None:
    # Credential snapshots are derived data; there is no safe global value to restore.
    pass
