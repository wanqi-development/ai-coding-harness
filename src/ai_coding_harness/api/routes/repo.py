"""右侧栏「代码」面板的接口：本机那份仓库只读缓存的读口（见 ``repo_cache.py``）。

四个接口都挂在会话下 —— 要看的仓库、以及私有仓库要用的令牌都来自会话（``resolve_session``
能拿库里的行组装出来，所以后端重启后照样能用）。

**与沙箱无关**：这几个接口全程只碰宿主机上的裸仓缓存，一不建沙箱、二不等工作区。工作区
还在准备、已经被回收、甚至建失败，代码面板都照常能看 —— 这是这个功能存在的理由。

错误口径：git 层抛的 ``RepoViewError`` 文案本来就是照着给用户看写的（且已按令牌脱敏），
所以原样当 409 的 ``detail`` 透出去；本机没装 git 单独给 503（那不是用户的输入问题）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ai_coding_harness.api.schemas import (
    RepoBranch,
    RepoFile,
    RepoSyncResult,
    RepoTree,
    RepoTreeEntry,
)
from ai_coding_harness.api.session_lookup import resolve_session
from ai_coding_harness.repo_cache import (
    GitMissingError,
    RepoSpec,
    RepoViewError,
    default_branch,
    fetched_at,
    find_git,
    is_cloned,
    list_branches,
    read_file,
    sync,
    tree,
)

router = APIRouter(prefix="/sessions", tags=["repo"])


def _settings(request: Request):
    return request.app.state.settings


def _enabled(request: Request) -> None:
    """总开关。关掉时四个接口一起 404 —— 与「这个部署没有这个功能」等价。"""
    if not _settings(request).repo_view_enabled:
        raise HTTPException(status_code=404, detail="代码面板已关闭（REPO_VIEW_ENABLED=false）")


async def _spec(request: Request, session_id: str) -> RepoSpec:
    _enabled(request)
    session = await resolve_session(request, session_id)
    return RepoSpec.from_session(session)


def _branches_payload(branches) -> list[RepoBranch]:
    return [RepoBranch(**vars(branch)) for branch in branches]


async def _status(request: Request, spec: RepoSpec, *, cloned_now: bool = False) -> RepoSyncResult:
    settings = _settings(request)
    cloned = is_cloned(settings, spec)
    default = ""
    branches = []
    if cloned:
        try:
            default = await default_branch(settings, spec)
        except RepoViewError:
            # 默认分支读不到（裸仓 HEAD 坏了之类）不该让整个面板打不开：
            # 分支列表照给，前端退回第一支
            default = ""
        branches = await list_branches(settings, spec, default=default)
    return RepoSyncResult(
        cloned=cloned,
        cloned_now=cloned_now,
        default_branch=default,
        branches=_branches_payload(branches),
        fetched_at=fetched_at(settings, spec),
        git=find_git(settings),
    )


@router.get("/{session_id}/repo", response_model=RepoSyncResult)
async def get_repo_status(session_id: str, request: Request) -> RepoSyncResult:
    """缓存状态 + 分支清单（**不联网**）：面板打开时先问它，有缓存就直接显示。"""
    spec = await _spec(request, session_id)
    try:
        return await _status(request, spec)
    except RepoViewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{session_id}/repo/sync", response_model=RepoSyncResult)
async def sync_repo(session_id: str, request: Request) -> RepoSyncResult:
    """下载或更新这份缓存：没有就 clone，有就 fetch。幂等，同一仓库并发进来只跑一次。"""
    spec = await _spec(request, session_id)
    try:
        result = await sync(_settings(request), spec)
    except GitMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RepoViewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RepoSyncResult(
        cloned=True,
        cloned_now=result.cloned,
        default_branch=result.default_branch,
        branches=_branches_payload(result.branches),
        fetched_at=fetched_at(_settings(request), spec),
        git=find_git(_settings(request)),
    )


@router.get("/{session_id}/repo/tree", response_model=RepoTree)
async def get_repo_tree(
    session_id: str,
    request: Request,
    ref: str = Query("", description="分支名；留空用默认分支"),
    path: str = Query("", description="仓库内相对路径；留空是根目录"),
) -> RepoTree:
    """列一层目录（惰性展开：前端点一层问一次，不把整棵文件树一次拉下来）。"""
    spec = await _spec(request, session_id)
    try:
        entries = await tree(_settings(request), spec, ref=ref, path=path)
    except RepoViewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RepoTree(
        ref=ref,
        path=path,
        entries=[RepoTreeEntry(**vars(entry)) for entry in entries],
    )


@router.get("/{session_id}/repo/file", response_model=RepoFile)
async def get_repo_file(
    session_id: str,
    request: Request,
    ref: str = Query("", description="分支名；留空用默认分支"),
    path: str = Query(..., description="仓库内相对路径"),
) -> RepoFile:
    """读一个文件的正文；太大 / 二进制只回标记。"""
    spec = await _spec(request, session_id)
    try:
        content = await read_file(_settings(request), spec, ref=ref, path=path)
    except RepoViewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RepoFile(**vars(content))
