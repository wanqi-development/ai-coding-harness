"""``GiteeClient`` 的鉴权传递方式与 ``find_open_pull`` 的本地筛选 + ``_format_pr`` 的合并态。

不联网：列表筛选用假 ``list_pulls``；鉴权测试用 ``httpx.MockTransport`` 截获真实请求。
"""

from __future__ import annotations

import httpx
import pytest

from ai_coding_harness.tools.git import (
    GiteeClient,
    GiteeRepo,
    PrLookupError,
    _format_pr,
    _format_pr_comments,
)

REPO = GiteeRepo(owner="acme", repo="demo", token="t")

# 鉴权测试专用的可辨识假令牌：便于断言它绝不出现在 URL / body 里
TOKEN = "FAKE-TOKEN-abc123"


def pull(
    *,
    number: int,
    head: str,
    base: str,
    head_repo: str | None = "acme/demo",
    state: str = "open",
) -> dict:
    """构造一条 Gitee PR 列表项。``head_repo=None`` 表示响应里没有 head.repo。"""
    head_payload: dict = {"ref": head}
    if head_repo is not None:
        head_payload["repo"] = {"full_name": head_repo}
    return {
        "number": number,
        "state": state,
        "head": head_payload,
        "base": {"ref": base},
    }


def make_client(pulls):
    """返回 (client, calls)；``pulls`` 为列表时作为列表接口结果，为异常时抛出。"""
    client = GiteeClient("https://gitee.com/api/v5", REPO)
    calls: list[dict] = []

    async def fake_list_pulls(
        *,
        owner,
        repo,
        state="all",
        limit=20,
        page=1,
        sort="created",
        direction="desc",
    ):
        calls.append(
            {
                "owner": owner,
                "repo": repo,
                "state": state,
                "limit": limit,
                "page": page,
                "sort": sort,
                "direction": direction,
            }
        )
        if isinstance(pulls, Exception):
            raise pulls
        return pulls

    client.list_pulls = fake_list_pulls
    return client, calls


async def test_finds_open_pull_matching_head_and_base() -> None:
    target = pull(number=7, head="feature/x", base="master")
    client, calls = make_client([pull(number=3, head="feature/y", base="master"), target])

    found = await client.find_open_pull(head="feature/x", base="master")

    assert found is target
    # 只查未关闭的、且一次拉够本地过滤的量
    assert calls == [
        {
            "owner": "acme",
            "repo": "demo",
            "state": "open",
            "limit": 100,
            "page": 1,
            "sort": "created",
            "direction": "desc",
        }
    ]


async def test_ignores_pull_with_different_base() -> None:
    client, _ = make_client([pull(number=7, head="feature/x", base="develop")])

    assert await client.find_open_pull(head="feature/x", base="master") is None


async def test_ignores_pull_with_different_head() -> None:
    client, _ = make_client([pull(number=7, head="feature/other", base="master")])

    assert await client.find_open_pull(head="feature/x", base="master") is None


async def test_skips_fork_pull_carrying_same_branch_name() -> None:
    """来自 fork 的同名分支不算本仓库的 PR，否则会把别人的 PR 当成自己的复用掉。"""
    client, _ = make_client(
        [pull(number=7, head="feature/x", base="master", head_repo="someone/demo")]
    )

    assert await client.find_open_pull(head="feature/x", base="master") is None


async def test_pull_without_head_repo_counts_as_this_repo() -> None:
    """响应缺 head.repo 时不误判为 fork —— 保守地认定是本仓库的 PR。"""
    client, _ = make_client(
        [pull(number=7, head="feature/x", base="master", head_repo=None)]
    )

    found = await client.find_open_pull(head="feature/x", base="master")

    assert found is not None and found["number"] == 7


async def test_returns_none_when_listing_fails() -> None:
    """未配置 token 等情况下列表接口返回 error 字典，视为查不到而不是抛错。"""
    client, _ = make_client({"error": "Gitee access token 未配置"})

    assert await client.find_open_pull(head="feature/x", base="master") is None


