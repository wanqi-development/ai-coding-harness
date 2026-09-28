"""代码托管平台（Gitee / GitHub）的公共口径。

一个会话绑一个仓库 → 一个平台。平台由**仓库地址**解析（会话级，记在
``sessions.git_platform``），也能由 **PR 链接**解析（工具入参级：贴的是哪个平台的链接
就查哪个）。对模型暴露的工具只有一套（见 ``tools/git.py``），平台差异全在
``tools/gitee_client.py`` / ``tools/github_client.py`` 两个实现里。

本模块是**叶子**：不 import ``config`` / ``tools`` —— ``config``、``session`` 都要用它，
反向依赖会成环。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse


class Platform(str, Enum):
    """支持的平台。``sessions.git_platform`` 存的就是这里的取值。"""

    GITEE = "gitee"
    GITHUB = "github"

    @classmethod
    def of(cls, value: str | None) -> Platform:
        """按库里的取值还原；空值与认不出的取值一律当 **Gitee**（老会话零迁移）。"""
        try:
            return cls(str(value or "").strip().lower())
        except ValueError:
            return cls.GITEE

    @classmethod
    def of_host(cls, host: str | None) -> Platform | None:
        """按域名认平台；自建实例（不是这两个域名）返回 ``None``，不猜。"""
        name = (host or "").strip().lower().split(":")[0].removeprefix("www.")
        for platform in cls:
            if name == platform.host:
                return platform
        return None

    @classmethod
    def of_url(cls, url: str) -> Platform | None:
        """按仓库 / PR 地址的域名认平台。"""
        return cls.of_host(urlparse((url or "").strip()).netloc)

    @property
    def host(self) -> str:
        return _HOSTS[self]

    @property
    def label(self) -> str:
        """给用户看的名字。"""
        return _LABELS[self]

    @property
    def pr_segment(self) -> str:
        """PR 链接里的那一段：Gitee 是 ``/pulls/12``，GitHub 是 ``/pull/12``。"""
        return _PR_SEGMENTS[self]

    @property
    def default_git_username(self) -> str:
        """askpass 回给 git 的用户名。

        Gitee 的 OAuth 令牌配 ``oauth2``；GitHub 的 PAT / 安装令牌配 ``x-access-token``。
        两边都允许用真实用户名（PAT 场景），所以这只是一份**默认值**。
        """
        return _GIT_USERNAMES[self]


_HOSTS = {Platform.GITEE: "gitee.com", Platform.GITHUB: "github.com"}
_LABELS = {Platform.GITEE: "Gitee", Platform.GITHUB: "GitHub"}
_PR_SEGMENTS = {Platform.GITEE: "pulls", Platform.GITHUB: "pull"}
_GIT_USERNAMES = {Platform.GITEE: "oauth2", Platform.GITHUB: "x-access-token"}

#: PR 链接里可能出现的那一段（两个平台都认，省得为「贴错写法」再报一次错）
_PR_SEGMENTS_ALL = frozenset(_PR_SEGMENTS.values())


@dataclass(frozen=True, kw_only=True)
class RepoSpec:
    """一次会话绑定的仓库与凭据。

    ``platform`` 默认 **Gitee**：既有调用方（含测试）都按 Gitee 建这个对象，不必逐个补参数。
    令牌留空表示「本会话没有可用凭据」—— 工具会回一句可读的提示，而不是抛异常。
    """

    owner: str
    repo: str
    token: str = ""
    git_username: str = ""
    platform: Platform = Platform.GITEE

    def __post_init__(self) -> None:
        # git 用户名留空就按平台默认，建会话处不必每处都写一遍
        if not self.git_username:
            object.__setattr__(self, "git_username", self.platform.default_git_username)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def web_url(self) -> str:
        """仓库的网页地址（不含 .git）——记忆里、回复里都用它。"""
        return f"https://{self.platform.host}/{self.full_name}"


def platform_label(source: object = None) -> str:
    """统一取平台的**展示名**（``Gitee`` / ``GitHub``）。

    接 ``Platform``、``RepoSpec``、库里的字符串（``"gitee"`` / ``"github"``）、或 ``None``；
    认不出的取值一律按 Gitee（与 ``Platform.of`` 同口径，老会话零迁移）。**不会抛异常** ——
    它服务的都是日志与给用户看的文案，那里最不该因为一个畸形取值再炸一次。

    存在的意义：**日志与文案里不许再写死平台名**。同一个仓库换个平台，写死的那句话就全错了
    （2026-09-28 实测：GitHub 仓库的 PR 闸门提示说「合并由人工在 Gitee 上完成」）。要平台名
    就调这里，别自己判、更别拼字符串。
    """
    if isinstance(source, RepoSpec):
        return source.platform.label
    if isinstance(source, Platform):
        return source.label
    return Platform.of(source if isinstance(source, str) else None).label


def parse_repo_url(url: str) -> tuple[Platform, str, str]:
    """解析仓库地址，返回 ``(platform, owner, repo)``。

    支持 ``https://gitee.com/<owner>/<repo>[.git]`` 与 github.com 同形。
    """
    platform = Platform.of_url(url)
    if platform is None:
        raise ValueError(f"只支持 gitee.com / github.com 的仓库地址：{url}")
    path = urlparse(url.strip()).path.strip("/").removesuffix(".git")
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2:
        raise ValueError(f"无法解析仓库地址：{url}")
    return platform, parts[0], parts[1]


def parse_pr_url(url: str) -> tuple[Platform, str, str, int]:
    """解析 PR 地址，返回 ``(platform, owner, repo, number)``。

    Gitee 用 ``/pulls/<n>``、GitHub 用 ``/pull/<n>``；两种写法都认（多认一种写法好过
    为它报一次错）。
    """
    platform = Platform.of_url(url)
    if platform is None:
        raise ValueError(f"只支持 gitee.com / github.com 的 PR 链接：{url}")
    parts = [p for p in urlparse(url.strip()).path.strip("/").split("/") if p]
    if len(parts) < 4 or parts[2] not in _PR_SEGMENTS_ALL:
        raise ValueError(f"无法解析 PR 地址：{url}")
    if not parts[3].isdigit():
        raise ValueError(f"PR 编号非法：{parts[3]}")
    return platform, parts[0], parts[1], int(parts[3])
