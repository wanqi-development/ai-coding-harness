"""Repository visibility checks use the current user's credential."""

from __future__ import annotations

import httpx
import pytest

from ai_coding_harness.config import Settings
from ai_coding_harness.core.codeplatform import Platform, RepoSpec
from ai_coding_harness.tools import repo_access
from ai_coding_harness.tools.repo_access import RepoAccessError


class _Client:
    def __init__(self, *, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self.payload = payload or {"full_name": "acme/demo"}
        self.repo_headers: dict[str, str] | None = None

    async def get_repo(self, *, owner: str, repo: str):
        if self.status >= 400:
            request = httpx.Request("GET", f"https://api.example/repos/{owner}/{repo}")
            response = httpx.Response(self.status, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)
        return self.payload

    async def get_user(self):
        if self.status >= 400:
            request = httpx.Request("GET", "https://api.example/user")
            response = httpx.Response(self.status, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)
        return {"login": "alice"}


async def test_a_visible_repository_passes(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr(repo_access, "_client", lambda settings, repo: client)

    await repo_access.verify_repo_access(
        Settings(_env_file=None),
        RepoSpec(platform=Platform.GITHUB, owner="acme", repo="demo"),
    )


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_private_or_missing_repo_is_a_readable_access_error(
    monkeypatch, status: int
) -> None:
    monkeypatch.setattr(
        repo_access, "_client", lambda settings, repo: _Client(status=status)
    )

    with pytest.raises(RepoAccessError, match="无权访问"):
        await repo_access.verify_repo_access(
            Settings(_env_file=None),
            RepoSpec(platform=Platform.GITHUB, owner="acme", repo="demo"),
        )
