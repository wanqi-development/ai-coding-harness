"""PR 工具：在代码平台（Gitee / GitHub）上创建 PR、评论、查询、关闭。

这些工具跑在**宿主进程**里（httpx 直连平台 API），不在沙箱里 shell 出 ``gh`` / ``curl``
—— 沙箱只需要能 push，交付物是 PR。

**平台差异不在这个文件里**：本模块只认 ``PrClient`` 那套方法（见 ``tools/pr_client.py``），
实现由 ``build_git_client`` 按仓库所在平台选（``tools/gitee_client.py`` /
``tools/github_client.py``）。对模型暴露的工具名、入参、返回文案两个平台**同一套**。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool

from ai_coding_harness.agent.turn_state import TurnState
from ai_coding_harness.config import Settings
from ai_coding_harness.core.codeplatform import (
    Platform,
    RepoSpec,
    parse_pr_url,
    parse_repo_url,
)
from ai_coding_harness.db import Database, dao
from ai_coding_harness.tools.context import get_configurable
from ai_coding_harness.tools.gitee_client import GiteeClient
from ai_coding_harness.tools.github_client import GitHubClient
from ai_coding_harness.tools.pr_client import (
    PrClient,
    PrLookupError,
    _format_pr,
    _format_pr_comments,
    _format_pr_list,
    pull_state,
    pull_summary,
)

logger = logging.getLogger(__name__)

# 旧名（既有测试与外部引用）：``GiteeRepo`` 就是 ``RepoSpec``，``platform`` 默认 Gitee。
GiteeRepo = RepoSpec

__all__ = [
    "GiteeClient",
    "GiteeRepo",
    "PrClient",
    "PrLookupError",
    "RepoSpec",
    "build_git_client",
    "build_pr_tools",
]


def build_git_client(settings: Settings, repo: RepoSpec) -> PrClient:
    """按仓库所在平台选客户端 —— 工具层与记忆闸门**唯一**的平台分叉点。"""
    base_url = settings.api_base_for(repo.platform)
    if repo.platform is Platform.GITHUB:
        return GitHubClient(base_url, repo)
    return GiteeClient(base_url, repo)


def _normalize_source(source: str, platform: Platform) -> str:
    """把 ``owner/repo`` 或 ``owner/repo/pulls|pull/n`` 归一化为完整 URL。

    只写了 ``owner/repo`` 时按**本会话**的平台补域名（贴的链接则自带域名，不受影响）。
    """
    text = source.strip()
    if "://" not in text:
        text = f"https://{platform.host}/" + text.lstrip("/")
    return text


def _spec_for(
    settings: Settings,
    repo: RepoSpec,
    platform: Platform,
    owner: str,
    name: str,
) -> RepoSpec:
    """给「按链接 / 按地址查」的目标仓库造一份规格。

    就是本会话这个仓库时沿用会话凭据；换了平台就取该平台配置里的令牌 ——
    跨平台查别人的 PR 只能这样（会话里只装着一个平台的令牌）。
    """
    if platform is repo.platform and owner == repo.owner and name == repo.repo:
        return repo
    return RepoSpec(
        platform=platform, owner=owner, repo=name, token=settings.token_for(platform)
    )


def build_pr_tools(
    settings: Settings,
    repo: RepoSpec,
    *,
    database: Database | None = None,
    session_id: int | None = None,
) -> list[BaseTool]:
    """Build the ``create_pr`` / ``comment_pr`` / ``list_pr_comments`` tools bound to a session repo."""
    client = build_git_client(settings, repo)

    async def register_pull(
        data: dict[str, Any],
        *,
        source: str = dao.PR_SOURCE_AGENT,
        mark_synced: bool = False,
    ) -> str | None:
        """把平台事实写进 PR 表；返回给模型看的失败提示。"""
        if database is None or session_id is None:
            return None
        try:
            number = int(data.get("number") or 0)
        except (TypeError, ValueError):
            return "PR 已创建，但登记失败：平台返回的 PR 编号不合法。"
        if number <= 0:
            return "PR 已创建，但登记失败：平台没有返回 PR 编号。"
        summary = pull_summary(data)
        head = data.get("head") if isinstance(data.get("head"), dict) else {}
        base = data.get("base") if isinstance(data.get("base"), dict) else {}
        state = get_configurable("turn_state")
        plan_id = state.plan_id if isinstance(state, TurnState) else None
        try:
            async with database.session() as db:
                await dao.upsert_pull_request(
                    db,
                    platform=repo.platform.value,
                    owner=repo.owner,
                    repo=repo.repo,
                    number=number,
                    title=summary["title"],
                    state=pull_state(data),
                    url=summary["url"],
                    author=summary["author"],
                    head_ref=str(head.get("ref") or ""),
                    head_sha=str(head.get("sha") or ""),
                    base_ref=str(base.get("ref") or ""),
                    source=source,
                    session_id=session_id,
                    plan_id=plan_id,
                    mark_synced=mark_synced and bool(head.get("sha")),
                )
        except Exception:
            logger.exception("PR 已创建但登记失败：%s/%s#%s", repo.owner, repo.repo, number)
            return f"PR 已创建，但登记到归属表失败（#{number}）。请检查数据库。"
        return None

    @tool
    async def create_pr(title: str, head: str, base: str, body: str) -> str:
        """在代码平台（Gitee / GitHub，按本会话仓库所在平台）上创建 Pull Request。

        幂等：若该 head → base 已有**未关闭**的 PR，则直接复用并返回它的编号与链接，
        不会新建重复 PR（因此无需先自己查一遍）。

        Args:
            title: PR 标题。
            head: 源分支（你刚 push 的分支）。
            base: 目标分支（合并目标，如 master / main）。
            body: PR 描述（改动内容、原因、验证结果）。
        """
        try:
            existing = await client.find_open_pull(head=head, base=base)
        except httpx.HTTPStatusError as exc:
            # 查重失败不阻断创建：平台自身会拦截重复 PR，这里只是少一次友好提示。
            logger.warning(
                "查询已有 PR 失败，直接创建：HTTP %s", exc.response.status_code
            )
            existing = None
        if existing is not None:
            number = int(existing.get("number") or 0)
            state = get_configurable("turn_state")
            if number > 0 and isinstance(state, TurnState):
                state.pr = number
            warning = await register_pull(existing, mark_synced=True)
            return (
                f"PR 已存在，直接复用：{existing.get('html_url')}"
                f"（编号 {number or '?'}，未新建）"
                + (f"\n{warning}" if warning else "")
            )

        try:
            data = await client.create_pr(title=title, head=head, base=base, body=body)
        except httpx.HTTPStatusError as exc:
            return f"创建 PR 失败：HTTP {exc.response.status_code} {exc.response.text[:500]}"
        if "error" in data:
            return f"创建 PR 失败：{data['error']}"
        number = int(data.get("number") or 0)
        state = get_configurable("turn_state")
        if number > 0 and isinstance(state, TurnState):
            state.pr = number
        warning = await register_pull(data, mark_synced=True)
        return (
            f"PR 已创建：{data.get('html_url')}（编号 {number or '?'}）"
            + (f"\n{warning}" if warning else "")
        )

    @tool
    async def comment_pr(number: int, body: str) -> str:
        """在指定 PR 上发布评论（用于代码审查结论）。

        Args:
            number: PR 编号。
            body: 评论内容（审查结论与关键问题）。
        """
        try:
            data = await client.comment_pr(number=number, body=body)
        except httpx.HTTPStatusError as exc:
            return f"评论失败：HTTP {exc.response.status_code} {exc.response.text[:500]}"
        if "error" in data:
            return f"评论失败：{data['error']}"
        return f"评论已发布：{data.get('html_url')}"

    @tool
    async def list_pr_comments(number: int) -> str:
        """读取指定 PR 上已有的评论（含审查结论与问题清单）。

        用于**取回审查原文**：回应某条问题、或据此生成修复方案前先调用它，
        不要凭记忆复述审查结论。

        Args:
            number: PR 编号。
        """
        try:
            data = await client.list_pull_comments(number=number)
        except httpx.HTTPStatusError as exc:
            return f"获取 PR 评论失败：HTTP {exc.response.status_code} {exc.response.text[:300]}"
        if isinstance(data, dict):
            return f"获取 PR 评论失败：{data.get('error', '接口返回了非列表结果')}"
        return _format_pr_comments(data)

    @tool
    async def get_pr_info(source: str, state: str = "all") -> str:
        """查看仓库的 PR（输入可以是仓库，也可以是某个 PR 链接）。

        Gitee 与 GitHub 都支持；**贴的是哪个平台的链接就查哪个**，与会话仓库不必同平台。

        Args:
            source: 仓库地址（``https://gitee.com/<owner>/<repo>``、
                    ``https://github.com/<owner>/<repo>``，或 ``<owner>/<repo>``），
                    或 PR 链接（Gitee ``/pulls/<n>``、GitHub ``/pull/<n>``）。
            state: 当 source 为仓库时，筛选 PR 状态：open / closed / merged / all（默认 all）。
        """
        normalized = _normalize_source(source, repo.platform)

        # 1) PR 链接 → 单个 PR 详情
        try:
            platform, owner, name, number = parse_pr_url(normalized)
        except ValueError:
            number = None
        if number is not None:
            target = build_git_client(
                settings, _spec_for(settings, repo, platform, owner, name)
            )
            try:
                data = await target.get_pull(owner=owner, repo=name, number=number)
            except httpx.HTTPStatusError as exc:
                return f"获取 PR 失败：HTTP {exc.response.status_code} {exc.response.text[:300]}"
            if isinstance(data, dict) and "error" in data:
                return f"获取 PR 失败：{data['error']}"
            return _format_pr(data)

        # 2) 仓库 → PR 列表
        try:
            platform, owner, name = parse_repo_url(normalized)
        except ValueError:
            return f"无法解析：{source}（应为仓库地址或 PR 链接）"
        target = build_git_client(
            settings, _spec_for(settings, repo, platform, owner, name)
        )
        try:
            pulls = await target.list_pulls(owner=owner, repo=name, state=state)
        except httpx.HTTPStatusError as exc:
            return f"获取 PR 列表失败：HTTP {exc.response.status_code} {exc.response.text[:300]}"
        if isinstance(pulls, dict) and "error" in pulls:
            return f"获取 PR 列表失败：{pulls['error']}"
        if not pulls:
            return f"{owner}/{name} 没有符合条件的 PR。"
        return _format_pr_list(owner, name, pulls)

    @tool
    async def close_pr(number: int, comment: str = "") -> str:
        """关闭指定 PR（不合并）。

        **调用前必须先向用户说明并获得用户明确同意**。

        Args:
            number: PR 编号。
            comment: 关闭原因；非空时先作为评论发布到该 PR，再关闭。
        """
        if comment.strip():
            try:
                posted = await client.comment_pr(number=number, body=comment)
            except httpx.HTTPStatusError as exc:
                return (
                    f"关闭 PR 失败：发布评论出错 "
                    f"HTTP {exc.response.status_code} {exc.response.text[:300]}"
                )
            if isinstance(posted, dict) and "error" in posted:
                return f"关闭 PR 失败：发布评论出错 {posted['error']}"
        try:
            data = await client.close_pull(number=number)
        except httpx.HTTPStatusError as exc:
            return f"关闭 PR 失败：HTTP {exc.response.status_code} {exc.response.text[:500]}"
        if isinstance(data, dict) and "error" in data:
            return f"关闭 PR 失败：{data['error']}"
        if database is not None and session_id is not None:
            try:
                async with database.session() as db:
                    row = await dao.get_pull_request(
                        db,
                        platform=repo.platform.value,
                        owner=repo.owner,
                        repo=repo.repo,
                        number=number,
                    )
                    if row is not None:
                        row.state = "closed"
            except Exception:
                logger.exception("PR 已关闭但本地状态更新失败：%s", number)
        return f"PR #{number} 已关闭"

    return [create_pr, comment_pr, list_pr_comments, get_pr_info, close_pr]
