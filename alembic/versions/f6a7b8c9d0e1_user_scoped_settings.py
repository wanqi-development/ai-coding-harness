"""move user-facing settings and model endpoints to user scope

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("repo_view_dir", sa.Text(), nullable=True),
        sa.Column("gitee_base_branch", sa.Text(), nullable=True),
        sa.Column("github_base_branch", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        comment="用户级代码缓存路径与默认分支",
    )

    # Existing single-user settings belong to the current administrator. New users start
    # with service defaults and configure their own values in the personal settings page.
    op.execute(
        sa.text(
            """
            INSERT INTO user_settings
                (user_id, repo_view_dir, gitee_base_branch, github_base_branch)
            SELECT
                u.id,
                (SELECT value #>> '{}' FROM app_settings WHERE key = 'repo_view_dir'),
                (SELECT value #>> '{}' FROM app_settings WHERE key = 'gitee_base_branch'),
                (SELECT value #>> '{}' FROM app_settings WHERE key = 'github_base_branch')
            FROM users u
            WHERE u.role = 'admin'
            ORDER BY u.id
            LIMIT 1
            """
        )
    )
    op.execute(
        "DELETE FROM app_settings WHERE key IN "
        "('repo_view_dir', 'gitee_base_branch', 'github_base_branch')"
    )

    op.add_column("model_endpoints", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE model_endpoints
        SET user_id = (
            SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1
        )
        WHERE user_id IS NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM model_endpoints WHERE user_id IS NULL) THEN
                RAISE EXCEPTION 'model_endpoints has rows but no administrator user exists';
            END IF;
        END $$;
        """
    )
    op.drop_constraint("model_endpoints_pkey", "model_endpoints", type_="primary")
    op.alter_column("model_endpoints", "user_id", nullable=False)
    op.create_primary_key(
        "model_endpoints_pkey", "model_endpoints", ["user_id", "agent"]
    )
    op.create_foreign_key(
        "model_endpoints_user_id_fkey",
        "model_endpoints",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("model_endpoints_user_idx", "model_endpoints", ["user_id"])


def downgrade() -> None:
    op.drop_index("model_endpoints_user_idx", table_name="model_endpoints")
    op.drop_constraint(
        "model_endpoints_user_id_fkey", "model_endpoints", type_="foreignkey"
    )
    op.execute(
        """
        DELETE FROM model_endpoints a
        USING model_endpoints b
        WHERE a.agent = b.agent AND a.user_id > b.user_id
        """
    )
    op.drop_constraint("model_endpoints_pkey", "model_endpoints", type_="primary")
    op.drop_column("model_endpoints", "user_id")
    op.create_primary_key("model_endpoints_pkey", "model_endpoints", ["agent"])

    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'repo_view_dir', to_jsonb(repo_view_dir)
        FROM user_settings WHERE repo_view_dir IS NOT NULL LIMIT 1
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'gitee_base_branch', to_jsonb(gitee_base_branch)
        FROM user_settings WHERE gitee_base_branch IS NOT NULL LIMIT 1
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'github_base_branch', to_jsonb(github_base_branch)
        FROM user_settings WHERE github_base_branch IS NOT NULL LIMIT 1
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.drop_table("user_settings")
