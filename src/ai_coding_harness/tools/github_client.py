"""GitHub REST API 客户端：PR 的创建 / 评论 / 查询 / 关闭。

与 Gitee 的差异**只在这个文件里**（工具层与记忆闸门只认 ``PrClient`` 那套方法）：

- 认证头：``Authorization: Bearer <token>``（Gitee 是 ``token <token>``），另加 GitHub
  要求的 ``Accept`` 与 ``X-GitHub-Api-Version``；
- 评论走 **issues 端点** ``/issues/{n}/comments`` —— ``/pulls/{n}/comments`` 在 GitHub 是
  **diff 行级 review 评论**，要 path/position，语义完全不同；
- 关闭 PR 用 **body** 传 ``{"state": "closed"}``（Gitee 走 query）；
- ``state`` 只认 ``open`` / ``closed`` / ``all``，**没有 ``merged``** —— 合并态看
  ``merged_at``，调用方要 ``merged`` 时本地过滤。
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

#: GitHub 的 REST API 版本头（不写会用默认版本，写死是为了行为可预期）
API_VERSION = "2022-11-28"


class GitHubClient:
    """Minimal GitHub REST client for PR create/comment."""

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
        """令牌只经请求头传递（进 URL 会被 httpx 按 INFO 记进日志）。"""
        return {
            "Authorization": f"Bearer {self._repo.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

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
        """在 PR 上发评论 —— GitHub 的「PR 评论」就是 issue 评论。"""
        err = self._check_configured()
        if err:
            return {"error": err}
        url = (
            f"{self._base_url}/repos/{self._repo.owner}/{self._repo.repo}"
            f"/issues/{number}/comments"
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
        """列出指定 PR 的评论（issue 评论；GitHub 默认按时间**正序**，与 Gitee 相反）。"""
        err = self._check_configured()
        if err:
            return {"error": err}
        url = (
            f"{self._base_url}/repos/{self._repo.owner}/{self._repo.repo}"
            f"/issues/{number}/comments"
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
        """列出仓库的 PR。

        GitHub 没有 ``state=merged``：要「已合并」就查 ``closed`` 再按 ``merged_at``
        本地过滤，这样调用方（``memory_reconcile`` 的对账、``get_pr_info``）不必知道
        这个差异。
        """
        err = self._check_configured()
        if err:
            return {"error": err}
        wanted = (state or "all").strip().lower()
        query_state = "closed" if wanted == "merged" else wanted
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls"
        params = {"state": query_state, "per_page": limit}
        async with self._client() as client:
            resp = await client.get(url, headers=self._auth_headers(), params=params)
        resp.raise_for_status()
        pulls = resp.json()
        if not isinstance(pulls, list) or wanted != "merged":
            return pulls
        return [p for p in pulls if p.get("merged_at")]

    async def open_pulls(self) -> list[dict]:
        """本仓库未关闭的 PR 列表；失败一律抛 ``PrLookupError``（见 ``pr_client``）。"""
        return await pulls_or_raise(self, self._repo, state="open")

    async def all_pulls(self) -> list[dict]:
        """本仓库全部 PR（含已合入与已关闭未合入）；失败纪律同 ``open_pulls``。"""
        return await pulls_or_raise(self, self._repo, state="all")

    async def find_open_pull(self, *, head: str, base: str) -> dict | None:
        """查找本仓库中 head → base 仍未关闭的 PR；没有则返回 ``None``。"""
        return await find_open_pull(self, self._repo, head=head, base=base)

    async def close_pull(self, *, number: int) -> dict:
        """关闭指定 PR（不合并）。GitHub 的状态放在请求体里。"""
        err = self._check_configured()
        if err:
            return {"error": err}
        url = (
            f"{self._base_url}/repos/{self._repo.owner}/{self._repo.repo}"
            f"/pulls/{number}"
        )
        async with self._client() as client:
            resp = await client.patch(
                url, headers=self._auth_headers(), json={"state": "closed"}
            )
        resp.raise_for_status()
        return resp.json()


if TYPE_CHECKING:  # 类型检查期的结构断言：客户端必须满足 PrClient 那套方法
    _implements_pr_client: type[PrClient] = GitHubClient
