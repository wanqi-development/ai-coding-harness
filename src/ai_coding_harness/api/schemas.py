"""API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    repo_url: str
    # 平台由 repo_url 判定；两个令牌字段按平台取用（Gitee 走 OAuth 登录、
    # GitHub 目前只支持 .env 里的令牌，见 CHANGELOG 的遗留项）
    gitee_token: str | None = None
    github_token: str | None = None


class SessionResponse(BaseModel):
    """建会话 / 准备代码工作区的回执。``workspace_status`` 见 ``SessionDetail``。

    ``reused`` 为真表示这个仓库已有活跃会话，本次落到既有会话上（没新建也没建工作区），
    前端据此还原历史而不是当新会话；准备代码工作区的回执里恒为假。
    """

    session_id: str
    workspace_status: str
    reused: bool = False


class SessionDetail(BaseModel):
    """会话详情：前端据此显示代码工作区进度（准备中 / 可用 / 异常）。

    ``workspace_error`` 不外发 —— 那是沙箱侧原始异常文本，只给运维看（见 DB 注释）。
    ``running`` 表示本会话有一轮对话正在跑：页面刷新后据它决定要不要接回直播流。
    """

    session_id: str
    title: str | None = None
    owner: str | None = None
    repo: str | None = None
    repo_url: str | None = None
    status: str
    workspace_status: str
    running: bool = False
    created_at: datetime


class SessionListItem(BaseModel):
    session_id: str
    title: str | None = None
    owner: str | None = None
    repo: str | None = None
    repo_url: str | None = None
    status: str
    created_at: datetime


class RecordItem(BaseModel):
    id: int
    type: str
    summary: str | None = None
    content: str | None = None
    extra: Any | None = None
    created_at: datetime


class ChatRequest(BaseModel):
    message: str


class PlanStepItem(BaseModel):
    """方案里的一步。历史数据可能带脏字段，故只保证 index/goal，其余给默认。"""

    index: int
    goal: str
    status: str = "pending"
    files: Any | None = None
    done_criteria: str | None = None


class PlanInfo(BaseModel):
    """方案快照：SSE 的 plan 事件与 GET /sessions/{id}/plan 共用同一份结构。"""

    plan_id: int
    title: str | None = None
    status: str
    steps: list[PlanStepItem] = Field(default_factory=list)
    updated_at: datetime


# --------------------------------------------------------------------------- 代码面板
# 宿主机侧的仓库只读缓存（``repo_cache.py``）：这几个结构只服务右侧栏「代码」标签。


class RepoBranch(BaseModel):
    """一支分支。``is_default`` 由裸仓的 HEAD 判定（前端据此把它排在/标在最前）。"""

    name: str
    sha: str
    updated_at: str = ""
    subject: str = ""
    is_default: bool = False


class RepoStatus(BaseModel):
    """缓存状态 + 分支清单 —— 打开面板先看它，据此决定显示「下载仓库」还是直接浏览。

    ``cloned`` 为假时 ``branches`` 一定是空的；``fetched_at`` 是上次**成功**同步的时间。
    """

    cloned: bool
    default_branch: str = ""
    branches: list[RepoBranch] = Field(default_factory=list)
    fetched_at: str | None = None
    git: str | None = None


class RepoSyncResult(RepoStatus):
    """同步回执：比 ``RepoStatus`` 多一个「这次是克隆还是更新」。"""

    cloned_now: bool = False


class RepoTreeEntry(BaseModel):
    name: str
    path: str
    type: str
    size: int | None = None


class RepoTree(BaseModel):
    ref: str
    path: str = ""
    entries: list[RepoTreeEntry] = Field(default_factory=list)


class RepoFile(BaseModel):
    """一个文件的正文或「为什么不给正文」：``too_big`` / ``binary`` 为真时 ``text`` 为空。"""

    path: str
    ref: str
    text: str | None = None
    size: int = 0
    binary: bool = False
    too_big: bool = False