async def test_open_pulls_asks_for_the_open_state_and_returns_the_list() -> None:
    target = pull(number=7, head="feature/x", base="master")
    client, calls = make_client([target])

    assert await client.open_pulls() == [target]
    assert calls == [
        {
            "owner": "acme",
            "repo": "demo",
            "state": "open",
            "limit": 100,
            "page": 1,
            "sort": "created",
            "direction": "desc",
        }
    ]


async def test_all_pulls_asks_for_every_state() -> None:
    """对账器要区分「已合入」与「已关闭未合入」，只看 open 不够。"""
    merged = pull(number=7, head="feature/x", base="master", state="merged")
    client, calls = make_client([merged])

    assert await client.all_pulls() == [merged]
    assert calls == [
        {
            "owner": "acme",
            "repo": "demo",
            "state": "all",
            "limit": 10,
            "page": 1,
            "sort": "created",
            "direction": "desc",
        }
    ]


async def test_open_pulls_reads_more_than_one_page() -> None:
    first = [pull(number=number, head=f"feature/{number}", base="master") for number in range(1, 101)]
    client, calls = make_client(first)

    class _PagedClient(type(client)):
        async def list_pulls(self, **kwargs):
            calls.append(kwargs)
            return first if kwargs["page"] == 1 else [first[0]]

    paged = _PagedClient("https://gitee.com/api/v5", REPO)

    assert len(await paged.open_pulls()) == 101
    assert [call["page"] for call in calls] == [1, 2]


async def test_recent_all_pulls_pages_until_it_overlaps_the_internal_watermark() -> None:
    client = GiteeClient("https://gitee.com/api/v5", REPO)
    pages = {
        1: [pull(number=n, head=f"feature/{n}", base="master") for n in range(21, 31)],
        2: [pull(number=n, head=f"feature/{n}", base="master") for n in range(11, 21)],
    }
    seen: list[int] = []

    async def fake_list_pulls(*, page, **kwargs):
        seen.append(page)
        return pages[page]

    client.list_pulls = fake_list_pulls

    rows = await client.all_pulls(limit=10, until_number=12)

    assert seen == [1, 2]
    assert next(row["number"] for row in rows) == 21
    assert [row["number"] for row in rows][-1] == 20


@pytest.mark.parametrize("entry", ["open_pulls", "all_pulls"])
async def test_pulls_raises_on_an_error_payload(entry: str) -> None:
    """未配置令牌时列表接口返回 error 字典而不是抛异常 —— 在这里同样算「没查成」。"""
    client, _ = make_client({"error": "Gitee access token 未配置"})

    with pytest.raises(PrLookupError, match="未配置"):
        await getattr(client, entry)()


@pytest.mark.parametrize("entry", ["open_pulls", "all_pulls"])
async def test_pulls_raises_on_http_failure(entry: str) -> None:
    """``find_open_pull`` 容忍的 HTTP 失败，在「写不写记忆」这个用途上必须抛出来：
    空列表会被读成「没有未关闭的 PR」，而事实是没查成。两个入口共用这条纪律。"""
    error = httpx.HTTPStatusError(
        "boom",
        request=httpx.Request("GET", "https://gitee.com/api/v5/repos/acme/demo/pulls"),
        response=httpx.Response(401, text="unauthorized"),
    )
    client, _ = make_client(error)

    with pytest.raises(PrLookupError, match="401"):
        await getattr(client, entry)()


@pytest.mark.parametrize("entry", ["open_pulls", "all_pulls"])
async def test_pulls_raises_on_a_connection_error(entry: str) -> None:
    """连不上走的是传输层异常（不是 HTTPStatusError）—— 它是另一条逃逸路径，
    漏掉就会绕过检查、把裸异常抛给模型。"""
    client, _ = make_client(httpx.ConnectError("getaddrinfo failed"))

    with pytest.raises(PrLookupError, match="ConnectError"):
        await getattr(client, entry)()


