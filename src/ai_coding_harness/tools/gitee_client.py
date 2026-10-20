"""Gitee API v5 客户端：PR 的创建 / 评论 / 查询 / 关闭。

平台差异全部收在这个文件里（认证头、评论端点、关闭方式、``state=merged``）；工具层与
记忆闸门只认 ``tools/pr_client.PrClient`` 那套方法，不关心背后是哪个平台。

``owner/repo/token`` 是会话级的（从请求的仓库地址 + 令牌解析而来），``base_url`` 是
平台级配置。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from ai_coding_harness.core.codeplatform import RepoSpec
from ai_coding_harness.tools.pr_client import (
    PrClient,
    find_open_pull,
    missing_token_message,
    pulls_or_raise,
)


class GiteeClient:
    """Minimal Gitee API v5 client for PR create/comment."""

    def __init__(
        self,
        base_url: str,
        repo: RepoSpec,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._repo = repo
        self._transport = transport  # 仅测试注入用；生产为 None（默认传输）

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=30, transport=self._transport)

    def _auth_headers(self) -> dict[str, str]:
        """令牌只经请求头传递。

        Gitee 也支持把 ``access_token`` 放 query 或 body，但那样令牌会进 URL ——
        httpx 会把完整 URL 按 INFO 写进日志，等于把令牌落盘明文。请求头 httpx 不记。
        """
        return {"Authorization": f"token {self._repo.token}"}

    def _check_configured(self) -> str | None:
        if not self._repo.token:
            return missing_token_message(self._repo)
        return None

    async def create_pr(
        self, *, title: str, head: str, base: str, body: str
    ) -> dict:
        err = self._check_configured()
        if err:
            return {"error": err}
        url = f"{self._base_url}/repos/{self._repo.owner}/{self._repo.repo}/pulls"
        payload = {"title": title, "head": head, "base": base, "body": body}
        async with self._client() as client:
            resp = await client.post(url, headers=self._auth_headers(), json=payload)
        resp.raise_for_status()
        return resp.json()

    async def comment_pr(self, *, number: int, body: str) -> dict:
        err = self._check_configured()
        if err:
            return {"error": err}
        url = (
            f"{self._base_url}/repos/{self._repo.owner}/{self._repo.repo}"
            f"/pulls/{number}/comments"
        )
        async with self._client() as client:
            resp = await client.post(
                url, headers=self._auth_headers(), json={"body": body}
            )
        resp.raise_for_status()
        return resp.json()

    async def list_pull_comments(
        self, *, number: int, page: int = 1, limit: int = 100
    ) -> list[dict] | dict:
        """列出指定 PR 的评论（新版在前）。"""
        err = self._check_configured()
        if err:
            return {"error": err}
        url = (
            f"{self._base_url}/repos/{self._repo.owner}/{self._repo.repo}"
            f"/pulls/{number}/comments"
        )
        params = {"page": page, "per_page": limit}
        async with self._client() as client:
            resp = await client.get(url, headers=self._auth_headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_pull(self, *, owner: str, repo: str, number: int) -> dict:
        """获取指定 PR 的详情（owner/repo 可来自 PR 地址，不限于本会话仓库）。"""
        err = self._check_configured()
        if err:
            return {"error": err}
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls/{number}"
        async with self._client() as client:
            resp = await client.get(url, headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json()

    async def list_pulls(
        self, *, owner: str, repo: str, state: str = "all", limit: int = 20
    ) -> list[dict] | dict:
        """列出仓库的 PR（owner/repo 可来自仓库地址）。

        Gitee 的 ``state`` 直接支持 ``merged``，不用本地过滤。
        """
        err = self._check_configured()
        if err:
            return {"error": err}
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls"
        params = {"state": state, "per_page": limit}
        async with self._client() as client:
            resp = await client.get(url, headers=self._auth_headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    async def open_pulls(self) -> list[dict]:
        """本仓库未关闭的 PR 列表；失败一律抛 ``PrLookupError``（见 ``pr_client``）。"""
        return await pulls_or_raise(self, self._repo, state="open")

    async def all_pulls(self) -> list[dict]:
        """本仓库全部 PR（含已合入与已废弃）；失败纪律同 ``open_pulls``。"""
        return await pulls_or_raise(self, self._repo, state="all")

    async def find_open_pull(self, *, head: str, base: str) -> dict | None:
        """查找本仓库中 head → base 仍未关闭的 PR；没有则返回 ``None``。"""
        return await find_open_pull(self, self._repo, head=head, base=base)

    async def close_pull(self, *, number: int) -> dict:
        """关闭指定 PR（不合并）。Gitee 的状态放在 query 上。"""
        err = self._check_configured()
        if err:
            return {"error": err}
        url = (
            f"{self._base_url}/repos/{self._repo.owner}/{self._repo.repo}"
            f"/pulls/{number}"
        )
        async with self._client() as client:
            resp = await client.patch(
                url, headers=self._auth_headers(), params={"state": "closed"}
            )
        resp.raise_for_status()
        return resp.json()


if TYPE_CHECKING:  # 类型检查期的结构断言：客户端必须满足 PrClient 那套方法
    _implements_pr_client: type[PrClient] = GiteeClient
