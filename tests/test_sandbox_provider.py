"""``SandboxProvider._upload_tree`` 的资产上传与残留清理单测 —— 全用假客户端，不连真沙箱。

关注两件事：整棵树（含子目录）按相对路径推入；容器里源目录已不存在的残留被清掉，
否则仓库里删掉的 skill 会一直留在容器里被 SkillsMiddleware 当有效 skill 加载。
"""

from __future__ import annotations

import shlex
from pathlib import Path

from ai_coding_harness.config import Settings
from ai_coding_harness.sandbox.client import CommandResult
from ai_coding_harness.sandbox.provider import SandboxProvider

SKILLS_DIR = "/ai_coding/skills"
REPO = Path(__file__).resolve().parents[1]


class FakeClient:
    """按 ``find`` / ``rm`` 两条命令模拟沙箱内文件系统的假客户端。"""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.id = "sb-test"
        self.files: dict[str, str] = dict(files or {})
        self.run_calls: list[str] = []

    async def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    async def run(self, command, *, cwd=None, timeout=None) -> CommandResult:
        self.run_calls.append(command)
        argv = shlex.split(command)
        if argv[0] == "find":
            listed = "\n".join(p for p in sorted(self.files) if p.startswith(f"{argv[1]}/"))
        elif argv[0] == "rm":
            for path in argv[2:]:
                self.files.pop(path, None)
            listed = ""
        elif argv[0] in ("chmod", "git", "mkdir"):
            listed = ""  # 与文件无关的准备工作：成功即可
        else:
            raise AssertionError(f"未预期的命令：{command}")
        return CommandResult(exit_code=0, stdout=listed, stderr="", text=listed)


def make_provider() -> SandboxProvider:
    return SandboxProvider(Settings())


def write_source(root: Path, tree: dict[str, str]) -> Path:
    for rel, content in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


async def test_upload_tree_pushes_nested_files_by_relative_path(tmp_path: Path) -> None:
    src = write_source(
        tmp_path / "skills",
        {
            "code-review/SKILL.md": "main",
            "code-review/references/solid-checklist.md": "ref",
        },
    )

    client = FakeClient()
    await make_provider()._upload_tree(client, src, SKILLS_DIR)

    assert client.files == {
        f"{SKILLS_DIR}/code-review/SKILL.md": "main",
        f"{SKILLS_DIR}/code-review/references/solid-checklist.md": "ref",
    }


async def test_upload_tree_prunes_files_missing_from_source(tmp_path: Path) -> None:
    """仓库里删掉的 skill 必须从容器里清掉，reconnect 走同一条路径也必须清。"""
    src = write_source(tmp_path / "skills", {"code-review/SKILL.md": "new"})

    client = FakeClient(
        {
            f"{SKILLS_DIR}/code-review/SKILL.md": "old",
            f"{SKILLS_DIR}/code-review-expert/SKILL.md": "stale",
            f"{SKILLS_DIR}/code-review-expert/references/solid.md": "stale",
        }
    )
    await make_provider()._upload_tree(client, src, SKILLS_DIR)

    assert set(client.files) == {f"{SKILLS_DIR}/code-review/SKILL.md"}
    assert client.files[f"{SKILLS_DIR}/code-review/SKILL.md"] == "new"
    # 残留清完还要把空目录一起回收，否则容器里会留一堆空壳
    assert any("-empty -delete" in call for call in client.run_calls)


async def test_upload_tree_does_not_rm_when_nothing_is_stale(tmp_path: Path) -> None:
    src = write_source(tmp_path / "skills", {"code-review/SKILL.md": "same"})

    client = FakeClient({f"{SKILLS_DIR}/code-review/SKILL.md": "same"})
    await make_provider()._upload_tree(client, src, SKILLS_DIR)

    assert not [call for call in client.run_calls if call.startswith("rm ")]


# --------------------------------------------------------------------------- Maven


async def test_install_assets_pushes_the_maven_settings() -> None:
    """Maven 的 settings.xml 也是沙箱资产：推到 ``/ai_coding/maven``，
    再由 setup.sh 复制到 Maven 默认读取的位置（``$HOME/.m2/settings.xml``）。"""
    client = FakeClient()

    await make_provider()._install_assets(client)

    assert "/ai_coding/maven/settings.xml" in client.files
    assert "aliyunmaven" in client.files["/ai_coding/maven/settings.xml"]


def test_the_maven_settings_carry_the_mirror_and_the_local_repository() -> None:
    """两个要件都在：阿里云镜像（加速）与卷上的本地仓库（缓存）。"""
    text = (REPO / "sandbox" / "maven" / "settings.xml").read_text(encoding="utf-8")

    assert "<id>aliyunmaven</id>" in text
    assert "<mirrorOf>*</mirrorOf>" in text
    assert "https://maven.aliyun.com/repository/public" in text
    assert "<localRepository>/ai_coding/.m2/repository</localRepository>" in text


def test_the_setup_script_installs_the_maven_settings() -> None:
    """setup.sh 每次都跑（建会话与重连都跑），所以它负责把配置摆到 Maven 读得到的地方。"""
    text = (REPO / "sandbox" / "deploy" / "sandbox" / "setup.sh").read_text(encoding="utf-8")

    assert "mkdir -p /ai_coding/.m2/repository" in text
    assert "/ai_coding/maven/settings.xml" in text
    assert '.m2/settings.xml' in text
