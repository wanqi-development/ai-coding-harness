"""``codeplatform``：平台判定、仓库/PR 地址解析、``RepoSpec`` 的默认值。"""

from __future__ import annotations

import pytest

from ai_coding_harness.core.codeplatform import (
    Platform,
    RepoSpec,
    parse_pr_url,
    parse_repo_url,
    platform_label,
)


def test_platform_of_recognizes_values_and_falls_back_to_gitee() -> None:
    assert Platform.of("github") is Platform.GITHUB
    assert Platform.of("GITHUB") is Platform.GITHUB
    assert Platform.of("gitee") is Platform.GITEE
    # 老会话（NULL）与畸形取值一律当 Gitee：迁移零成本，也不会把老会话判到别的平台
    assert Platform.of(None) is Platform.GITEE
    assert Platform.of("") is Platform.GITEE
    assert Platform.of("bitbucket") is Platform.GITEE


def test_platform_of_url_reads_the_host() -> None:
    assert Platform.of_url("https://github.com/acme/demo.git") is Platform.GITHUB
    assert Platform.of_url("https://gitee.com/acme/demo") is Platform.GITEE
    assert Platform.of_url("https://www.github.com/acme/demo") is Platform.GITHUB
    # 自建实例与 SSH 写法都不认（API 工具走不了它们），返回 None 让调用方报清楚
    assert Platform.of_url("https://gitlab.com/acme/demo") is None
    assert Platform.of_url("git@github.com:acme/demo.git") is None


def test_platform_constants_differ_where_it_matters() -> None:
    assert Platform.GITEE.host == "gitee.com"
    assert Platform.GITHUB.host == "github.com"
    assert Platform.GITEE.pr_segment == "pulls"
    assert Platform.GITHUB.pr_segment == "pull"
    assert Platform.GITEE.default_git_username == "oauth2"
    assert Platform.GITHUB.default_git_username == "x-access-token"


def test_parse_repo_url_supports_both_hosts() -> None:
    assert parse_repo_url("https://github.com/acme/demo.git") == (
        Platform.GITHUB,
        "acme",
        "demo",
    )
    assert parse_repo_url("https://gitee.com/acme/demo") == (
        Platform.GITEE,
        "acme",
        "demo",
    )
    with pytest.raises(ValueError):
        parse_repo_url("https://gitlab.com/acme/demo.git")
    with pytest.raises(ValueError):
        parse_repo_url("https://github.com/acme")


def test_parse_pr_url_accepts_both_segments() -> None:
    assert parse_pr_url("https://gitee.com/acme/demo/pulls/12") == (
        Platform.GITEE,
        "acme",
        "demo",
        12,
    )
    assert parse_pr_url("https://github.com/acme/demo/pull/7") == (
        Platform.GITHUB,
        "acme",
        "demo",
        7,
    )
    # 写法串了也认（多认一种写法，好过为它报一次错）
    assert parse_pr_url("https://github.com/acme/demo/pulls/7")[3] == 7
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/acme/demo/issues/7")
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/acme/demo/pull/abc")


def test_repo_spec_defaults_to_gitee_and_fills_git_username() -> None:
    spec = RepoSpec(owner="acme", repo="demo", token="t")
    assert spec.platform is Platform.GITEE  # 既有调用方不写 platform，行为不变
    assert spec.git_username == "oauth2"
    assert spec.full_name == "acme/demo"
    assert spec.web_url == "https://gitee.com/acme/demo"

    gh = RepoSpec(platform=Platform.GITHUB, owner="acme", repo="demo", token="t")
    assert gh.git_username == "x-access-token"
    assert gh.web_url == "https://github.com/acme/demo"

    # 显式给了用户名就不覆盖（PAT 场景用真实用户名）
    assert RepoSpec(owner="a", repo="b", git_username="me").git_username == "me"


def test_build_git_client_dispatches_by_platform() -> None:
    from ai_coding_harness.config import Settings
    from ai_coding_harness.tools.git import build_git_client
    from ai_coding_harness.tools.gitee_client import GiteeClient
    from ai_coding_harness.tools.github_client import GitHubClient

    settings = Settings()
    assert isinstance(
        build_git_client(settings, RepoSpec(owner="o", repo="r")), GiteeClient
    )
    assert isinstance(
        build_git_client(
            settings, RepoSpec(platform=Platform.GITHUB, owner="o", repo="r")
        ),
        GitHubClient,
    )


def test_platform_label_accepts_every_shape_and_never_raises() -> None:
    """平台展示名只从这一个入口取 —— 各种输入都要认，且一律不抛。

    它服务的都是日志与给用户看的文案：那里最不该因为一个畸形取值再炸一次。
    """
    repo = RepoSpec(owner="o", repo="r")
    gh_repo = RepoSpec(platform=Platform.GITHUB, owner="o", repo="r")

    assert platform_label(repo) == "Gitee"
    assert platform_label(gh_repo) == "GitHub"
    assert platform_label(Platform.GITHUB) == "GitHub"
    # 库里的字符串（``sessions.git_platform``）与 HTTP 入参都可能走到这里
    assert platform_label("github") == "GitHub"
    assert platform_label("GITEE") == "Gitee"
    # 空值与认不出的取值一律按 Gitee（与 Platform.of 同一口径），不抛
    assert platform_label(None) == "Gitee"
    assert platform_label("") == "Gitee"
    assert platform_label("gitlab") == "Gitee"
    assert platform_label(42) == "Gitee"

    assert all(platform_label(x) in ("Gitee", "GitHub") for x in (None, 0, [], object()))


def test_missing_token_message_names_the_platform_and_its_field_names() -> None:
    """两个客户端的「令牌未配置」文案由一处生成：平台名与字段名都不许写死。

    以前是两个客户端各写一份（改一处漏一处），而且平台名是硬编码的 —— 现在的口径是
    ``<平台小写>_token``（建会话入参）与 ``<平台大写>_ACCESS_TOKEN``（.env 兜底键）。
    """
    from ai_coding_harness.tools.pr_client import missing_token_message

    gitee = missing_token_message(RepoSpec(owner="o", repo="r"))
    assert gitee.startswith("Gitee access token 未配置")
    assert "gitee_token" in gitee and "GITEE_ACCESS_TOKEN" in gitee

    github = missing_token_message(
        RepoSpec(platform=Platform.GITHUB, owner="o", repo="r")
    )
    assert github.startswith("GitHub access token 未配置")
    assert "github_token" in github and "GITHUB_ACCESS_TOKEN" in github
    assert "Gitee" not in github
