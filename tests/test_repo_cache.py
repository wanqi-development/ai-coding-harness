"""宿主机侧的仓库只读缓存（``repo_cache.py``）：**真拿 git 跑**，不 mock git。

夹具是一个临时 git 仓库（master + 一支 feature 分支 + 带空格/中文的路径 + 二进制文件），
克隆走**本地路径** —— 不需要网络，也不涉及凭据。本机没装 git 时整个文件跳过：这个功能
本来就以「有 git」为前提（面板会明说，见 ``GitMissingError``）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_coding_harness.config import Settings
from ai_coding_harness.repo_cache import (
    GitMissingError,
    RepoSpec,
    RepoViewError,
    _scrub,
    clean_path,
    default_branch,
    fetched_at,
    is_cloned,
    list_branches,
    read_file,
    require_git,
    sync,
    tree,
)
from tests.git_repo import git as _git
from tests.git_repo import hide_remote as _hide_remote
from tests.git_repo import make_origin, requires_git

#: 本机没装 git 时整个文件跳过（与夹具模块同一个判据）
pytestmark = requires_git


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """一个真仓库：master 上一份代码，feature 分支上多一个文件与一处改动。"""
    return make_origin(tmp_path)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(repo_view_dir=str(tmp_path / "cache"))


@pytest.fixture
def spec(origin: Path) -> RepoSpec:
    return RepoSpec(owner="acme", repo="demo", url=str(origin))


class TestSync:
    async def test_a_relative_cache_dir_is_resolved_once(self, tmp_path, monkeypatch, origin):
        """回归：`REPO_VIEW_DIR` 默认是**相对路径**，而每条 git 命令都要换 `cwd` 跑 ——
        相对的目标路径会被 git 相对那个 cwd **再解析一次**，克隆于是落到
        `repo_cache/<owner>/repo_cache/<owner>/<repo>`；随后按 cwd 找 `HEAD` 又找不到，
        下一次克隆就报「destination path … already exists」（真机踩到过一次）。
        缓存根必须是绝对路径，这样「换 cwd 跑 git」不再改变落点。
        """
        monkeypatch.chdir(tmp_path)
        settings = Settings(repo_view_dir="cache")  # 相对路径，正是默认值的形态
        spec = RepoSpec(owner="acme", repo="demo", url=str(origin))

        await sync(settings, spec)

        expected = tmp_path / "cache" / "acme" / "demo"
        assert expected.is_dir(), "克隆要落在 <cwd>/cache/acme/demo"
        assert (expected / "HEAD").is_file()
        assert not (expected / "cache").exists(), "不能再套一层同名目录"
        assert [b.name for b in await list_branches(settings, spec)] == ["master", "feature/x"]

    async def test_a_leftover_directory_with_readonly_files_is_cleared(self, settings, spec, tmp_path):
        """回归：克隆崩在半路会留下**只读**的 git 对象（Windows 上实测 `-ar--`），
        而 `shutil.rmtree(ignore_errors=True)` 会静默失败 —— 下一次 clone 就报
        「already exists and is not an empty directory」。清残留必须真能清掉。
        """
        leftover = tmp_path / "cache" / "acme" / "demo"
        (leftover / "objects" / "pack").mkdir(parents=True)
        readonly = leftover / "objects" / "pack" / "pack-x.pack"
        readonly.write_bytes(b"junk")
        readonly.chmod(0o400)  # 只读：模拟 git 留下的打包对象

        result = await sync(settings, spec)  # 不该报「已存在」，而是清掉重来

        assert result.cloned is True
        assert not readonly.exists()
        assert (leftover / "HEAD").is_file()

    async def test_first_sync_clones_and_the_second_fetches(self, settings, spec, origin):
        first = await sync(settings, spec)

        assert first.cloned is True
        assert first.default_branch == "master", "裸仓的 HEAD 记的就是远程默认分支"
        assert [b.name for b in first.branches] == ["master", "feature/x"]
        assert first.branches[0].is_default is True
        assert first.branches[0].sha and first.branches[0].subject == "首次提交"
        assert fetched_at(settings, spec) is not None

        # 远程多一支分支、master 上多一个提交
        _git(origin, "checkout", "-q", "-b", "feature/y", "master")
        (origin / "y.md").write_text("y\n", encoding="utf-8")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-qm", "y 分支")
        _git(origin, "checkout", "-q", "master")
        (origin / "later.md").write_text("later\n", encoding="utf-8")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-qm", "后来的提交")

        second = await sync(settings, spec)

        assert second.cloned is False
        assert [b.name for b in second.branches] == ["master", "feature/x", "feature/y"], (
            "显式 refspec + prune 才认得出新分支"
        )
        assert second.branches[0].subject == "后来的提交"
        names = {entry.name for entry in await tree(settings, spec, path="")}
        assert "later.md" in names, "fetch 之后 master 的内容跟着变"

    async def test_concurrent_syncs_clone_only_once(self, settings, spec):
        results = await asyncio.gather(sync(settings, spec), sync(settings, spec))

        assert sorted(result.cloned for result in results) == [False, True], (
            "同一仓库的 clone 互斥：第二个进来时已经克隆好了，只做 fetch"
        )

    async def test_a_failed_clone_leaves_no_half_baked_directory(self, settings, tmp_path):
        spec = RepoSpec(owner="acme", repo="demo", url=str(tmp_path / "nope"))

        with pytest.raises(RepoViewError):
            await sync(settings, spec)

        assert not is_cloned(settings, spec)
        assert not (tmp_path / "cache" / "acme" / "demo").exists(), "半成品目录要清掉，否则下次克隆直接失败"

    async def test_reading_never_touches_the_remote(self, settings, spec, origin):
        await sync(settings, spec)
        _hide_remote(origin)

        branches = await list_branches(settings, spec)
        entries = await tree(settings, spec, path="docs")
        content = await read_file(settings, spec, path="readme.md")

        assert [b.name for b in branches] == ["master", "feature/x"]
        assert [entry.name for entry in entries] == ["guide.md"]
        assert content.text == "# hello\n"

    async def test_fetch_really_goes_to_the_remote(self, settings, spec, origin):
        await sync(settings, spec)
        _hide_remote(origin)

        with pytest.raises(RepoViewError):
            await sync(settings, spec)


class TestBranches:
    async def test_before_cloning_there_is_nothing(self, settings, spec):
        assert is_cloned(settings, spec) is False
        assert await list_branches(settings, spec) == []
        assert fetched_at(settings, spec) is None

    async def test_default_branch_comes_from_the_bare_head(self, settings, spec, origin):
        await sync(settings, spec)
        assert await default_branch(settings, spec) == "master"


class TestTree:
    async def test_root_lists_dirs_first_then_files_with_sizes(self, settings, spec):
        await sync(settings, spec)

        entries = await tree(settings, spec, path="")

        assert [(e.name, e.type) for e in entries] == [
            ("docs", "dir"),
            ("有 空格的目录", "dir"),
            ("big.txt", "file"),
            ("blob.bin", "file"),
            ("readme.md", "file"),
        ]
        by_name = {entry.name: entry for entry in entries}
        assert by_name["readme.md"].size == len(b"# hello\n")
        assert by_name["docs"].size is None
        assert by_name["readme.md"].path == "readme.md", "路径是相对仓库根的完整路径"

    async def test_subdirectory_paths_join_with_the_parent(self, settings, spec):
        await sync(settings, spec)

        entries = await tree(settings, spec, path="有 空格的目录")

        assert entries[0].name == "中文.txt"
        assert entries[0].path == "有 空格的目录/中文.txt"

    async def test_the_ref_decides_which_branch_is_read(self, settings, spec):
        await sync(settings, spec)

        master = {entry.name for entry in await tree(settings, spec, ref="master", path="")}
        feature = {entry.name for entry in await tree(settings, spec, ref="feature/x", path="")}

        assert "feature.md" not in master
        assert "feature.md" in feature


class TestFile:
    async def test_text_file_and_the_ref_it_came_from(self, settings, spec):
        await sync(settings, spec)

        content = await read_file(settings, spec, path="readme.md")

        assert content.ref == "master", "不给 ref 就是默认分支"
        assert content.text == "# hello\n"
        assert content.size == len(b"# hello\n")
        assert (content.binary, content.too_big) == (False, False)

    async def test_the_same_path_differs_between_branches(self, settings, spec):
        await sync(settings, spec)

        feature = await read_file(settings, spec, ref="feature/x", path="readme.md")

        assert "feature 里改过的" in (feature.text or "")

    async def test_too_big_only_reports_the_size(self, tmp_path, origin, spec):
        settings = Settings(repo_view_dir=str(tmp_path / "cache"), repo_view_file_max_bytes=10)
        await sync(settings, spec)

        content = await read_file(settings, spec, path="big.txt")

        assert content.too_big is True
        assert content.text is None
        assert content.size == 500

    async def test_binary_is_not_sent_as_text(self, settings, spec):
        await sync(settings, spec)

        content = await read_file(settings, spec, path="blob.bin")

        assert content.binary is True
        assert content.text is None

    async def test_a_directory_is_not_a_file(self, settings, spec):
        await sync(settings, spec)

        with pytest.raises(RepoViewError, match="目录"):
            await read_file(settings, spec, path="docs")

    async def test_a_missing_path_says_so(self, settings, spec):
        await sync(settings, spec)

        with pytest.raises(RepoViewError, match="不在"):
            await read_file(settings, spec, path="nope.md")

    async def test_before_cloning_it_tells_you_to_sync(self, settings, spec):
        with pytest.raises(RepoViewError, match="还没下载"):
            await read_file(settings, spec, path="readme.md")
        with pytest.raises(RepoViewError, match="还没下载"):
            await tree(settings, spec, path="")


class TestGuards:
    @pytest.mark.parametrize(
        "bad",
        ["../readme.md", "docs/../../etc/passwd", "/etc/passwd", r"C:\Windows\win.ini", r"a\b"],
    )
    def test_dangerous_paths_are_rejected_outright(self, bad):
        with pytest.raises(RepoViewError):
            clean_path(bad)

    def test_harmless_dots_are_normalised(self):
        assert clean_path("./docs/./guide.md") == "docs/guide.md"
        assert clean_path("") == ""

    async def test_a_traversal_path_never_reaches_git(self, settings, spec):
        await sync(settings, spec)

        with pytest.raises(RepoViewError):
            await read_file(settings, spec, path="../readme.md")
        with pytest.raises(RepoViewError):
            await tree(settings, spec, path="..")

    async def test_refs_are_whitelisted(self, settings, spec):
        await sync(settings, spec)

        with pytest.raises(RepoViewError, match="分支名不合法"):
            await tree(settings, spec, ref="-c", path="")
        with pytest.raises(RepoViewError, match="没有分支"):
            await tree(settings, spec, ref="nosuch", path="")

    async def test_the_owner_and_repo_are_used_as_paths_so_they_are_validated(self, settings):
        with pytest.raises(RepoViewError, match="不合法"):
            await sync(settings, RepoSpec(owner="..", repo="demo", url="x"))
        with pytest.raises(RepoViewError, match="不合法"):
            await sync(settings, RepoSpec(owner="acme", repo="a/b", url="x"))


class TestCredentials:
    def test_the_auth_header_falls_back_to_the_repo_platform_host(self):
        """URL 解析不出 host 时，兜底域名要跟着**平台**走。

        回归：这里曾经写死 ``or "gitee.com"`` —— GitHub 仓库（会话里 URL 畸形 / 空时）会
        把认证头挂到 gitee.com 上，等于**静默失效**：git 照样去 github 拉，却带着一份用不上
        的凭据。
        """
        from ai_coding_harness.codeplatform import Platform
        from ai_coding_harness.repo_cache import _auth_env

        gitee = _auth_env(RepoSpec(owner="a", repo="b", url="", token="t"))
        github = _auth_env(
            RepoSpec(owner="a", repo="b", url="", token="t", platform=Platform.GITHUB)
        )

        assert "http.https://gitee.com/.extraheader" in gitee["GIT_CONFIG_KEY_0"]
        assert "http.https://github.com/.extraheader" in github["GIT_CONFIG_KEY_0"]

    def test_the_token_is_scrubbed_from_the_text_we_hand_out(self):
        spec = RepoSpec(owner="acme", repo="demo", url="https://gitee.com/acme/demo.git", token="s3cr3t")

        scrubbed = _scrub("fatal: https://oauth2:s3cr3t@gitee.com/acme/demo.git 认证失败", spec)

        assert "s3cr3t" not in scrubbed
        assert "***" in scrubbed

    async def test_a_clone_with_a_token_keeps_it_out_of_the_git_config(self, settings, origin, tmp_path):
        """令牌拼进 URL 的最大风险是 git 把它记进 ``remote.origin.url`` —— 这里钉死。"""
        spec = RepoSpec(
            owner="acme", repo="demo", url=str(origin), token="s3cr3t", git_username="oauth2"
        )

        await sync(settings, spec)

        config = (tmp_path / "cache" / "acme" / "demo" / "config").read_text("utf-8")
        assert "s3cr3t" not in config
        # git 写配置时会把 Windows 路径里的反斜杠转义成 `\\`，比对前先还原
        assert str(origin) in config.replace("\\\\", "\\"), "remote.origin.url 仍是干净地址"


class TestEventLoopIndependence:
    """回归：**后端的事件循环不支持子进程**（Windows + SelectorEventLoop，psycopg 的要求），
    而 ``asyncio.create_subprocess_exec`` 在那上面直接抛 ``NotImplementedError``。

    实测过一次线上故障：面板点「同步」返回 500，`api.log` 里只有一行 ``NotImplementedError``
    指到 ``create_subprocess_exec``。所以 git 一律走线程里的 ``subprocess.run`` —— 这条用例
    在 Selector 循环里真跑一次同步，把它钉住（Linux 上 Selector 本来就支持子进程，故只在
    Windows 才有回归意义，但两边都跑无妨）。
    """

    def test_sync_works_on_a_selector_event_loop(self, settings, spec):
        loop = asyncio.SelectorEventLoop()
        try:
            result = loop.run_until_complete(sync(settings, spec))
        finally:
            loop.close()

        assert result.cloned is True
        assert [b.name for b in result.branches] == ["master", "feature/x"]


class TestGitDiscovery:
    def test_a_configured_git_wins(self):
        settings = Settings(repo_view_git="/opt/git/bin/git")
        assert require_git(settings) == "/opt/git/bin/git"

    def test_no_git_anywhere_gives_a_readable_error(self, monkeypatch):
        monkeypatch.setattr("ai_coding_harness.repo_cache.find_git", lambda settings: None)

        with pytest.raises(GitMissingError, match="REPO_VIEW_GIT"):
            require_git(Settings())
