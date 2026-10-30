"""Sandbox lifecycle — create a sandbox per session and initialize its layout."""

from __future__ import annotations

import logging
import shlex
from datetime import timedelta
from pathlib import Path

from opensandbox import Sandbox
from opensandbox.exceptions import (
    SandboxApiException,
    SandboxInternalException,
    SandboxUnhealthyException,
)
from opensandbox.models.sandboxes import Volume

from ai_coding_harness.config import Settings
from ai_coding_harness.sandbox.client import SandboxClient, build_connection_config

logger = logging.getLogger(__name__)

# 仓库根：配置里的资产源路径都相对它解析
_REPO_ROOT = Path(__file__).resolve().parents[3]

# 销毁路径专用：沙箱已经不存在，等于销毁成功。
# 不能复用 proxy 的可恢复状态码表 —— 那里含 502/503，而 502/503 在这里表示网关挂了、
# 沙箱可能还在，吞掉会漏掉一个真容器，与「已经没了」语义完全不同。
GONE_API_STATUS = frozenset({404, 410})


def _repo_path(value: str) -> Path:
    """把配置里的源路径解析为绝对路径（绝对路径原样返回）。"""
    return _REPO_ROOT / value


def _read_asset(path: Path) -> str:
    """读资产文件为文本；以文本模式读取会把 CRLF 归一为 LF，避免脚本在 Linux 上跑不起来。"""
    if not path.is_file():
        raise RuntimeError(f"沙箱资产缺失：{path}")
    return path.read_text(encoding="utf-8")