def test_format_pr_marks_merged_state() -> None:
    """Gitee 合并后 `merged` 仍为 false、只有 state 变 merged，也得显示「已合并」。"""
    merged = pull(number=7, head="feature/x", base="master", state="merged")
    merged["title"] = "feat: x"

    assert "（已合并）" in _format_pr(merged)

    opened = pull(number=7, head="feature/x", base="master")
    opened["title"] = "feat: x"
    opened["merged"] = False

    assert "（已合并）" not in _format_pr(opened)


# ---------------------------------------------------------------------------
# _format_pr_comments：评论列表渲染（保留 id，供后续逐条引用与回应）
# ---------------------------------------------------------------------------


def test_formats_comments_with_id_author_and_body() -> None:
    text = _format_pr_comments(
        [
            {
                "id": 101,
                "user": {"login": "reviewer-bot"},
                "created_at": "2026-09-16T10:00:00+08:00",
                "body": "P1 空指针风险",
            }
        ]
    )

    assert "共 1 条评论" in text
    assert "#101" in text
    assert "reviewer-bot" in text
    assert "P1 空指针风险" in text


def test_empty_comment_list_says_so() -> None:
    assert _format_pr_comments([]) == "该 PR 暂无评论。"


def test_comment_missing_user_and_body_does_not_crash() -> None:
    """Gitee 对空正文 / 无作者的评论可能缺字段，渲染不能炸。"""
    text = _format_pr_comments([{"id": 7}])

    assert "#7" in text
    assert "未知" in text


# ---------------------------------------------------------------------------
# 鉴权：令牌只走请求头（放 query/body 会被 httpx 的 INFO 日志写成明文落盘）
# ---------------------------------------------------------------------------

CALLS = [
    (
        "create_pr",
        lambda c: c.create_pr(title="t", head="h", base="b", body="x"),
        "POST",
        "/api/v5/repos/acme/demo/pulls",
    ),
    (
        "comment_pr",
        lambda c: c.comment_pr(number=6, body="x"),
        "POST",
        "/api/v5/repos/acme/demo/pulls/6/comments",
    ),
    (
        "list_pull_comments",
        lambda c: c.list_pull_comments(number=6),
        "GET",
        "/api/v5/repos/acme/demo/pulls/6/comments",
    ),
    (
        "list_pull_commits",
        lambda c: c.list_pull_commits(number=6),
        "GET",
        "/api/v5/repos/acme/demo/pulls/6/commits",
    ),
    (
        "get_pull",
        lambda c: c.get_pull(owner="acme", repo="demo", number=6),
        "GET",
        "/api/v5/repos/acme/demo/pulls/6",
    ),
    (
        "list_pulls",
        lambda c: c.list_pulls(owner="acme", repo="demo"),
        "GET",
        "/api/v5/repos/acme/demo/pulls",
    ),
    (
        "find_open_pull",
        lambda c: c.find_open_pull(head="h", base="b"),
        "GET",
        "/api/v5/repos/acme/demo/pulls",
    ),
    (
        "close_pull",
        lambda c: c.close_pull(number=6),
        "PATCH",
        "/api/v5/repos/acme/demo/pulls/6",
    ),
]


@pytest.mark.parametrize("name,call,method,path", CALLS, ids=[c[0] for c in CALLS])
async def test_token_travels_only_in_header(name, call, method, path) -> None:
    """每个方法都必须：令牌在 Authorization 头里，且 URL / body 里没有它。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    client = GiteeClient(
        "https://gitee.com/api/v5",
        GiteeRepo(owner="acme", repo="demo", token=TOKEN),
        transport=httpx.MockTransport(handler),
    )

    await call(client)

    assert len(captured) == 1
    request = captured[0]
    assert request.method == method
    assert request.url.path == path

    assert request.headers["authorization"] == f"token {TOKEN}"

    # 回归：曾经的写法把令牌拼进 query / body，httpx 会照 URL 打进日志
    assert TOKEN not in str(request.url)
    assert TOKEN not in request.content.decode()
    assert "access_token" not in str(request.url)
    assert "access_token" not in request.content.decode()
