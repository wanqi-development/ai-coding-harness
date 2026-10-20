"""``GitHubClient``：认证头、**issues 评论端点**、关闭方式、``merged`` 映射、可读格式化。

与 ``test_gitee_client.py`` 同路数：不联网，用 ``httpx.MockTransport`` 截获真实请求。
两边**一模一样**的部分（失败纪律、head→base 查重）由 ``pr_client`` 的共用实现保证，
这里只钉 GitHub 自己的差异。
"""

from __future__ import annotations

import httpx
import pytest

from ai_coding_harness.core.codeplatform import Platform, RepoSpec
from ai_coding_harness.tools.github_client import API_VERSION, GitHubClient
from ai_coding_harness.tools.pr_client import PrLookupError, _format_pr

REPO = RepoSpec(platform=Platform.GITHUB, owner="acme", repo="demo", token="t")
# 可辨识假令牌：便于断言它绝不出现在 URL / body 里
TOKEN = "FAKE-TOKEN-abc123"


def make_client(handler, *, token: str = TOKEN, base: str = "https://api.github.com"):
    repo = RepoSpec(
        platform=Platform.GITHUB, owner="acme", repo="demo", token=token
    )
    return GitHubClient(base, repo, transport=httpx.MockTransport(handler))


def pull(*, number: int, state: str = "open", merged_at: str | None = None) -> dict:
    return {
        "number": number,
        "state": state,
        "merged_at": merged_at,
        "head": {"ref": "feature/x", "repo": {"full_name": "acme/demo"}},
        "base": {"ref": "main"},
        "user": {"login": "acme"},
        "title": "改点什么",
    }


CALLS = [
    (
        "create_pr",
        lambda c: c.create_pr(title="t", head="h", base="b", body="x"),
        "POST",
        "/repos/acme/demo/pulls",
    ),
    (
        "comment_pr",
        lambda c: c.comment_pr(number=6, body="x"),
        "POST",
        "/repos/acme/demo/issues/6/comments",
    ),
    (
        "list_pull_comments",
        lambda c: c.list_pull_comments(number=6),
        "GET",
        "/repos/acme/demo/issues/6/comments",
    ),
    (
        "get_pull",
        lambda c: c.get_pull(owner="acme", repo="demo", number=6),
        "GET",
        "/repos/acme/demo/pulls/6",
    ),
    (
        "list_pulls",
        lambda c: c.list_pulls(owner="acme", repo="demo"),
        "GET",
        "/repos/acme/demo/pulls",
    ),
    (
        "find_open_pull",
        lambda c: c.find_open_pull(head="h", base="b"),
        "GET",
        "/repos/acme/demo/pulls",
    ),
    (
        "close_pull",
        lambda c: c.close_pull(number=6),
        "PATCH",
        "/repos/acme/demo/pulls/6",
    ),
]


@pytest.mark.parametrize("name,call,method,path", CALLS, ids=[c[0] for c in CALLS])
async def test_token_travels_only_in_header(name, call, method, path) -> None:
    """每个方法都必须：令牌在 ``Authorization: Bearer`` 头里，URL / body 里没有它。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={} if request.method != "GET" else [])

    await call(make_client(handler))

    assert len(captured) == 1
    request = captured[0]
    assert request.method == method
    assert request.url.path == path
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    # GitHub 要求的两件套：媒体类型与 API 版本
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == API_VERSION

    assert TOKEN not in str(request.url)
    assert TOKEN not in request.content.decode()


async def test_comments_use_the_issues_endpoint() -> None:
    """回归：``/pulls/{n}/comments`` 在 GitHub 是 diff 行级 review 评论，语义不同。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"id": 1, "html_url": "u"} if request.method == "POST" else [])

    client = make_client(handler)
    await client.comment_pr(number=6, body="结论")
    await client.list_pull_comments(number=6)

    assert seen == [
        "POST /repos/acme/demo/issues/6/comments",
        "GET /repos/acme/demo/issues/6/comments",
    ]


async def test_close_sends_state_in_the_body_like_github_expects() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"state": "closed"})

    await make_client(handler).close_pull(number=6)

    request = captured[0]
    assert request.method == "PATCH"
    assert request.url.query == b""  # 不学 Gitee 往 query 上挂
    assert request.content.decode() == '{"state":"closed"}'


async def test_merged_state_is_filtered_locally() -> None:
    """GitHub 没有 ``state=merged``：请求发 ``closed``，再按 ``merged_at`` 本地过滤。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("state"))
        return httpx.Response(
            200,
            json=[
                pull(number=1, state="closed", merged_at="2026-09-27T00:00:00Z"),
                pull(number=2, state="closed", merged_at=None),
            ],
        )

    pulls = await make_client(handler).list_pulls(
        owner="acme", repo="demo", state="merged"
    )

    assert seen == ["closed"]
    assert [p["number"] for p in pulls] == [1]


async def test_find_open_pull_ignores_forks_and_other_branches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {  # head 分支对不上
                    **pull(number=1),
                    "head": {"ref": "feature/other", "repo": {"full_name": "acme/demo"}},
                },
                {
                    **pull(number=2),  # 同名分支但来自 fork
                    "head": {"ref": "feature/x", "repo": {"full_name": "other/demo"}},
                },
                pull(number=3),  # 命中
            ],
        )

    found = await make_client(handler).find_open_pull(head="feature/x", base="main")

    assert found is not None
    assert found["number"] == 3


@pytest.mark.parametrize("entry", ["open_pulls", "all_pulls"])
async def test_lookup_failures_are_never_reported_as_empty(entry) -> None:
    """查 PR 失败必须抛 ``PrLookupError`` —— 空列表会被上层当成「没有未合入的 PR」。"""

    def http_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "rate limit"})

    with pytest.raises(PrLookupError):
        await getattr(make_client(http_error), entry)()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(PrLookupError):
        await getattr(make_client(boom), entry)()


async def test_missing_token_returns_a_readable_error_not_an_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("没令牌时不该发请求")

    data = await make_client(handler, token="").get_pull(
        owner="acme", repo="demo", number=1
    )

    assert "GitHub access token 未配置" in data["error"]


def test_format_pr_reads_github_merged_and_null_mergeable() -> None:
    """GitHub 的合并态是 ``state=closed`` + ``merged_at``；``mergeable`` 可能是 null。"""
    text = _format_pr(
        {
            "number": 7,
            "title": "改点什么",
            "state": "closed",
            "merged": False,
            "merged_at": "2026-09-27T00:00:00Z",
            "mergeable": None,
            "head": {"ref": "feature/x"},
            "base": {"ref": "main"},
            "user": {"login": "acme"},
            "html_url": "https://github.com/acme/demo/pull/7",
        }
    )
    assert "closed（已合并）" in text
    assert "未知（平台还在计算" in text