class SandboxProvider:
    """Creates and destroys per-session OpenSandbox sandboxes."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connection_config = build_connection_config(settings)

    async def create(
        self,
        volumes: list[Volume] | None = None,
        env: dict[str, str] | None = None,
        *,
        session_id: str | None = None,
    ) -> SandboxClient:
        """Create a sandbox and initialize it.

        Docker provider 用默认 entrypoint 覆盖镜像 ENTRYPOINT，且不支持 lifecycle
        hooks，故镜像的初始化逻辑改由**创建后经 `execute` 执行 setup 脚本**
        （``sandbox_setup_command``，读沙箱内 ``$GIT_TOKEN`` 写 ``.secrets``）完成。
        """
        del session_id
        sandbox = await Sandbox.create(
            self._settings.sandbox_image,
            connection_config=self._connection_config,
            timeout=timedelta(minutes=self._settings.sandbox_timeout_minutes),
            volumes=volumes,
            env=env,
        )
        client = SandboxClient(sandbox)
        try:
            await self.initialize(client)
        except Exception:
            await self.destroy(client)
            raise
        return client

    async def reconnect(
        self,
        sandbox_id: str,
        *,
        session_id: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxClient | None:
        """按 id 重连到仍在运行的沙箱；沙箱已不存在或不可用时返回 ``None``。

        重连比重建便宜得多：容器与容器内文件系统原样保留。但容器若被重启过，
        ``.secrets`` 等非卷内容会丢失，故重连成功后仍重跑一次初始化（幂等）。
        """
        del session_id, env
        try:
            sandbox = await Sandbox.connect(
                sandbox_id,
                connection_config=self._connection_config,
                skip_health_check=True,
            )
            client = SandboxClient(sandbox)
            await self.initialize(client)
        # 重连是「尽力而为」：任何失败（含认证、初始化）都退回 None，由调用方重建一个新容器
        except Exception:  # noqa: BLE001
            return None
        return client

    async def initialize(self, client: SandboxClient) -> None:
        """初始化沙箱内的运行环境（幂等：建目录、推资产、写凭据）。"""
        await self._init_working_dirs(client)
        await self._install_assets(client)
        await self._run_setup(client)

    async def install_runtime_assets(self, client: SandboxClient) -> None:
        """Refresh scripts on an already-running sandbox before credential setup."""
        await self._install_scripts(client)

    async def _install_assets(self, client: SandboxClient) -> None:
        """把仓库里的 skill / 规则 / 凭据脚本推进沙箱。

        镜像不再预置这些内容，而容器被重建后非卷文件会丢失，所以 create 与 reconnect
        两条路径都要跑（幂等：覆盖写）。上传目标直接取自配置 —— 配置指向哪个路径，
        推的就是哪个路径，两者不会漂移。
        """
        settings = self._settings
        await self._install_scripts(client)

        await self._upload_tree(
            client,
            _repo_path(settings.sandbox_local_skills_dir),
            settings.sandbox_skills_dir,
        )
        await self._upload_tree(
            client,
            _repo_path(settings.sandbox_local_policies_dir),
            settings.sandbox_policies_dir,
        )
        # Maven 的 settings.xml（阿里云镜像 + 本地仓库路径）：推到位之后由 setup.sh 复制到
        # ``$HOME/.m2/settings.xml`` —— 那个位置 Maven 默认就会读，命令不用加 `-s`。
        await self._upload_tree(
            client,
            _repo_path(settings.sandbox_local_maven_dir),
            settings.sandbox_maven_dir,
        )

        identity = await client.run(
            f"git config --system user.name {shlex.quote(settings.sandbox_git_user_name)}"
            f" && git config --system user.email {shlex.quote(settings.sandbox_git_user_email)}"
        )
        if not identity.ok:
            raise RuntimeError(
                f"sandbox git identity failed: {identity.stderr or identity.text}"
            )

    async def _install_scripts(self, client: SandboxClient) -> None:
        """Install only the small askpass/setup scripts used by credential refresh."""
        settings = self._settings
        scripts = [settings.git_askpass_path]
        setup = settings.sandbox_setup_command.strip()
        if setup:
            scripts.append(setup)

        assets = _repo_path(settings.sandbox_assets_dir)
        for target in scripts:
            await client.write_file(target, _read_asset(assets / Path(target).name))
        result = await client.run(f"chmod 0755 {' '.join(scripts)}")
        if not result.ok:
            raise RuntimeError(
                f"sandbox assets not executable: {result.stderr or result.text}"
            )

    async def _upload_tree(
        self, client: SandboxClient, source: Path, dest: str
    ) -> None:
        """把本地目录整棵推入沙箱（覆盖写），并清掉源里已不存在的残留。

        只覆盖写不清理的话，仓库里删掉的 skill 会一直留在容器里被
        ``SkillsMiddleware`` 当有效 skill 加载；而 reconnect 走的是同一条上传路径，
        同样治不了这个残留，所以每次上传后都对齐一次整棵树。
        """
        if not source.is_dir():
            raise RuntimeError(f"沙箱资产目录不存在：{source}")
        dest = dest.rstrip("/")
        expected: set[str] = set()
        for path in sorted(source.rglob("*")):
            if path.is_file():
                rel = path.relative_to(source).as_posix()
                expected.add(rel)
                await client.write_file(f"{dest}/{rel}", _read_asset(path))
        await self._prune_tree(client, dest, expected)

    async def _prune_tree(
        self, client: SandboxClient, dest: str, expected: set[str]
    ) -> None:
        """删掉 ``dest`` 下不在 ``expected`` 内的文件，并回收随之变空的目录。"""
        listing = await client.run(f"find {shlex.quote(dest)} -type f")
        if not listing.ok:
            raise RuntimeError(
                f"sandbox asset listing failed: {listing.stderr or listing.text}"
            )
        prefix = f"{dest}/"
        stale = [
            line
            for raw in listing.stdout.splitlines()
            if (line := raw.strip()).startswith(prefix)
            and line[len(prefix) :] not in expected
        ]
        if not stale:
            return
        args = " ".join(shlex.quote(path) for path in stale)
        removed = await client.run(f"rm -f {args}")
        if not removed.ok:
            raise RuntimeError(
                f"sandbox asset cleanup failed: {removed.stderr or removed.text}"
            )
        # 文件删了目录还留着，下次 ls 会多出空目录噪音，一并回收
        await client.run(f"find {shlex.quote(dest)} -mindepth 1 -type d -empty -delete")
        logger.info("清理沙箱残留资产 %d 项：%s", len(stale), dest)

    async def _run_setup(self, client: SandboxClient) -> None:
        """执行初始化脚本（幂等：把注入的令牌写成凭据文件）。"""
        cmd = self._settings.sandbox_setup_command.strip()
        if not cmd:
            return
        result = await client.run(cmd)
        if not result.ok:
            raise RuntimeError(f"sandbox setup failed: {result.stderr or result.text}")

    async def _init_working_dirs(self, client: SandboxClient) -> None:
        dirs = [
            self._settings.sandbox_project_dir,
            self._settings.sandbox_policies_dir,
            self._settings.sandbox_skills_dir,
            self._settings.sandbox_maven_dir,
            self._settings.sandbox_maven_cache_mount_path,
            self._settings.sandbox_output_dir,
            self._settings.sandbox_review_dir,
            self._settings.sandbox_tmp_dir,
        ]
        result = await client.run(f"mkdir -p {' '.join(dirs)}")
        if not result.ok:
            raise RuntimeError(
                f"failed to initialize sandbox directories: "
                f"{result.stderr or result.text}"
            )

    async def destroy(self, client: SandboxClient) -> None:
        """Terminate a sandbox created by this provider."""
        await client.destroy()

    async def terminate(self, sandbox_id: str) -> None:
        """按 id 销毁沙箱，不初始化、不续期。

        用于「手上没有 client」的销毁路径：会话不在内存里（进程重启过）时删会话，
        只有库里记的 sandbox_id 可依。这里刻意不调 ``initialize``——那会推一遍资产、
        跑一次 setup，对着一个正要被杀掉的容器做这些纯属浪费。

        沙箱已被 OpenSandbox 侧回收（404/410）视为成功：目标状态已经达成。
        """
        try:
            sandbox = await Sandbox.connect(
                sandbox_id,
                connection_config=self._connection_config,
                skip_health_check=True,
            )
            await SandboxClient(sandbox).destroy()
        except SandboxApiException as exc:
            if exc.status_code not in GONE_API_STATUS:
                raise
            logger.info("沙箱已不存在，跳过销毁：sandbox=%s", sandbox_id)
        except (SandboxInternalException, SandboxUnhealthyException):
            # 连不上（容器不在了、网关不通）：同样按已达成目标处理。库里的记录即将失效，
            # 抛出去只会让删会话失败，而失败并不能让沙箱回来。
            logger.warning("按 id 销毁沙箱时连不上，按已销毁处理：sandbox=%s", sandbox_id)
