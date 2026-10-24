"""PR 客户端的公共部分：失败纪律、通用筛选、可读格式化。

两个平台各有一个客户端（``gitee_client`` / ``github_client``），差异化在那边；工具层
（``tools/git.py``）只认 ``PrClient`` 这套方法，不关心背后是谁。凡是两端**一模一样**的
逻辑（失败纪律、head→base 查重、文本结构化）都放这里，避免改一处漏一处。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from ai_coding_harness.core.codeplatform import RepoSpec, platform_label


def missing_token_message(repo: RepoSpec) -> str:
    """令牌没配时给模型看的那句话 —— 平台名与两个字段名**都从 ``repo`` 取**。

    以前这条消息在两个客户端里各写死一份（「Gitee access token 未配置…」/「GitHub access
    token 未配置…」），加平台就得记得改两处。这里一处生成，顺带保证字段名不会写歪：
    ``<平台小写>_token``（建会话的请求入参）与 ``<平台大写>_ACCESS_TOKEN``（.env 的兜底键）。
    """
    name = repo.platform.value
    return (
        f"{platform_label(repo)} access token 未配置"
        f"（请求未传 {name}_token，且 .env 未配置 {name.upper()}_ACCESS_TOKEN）。"
    )


class PrLookupError(RuntimeError):
    """查询仓库 PR 失败：凭据未配置 / 网络不通 / 接口报错。

    归一成一种异常，让调用方（如 ``save_repo_memory`` 的前置检查）不必分别处理
    「返回了 error 字典」与 ``httpx.HTTPStatusError`` 这两种失败形态 —— 任一种混进
    「查到了，没有 PR」里，就会把猜测当成事实。
    """


class PrClient(Protocol):
    """工具层认的方法集：两个平台的客户端都实现它。"""

    async def create_pr(
        self, *, title: str, head: str, base: str, body: str
    ) -> dict: ...

    async def comment_pr(self, *, number: int, body: str) -> dict: ...

    async def list_pull_comments(
        self, *, number: int, page: int = 1, limit: int = 100
    ) -> list[dict] | dict: ...

    async def get_pull(self, *, owner: str, repo: str, number: int) -> dict: ...

    async def list_pulls(
        self,
        *,
        owner: str,
        repo: str,
        state: str = "all",
        limit: int = 20,
        page: int = 1,
        sort: str = "created",
        direction: str = "desc",
    ) -> list[dict] | dict: ...

    async def list_pull_commits(
        self, *, number: int, page: int = 1, limit: int = 100
    ) -> list[dict] | dict: ...

    async def open_pulls(self, *, limit: int = 100) -> list[dict]: ...

    async def all_pulls(
        self, *, limit: int = 10, until_number: int | None = None
    ) -> list[dict]: ...

    async def find_open_pull(self, *, head: str, base: str) -> dict | None: ...

    async def close_pull(self, *, number: int) -> dict: ...


#: 归一后的 PR 状态码。**平台差异只在这里消化一次**：Gitee 把合并态写在 ``state=merged``
#: 上，GitHub 的 ``state`` 只认 open/closed、合并态要看 ``merged_at``。
PULL_OPEN = "open"
PULL_MERGED = "merged"
PULL_CLOSED = "closed"
#: 认不出的取值（平台将来新增的状态）——**不猜**：猜错会把「未知」说成「已合并」。
PULL_UNKNOWN = "unknown"


def pull_state(pull: Mapping[str, Any]) -> str:
    """一条 PR 的状态码：``open`` / ``merged`` / ``closed`` / ``unknown``。

    ``agent/memory_reconcile`` 判「有没有未合入的 PR」用的是**同一套判定**（那边把它翻成
    中文标签显示在记忆里）。改这里必须同时看那边 —— 两处判定不一致，就会出现「面板说还
    开着、闸门说没有」这种自相矛盾，而那正是最难查的一类 bug。
    """
    if pull.get("merged_at"):
        return PULL_MERGED
    state = str(pull.get("state") or "").strip().lower()
    if state == "merged":
        return PULL_MERGED
    if state == "open":
        return PULL_OPEN
    if state in ("closed", "rejected"):
        return PULL_CLOSED
    return PULL_UNKNOWN


def pull_summary(pull: Mapping[str, Any]) -> dict[str, str]:
    """一条 PR 归一成界面要的那几个字段（编号 / 标题 / 状态码 / 链接 / 分支 / 作者 / 时间）。

    放在客户端旁边而不是路由里：平台差异（``head.ref``、``user.login`` 与 ``user.name``、
    ``html_url``）到此为止，路由与前端都不碰原始响应形状。认不出的字段给空串，不给 ``None``
    —— 前端少一层判空。
    """
    head = pull.get("head")
    user = pull.get("user")
    number = str(pull.get("number") or "").strip()
    return {
        "number": number,
        "title": str(pull.get("title") or "").strip() or f"PR #{number}",
        "state": pull_state(pull),
        "url": str(pull.get("html_url") or "").strip(),
        "branch": str(head.get("ref") or "").strip() if isinstance(head, Mapping) else "",
        "author": (
            str(user.get("login") or user.get("name") or "").strip()
            if isinstance(user, Mapping)
            else ""
        ),
        "created_at": str(pull.get("created_at") or "").strip(),
        "updated_at": str(pull.get("updated_at") or "").strip(),
    }


async def pulls_or_raise(
    client: PrClient,
    repo: RepoSpec,
    *,
    state: str,
    limit: int = 100,
    page: int = 1,
    sort: str = "created",
    direction: str = "desc",
) -> list[dict]:
    """本仓库的 PR 列表；任何失败一律抛 ``PrLookupError``。

    这是「查 PR」**唯一**的失败纪律所在，``open_pulls`` 与 ``all_pulls`` 共用它：
    绝不返回空列表冒充「没有 PR」—— 把一次查询失败讲成「查到了，没有未关闭的 PR」，
    就等于让上层基于猜测去写仓库记忆。
    """
    try:
        pulls = await client.list_pulls(
            owner=repo.owner,
            repo=repo.repo,
            state=state,
            limit=limit,
            page=page,
            sort=sort,
            direction=direction,
        )
    except httpx.HTTPStatusError as exc:
        raise PrLookupError(
            f"HTTP {exc.response.status_code} {exc.response.text[:300]}"
        ) from exc
    except httpx.HTTPError as exc:
        # 连不上 / 超时等传输层错误：同属「没查成」，漏掉这条会让异常绕过检查
        # 直接抛给模型，模型只看到一句裸报错，不知道该按什么处理
        raise PrLookupError(f"{type(exc).__name__}: {exc}") from exc
    if isinstance(pulls, dict):
        reason = pulls.get("error")
        raise PrLookupError(str(reason or "接口返回了非列表结果"))
    return pulls


async def all_pages_or_raise(
    client: PrClient, repo: RepoSpec, *, state: str, limit: int = 100
) -> list[dict]:
    """分页取完某一状态；open PR 的闸门不能只看前 100 条。"""
    out: list[dict] = []
    for page in range(1, 21):
        batch = await pulls_or_raise(
            client,
            repo,
            state=state,
            limit=limit,
            page=page,
        )
        out.extend(batch)
        if len(batch) < limit:
            return out
    raise PrLookupError("PR 列表超过 20 页，拒绝在状态不完整时继续")


async def recent_pulls_or_raise(
    client: PrClient,
    repo: RepoSpec,
    *,
    limit: int = 10,
    until_number: int | None = None,
) -> list[dict]:
    """取最近一页；与内部水位没有重叠时继续翻页，直到能覆盖上次数据。"""
    out: list[dict] = []
    for page in range(1, 21):
        batch = await pulls_or_raise(
            client,
            repo,
            state="all",
            limit=limit,
            page=page,
        )
        out.extend(batch)
        numbers = []
        for pull in batch:
            try:
                numbers.append(int(str(pull.get("number") or "")))
            except ValueError:
                continue
        if (
            len(batch) < limit
            or until_number is None
            or (numbers and min(numbers) <= until_number)
        ):
            return out
    raise PrLookupError("PR 增量超过 20 页，拒绝在历史同步不完整时继续")


async def find_open_pull(
    client: PrClient, repo: RepoSpec, *, head: str, base: str
) -> dict | None:
    """查找本仓库中 head → base 仍未关闭的 PR；没有则返回 ``None``。

    用列表接口在本地过滤，而非依赖平台的 ``head``/``base`` 查询参数 ——
    两边对 ``owner:branch`` 形式的接受度不一致，本地过滤结果确定。
    """
    try:
        pulls = await all_pages_or_raise(client, repo, state="open", limit=100)
    except PrLookupError:
        return None  # 未配置 token 等，交由后续创建流程报错
    this_repo = repo.full_name
    for pull in pulls:
        if (pull.get("head") or {}).get("ref") != head:
            continue
        if (pull.get("base") or {}).get("ref") != base:
            continue
        full_name = ((pull.get("head") or {}).get("repo") or {}).get("full_name")
        if full_name and full_name != this_repo:
            continue  # 同名分支来自 fork，不算本仓库的 PR
        return pull
    return None


def commits_match_agent_identity(
    commits: list[dict],
    *,
    name: str,
    email: str,
) -> bool:
    """PR 的每个提交是否都来自当前沙箱的静态 git 身份。"""
    wanted_name = name.strip().casefold()
    wanted_email = email.strip().casefold()
    if not commits or not wanted_name or not wanted_email:
        return False
    for commit in commits:
        nested = commit.get("commit")
        author = nested.get("author") if isinstance(nested, Mapping) else None
        if not isinstance(author, Mapping):
            return False
        got_name = str(author.get("name") or "").strip().casefold()
        got_email = str(author.get("email") or "").strip().casefold()
        if got_name != wanted_name or got_email != wanted_email:
            return False
    return True


def _mergeable_text(value: Any) -> str:
    """``mergeable`` 的展示口径。

    GitHub 对刚创建的 PR 常回 ``null``（后台还在算），直接印 ``None`` 会让模型以为
    「不能合并」；Gitee 一般直接给布尔。
    """
    if value is None:
        return "未知（平台还在计算，稍后再查）"
    return str(value)


def _format_pr(data: dict) -> str:
    """把 PR 详情格式化为可读文本。

    两个平台的字段名一致（``number/title/state/head/base/user/html_url/merged_at``），
    只有「合并态」的表示不同：Gitee 会把 ``state`` 直接给成 ``merged``，GitHub 则是
    ``state=closed`` + 有 ``merged_at``。
    """
    head = data.get("head") or {}
    base = data.get("base") or {}
    user = data.get("user") or {}
    merged = (
        "（已合并）"
        if data.get("merged") or data.get("state") == "merged" or data.get("merged_at")
        else ""
    )
    lines = [
        f"PR #{data.get('number')}：{data.get('title')}",
        f"状态：{data.get('state')}{merged}",
        f"作者：{user.get('login') or user.get('name') or '未知'}",
        f"分支：{head.get('ref')} → {base.get('ref')}",
        f"链接：{data.get('html_url')}",
        f"创建：{data.get('created_at')}　更新：{data.get('updated_at')}　合并：{data.get('merged_at')}",
        f"可合并：{_mergeable_text(data.get('mergeable'))}　评论数：{data.get('comments')}",
    ]
    body = (data.get("body") or "").strip()
    if body:
        lines += ["", "描述：", body]
    return "\n".join(lines)


def _format_pr_list(owner: str, repo: str, pulls: list[dict]) -> str:
    """把仓库的 PR 列表格式化为可读文本。"""
    lines = [f"{owner}/{repo} 的 PR（{len(pulls)} 条）："]
    for p in pulls:
        head = (p.get("head") or {}).get("ref")
        base = (p.get("base") or {}).get("ref")
        user = (p.get("user") or {}).get("login")
        state = p.get("state")
        if p.get("merged_at") or p.get("merged"):
            state = f"{state}（已合并）"
        lines.append(
            f"- #{p.get('number')} [{state}] {p.get('title')}"
            f"（{user}，{head} → {base}）"
        )
    return "\n".join(lines)


def _format_pr_comments(items: list[dict]) -> str:
    """把 PR 评论列表格式化为可读文本。

    保留评论 ``id``：回应某条问题时用它定位，也便于引用原问题原文。
    """
    if not items:
        return "该 PR 暂无评论。"
    lines = [f"共 {len(items)} 条评论："]
    for item in items:
        user = (item.get("user") or {}).get("login") or "未知"
        lines += [
            "",
            f"- #{item.get('id')}　{user}　{item.get('created_at')}",
            (item.get("body") or "").strip(),
        ]
    return "\n".join(lines)
