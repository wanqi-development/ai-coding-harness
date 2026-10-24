"""sync pull request column comments

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMMENTS = {
    "id": "主键",
    "session_id": "首次登记该 PR 的会话；会话删除后保留 PR 审计",
    "plan_id": "产出该 PR 的方案；一个方案允许对应多个替代 PR",
    "platform": "代码平台：gitee / github",
    "repo_owner": "仓库 owner",
    "repo_name": "仓库名",
    "number": "平台 PR 编号",
    "title": "PR 标题",
    "state": "open / merged / closed / unknown",
    "url": "PR 网页链接",
    "author": "PR 创建者登录名",
    "head_ref": "源分支",
    "head_sha": "远端最新 head SHA",
    "synced_head_sha": "最后一次安全同步进沙箱的 head SHA",
    "base_ref": "目标分支",
    "source": "归属：agent / legacy_agent / external / unknown",
    "remote_created_at": "平台记录创建时间",
    "remote_updated_at": "平台记录更新时间",
    "last_synced_at": "最近一次从平台刷新该行的时间",
    "created_at": "创建时间",
    "updated_at": "更新时间",
}


def upgrade() -> None:
    for column, comment in COMMENTS.items():
        op.alter_column("pull_requests", column, comment=comment)


def downgrade() -> None:
    for column in COMMENTS:
        op.alter_column("pull_requests", column, comment=None)
