"""Repository/token access checks shared by session creation and credential updates."""

from __future__ import annotations

import httpx

from ai_coding_harness.config import Settings
from ai_coding_harness.core.codeplatform import Platform, RepoSpec, platform_label
from ai_coding_harness.tools.gitee_client import GiteeClient
from ai_coding_harness.tools.github_client import GitHubClient


class RepoAccessError(RuntimeError):
    """The current user's credential cannot access this repository."""


def _client(settings: Settings, repo: RepoSpec) -> GiteeClient | GitHubClient:
    base_url = settings.api_base_for(repo.platform)
    if repo.platform is Platform.GITHUB:
        return GitHubClient(base_url, repo)
    return GiteeClient(base_url, repo)


async def verify_repo_access(settings: Settings, repo: RepoSpec) -> None:
    """Fail unless the repository is public or visible to the configured token."""
    client = _client(settings, repo)
    try:
        data = await client.get_repo(owner=repo.owner, repo=repo.repo)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403, 404}:
            raise RepoAccessError(
                f"当前账号无权访问 {platform_label(repo)} 仓库 "
                f"{repo.owner}/{repo.repo}，或仓库不存在。请检查仓库地址和代码平台令牌。"
            ) from exc
        raise RepoAccessError(
            f"仓库权限校验失败：{platform_label(repo)} HTTP {status}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RepoAccessError(
            f"仓库权限校验失败：{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(data, dict) or data.get("error"):
        reason = data.get("error") if isinstance(data, dict) else "接口返回异常"
        raise RepoAccessError(f"仓库权限校验失败：{reason}")


async def verify_token(settings: Settings, platform: Platform, token: str) -> None:
    """Validate a non-empty platform token even when the user has no active sessions."""
    repo = RepoSpec(platform=platform, owner="_", repo="_", token=token)
    client = _client(settings, repo)
    try:
        data = await client.get_user()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            raise RepoAccessError(
                f"{platform.label} 令牌无效或已过期，请重新配置。"
            ) from exc
        raise RepoAccessError(
            f"{platform.label} 令牌校验失败：HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RepoAccessError(
            f"{platform.label} 令牌校验失败：{type(exc).__name__}: {exc}"
        ) from exc
    if isinstance(data, dict) and data.get("error"):
        raise RepoAccessError(str(data["error"]))
