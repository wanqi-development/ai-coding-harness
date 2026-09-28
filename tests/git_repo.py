"""真 git 的测试辅助：临时仓库夹具、以及这台机器上踩到的两个坑。

``repo_cache``（宿主机侧的只读缓存）与它的接口测试都要「一个真仓库」，所以夹具放在这里
共用一份 —— 两个测试文件各造一遍会在细节上漂（分支名、路径里的空格与中文…）。

两个坑：

- **提交要有身份**：全局 ``user.email`` 不一定配了，所以每条 git 都显式带 ``-c``；
- **Windows 上 git 的松散对象是只读的**：``shutil.rmtree`` 会以 WinError 5 失败，
  所以「让远程消失」用的是**改名**而不是删除（见 :func:`hide_remote`）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

#: 这台机器上的 git。``shutil.which`` 只看 PATH，Windows 上没加进 PATH 是常态，故兜底找。
GIT: str | None = shutil.which("git") or next(
    (
        hint
        for hint in (
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files (x86)\Git\cmd\git.exe",
            r"C:\Program Files\Git\bin\git.exe",
        )
        if Path(hint).is_file()
    ),
    None,
)

#: 没装 git 就跳过：这些用例本来就以「有 git」为前提
requires_git = pytest.mark.skipif(GIT is None, reason="本机没有 git")


def git(repo: Path, *args: str, check: bool = True) -> str:
    """在 ``repo`` 里跑一条 git，返回 stdout。``check=True`` 时失败即断言失败。"""
    assert GIT is not None
    proc = subprocess.run(
        [
            GIT,
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=tester",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check:
        assert proc.returncode == 0, f"git {args} 失败：{proc.stderr}"
    return proc.stdout


def make_origin(root: Path) -> Path:
    """造一个真仓库（``root/origin``）：master 一份代码 + ``feature/x`` 一支分支。

    刻意带上「带空格与中文的目录」「二进制文件」「大文件」—— 这三样正是文件浏览最容易
    出错的地方。
    """
    repo = root / "origin"
    repo.mkdir()
    git(repo, "init", "-q")
    # 不依赖 init.defaultBranch：显式把 HEAD 指到 master（老版本 git 也认）
    git(repo, "symbolic-ref", "HEAD", "refs/heads/master")
    (repo / "readme.md").write_text("# hello\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    (repo / "有 空格的目录").mkdir()
    (repo / "有 空格的目录" / "中文.txt").write_text("中文内容\n", encoding="utf-8")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    (repo / "big.txt").write_text("x" * 500, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "首次提交")
    git(repo, "checkout", "-q", "-b", "feature/x")
    (repo / "feature.md").write_text("feature only\n", encoding="utf-8")
    (repo / "readme.md").write_text("# hello\n\nfeature 里改过的\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feature 改动")
    git(repo, "checkout", "-q", "master")
    return repo


def hide_remote(repo: Path) -> Path:
    """把「远程」挪走，用来证明某条路径**根本没有**去碰它（改名，不是删除）。"""
    moved = repo.with_name(repo.name + "-moved")
    repo.rename(moved)
    return moved
