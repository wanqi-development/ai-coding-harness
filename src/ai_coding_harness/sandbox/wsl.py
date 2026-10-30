"""WSL2 + bubblewrap local sandbox for the standalone deployment."""

from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ai_coding_harness.config import Settings
from ai_coding_harness.sandbox.client import CommandResult

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class WslEntryInfo:
    path: str
    is_dir: bool
    size: int = 0


class WslSandboxClient:
    """Sandbox client whose files live in one WSL session directory."""

    def __init__(
        self,
        *,
        settings: Settings,
        session_id: str,
        root: Path,
    ) -> None:
        self._settings = settings
        self._session_id = session_id
        self._root = root
        user = settings.wsl_user.strip()
        if not user or "/" in user or user in (".", ".."):
            raise ValueError("invalid WSL sandbox user")
        self._home = f"/home/{user}"

    @property
    def id(self) -> str:
        return f"wsl:{self._session_id}"

    @property
    def root(self) -> Path:
        return self._root

    async def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: timedelta | None = None,
        envs: dict[str, str] | None = None,
    ) -> CommandResult:
        args = self._bwrap_command(command=command, cwd=cwd)
        env = {
            "HOME": self._home,
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": os.getenv("LANG", "C.UTF-8"),
            "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
            "TERM": os.getenv("TERM", "dumb"),
            **(envs or {}),
        }
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        seconds = (
            timeout.total_seconds()
            if timeout is not None
            else float(self._settings.wsl_command_timeout_seconds)
        )
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(), timeout=max(1.0, seconds)
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return CommandResult(
                exit_code=124,
                stdout="",
                stderr=f"command timed out after {seconds:.0f}s",
                text=f"command timed out after {seconds:.0f}s",
            )
        stdout = stdout_raw.decode("utf-8", errors="replace")
        stderr = stderr_raw.decode("utf-8", errors="replace")
        text = stdout + (("\n" + stderr) if stderr else "")
        return CommandResult(
            exit_code=process.returncode,
            stdout=stdout.rstrip("\n"),
            stderr=stderr.rstrip("\n"),
            text=text.rstrip("\n"),
        )

    async def is_healthy(self) -> bool:
        return self._root.is_dir() and Path(self._settings.wsl_bwrap).is_file()

    async def renew(self, timeout: timedelta) -> datetime:
        return datetime.now(UTC) + timeout

    async def renew_if_due(self) -> None:
        """WSL workspace has no TTL; keep the proxy-compatible interface."""

    async def write_file(self, path: str, content: str | bytes) -> None:
        target = self._physical(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")

    async def read_file(self, path: str) -> str:
        return self._physical(path).read_text(encoding="utf-8")

    async def list_dir(
        self, path: str, *, depth: int | None = None
    ) -> list[WslEntryInfo]:
        root = self._physical(path)
        if not root.is_dir():
            return []
        items: list[WslEntryInfo] = []
        for child in sorted(root.iterdir()):
            virtual = self._virtual(child)
            items.append(
                WslEntryInfo(
                    path=virtual,
                    is_dir=child.is_dir(),
                    size=0 if child.is_dir() else child.stat().st_size,
                )
            )
            if (depth is None or depth > 1) and child.is_dir():
                items.extend(
                    await self.list_dir(
                        virtual, depth=None if depth is None else depth - 1
                    )
                )
        return items

    async def search(self, path: str, pattern: str) -> list[WslEntryInfo]:
        root = self._physical(path)
        return [
            WslEntryInfo(
                path=self._virtual(item),
                is_dir=item.is_dir(),
                size=0 if item.is_dir() else item.stat().st_size,
            )
            for item in sorted(root.rglob(pattern))
        ]

    async def destroy(self) -> None:
        if self._root.is_dir():
            shutil.rmtree(self._root)

    def _physical(self, virtual_path: str) -> Path:
        normalized = posixpath.normpath(virtual_path)
        mappings = (
            ("/ai_coding", self._root / "ai_coding"),
            (self._home, self._root / "home"),
            ("/usr/local/sbin", self._root / "sbin"),
        )
        for prefix, physical_root in mappings:
            if virtual_path.startswith(prefix) and not (
                normalized == prefix or normalized.startswith(prefix + "/")
            ):
                raise ValueError(f"path escapes WSL sandbox: {virtual_path}")
            if normalized == prefix or normalized.startswith(prefix + "/"):
                suffix = normalized[len(prefix) :].lstrip("/")
                candidate = (physical_root / suffix).resolve()
                allowed = physical_root.resolve()
                if candidate != allowed and allowed not in candidate.parents:
                    raise ValueError(f"path escapes WSL sandbox: {virtual_path}")
                return candidate
        raise ValueError(f"unsupported WSL sandbox path: {virtual_path}")

    def _virtual(self, physical_path: Path) -> str:
        for subdir, prefix in (
            ("ai_coding", "/ai_coding"),
            ("home", self._home),
            ("sbin", "/usr/local/sbin"),
        ):
            root = (self._root / subdir).resolve()
            value = physical_path.resolve()
            if value == root:
                return prefix
            try:
                relative = value.relative_to(root).as_posix()
            except ValueError:
                continue
            return f"{prefix}/{relative}"
        return str(physical_path)

    def _bwrap_command(self, *, command: str, cwd: str | None) -> list[str]:
        bwrap = self._settings.wsl_bwrap
        args = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--hostname",
            "harness-sandbox",
        ]
        if not self._settings.wsl_allow_network:
            args.append("--unshare-net")
        for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt"):
            if Path(path).exists():
                args.extend(["--ro-bind", path, path])
        args.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])
        args.extend(["--dir", "/home", "--dir", self._home])
        args.extend(["--bind", str(self._root / "home"), self._home])
        args.extend(["--dir", "/ai_coding"])
        for name in (
            "project",
            "policies",
            "skills",
            "maven",
            "output",
            "review",
            "tmp",
            ".secrets",
            ".m2",
        ):
            source = self._root / "ai_coding" / name
            target = f"/ai_coding/{name}"
            args.extend(["--dir", target])
            flag = "--ro-bind" if name in ("policies", "skills", "maven") else "--bind"
            args.extend([flag, str(source), target])
        args.extend(["--dir", "/usr/local/sbin"])
        args.extend(["--bind", str(self._root / "sbin"), "/usr/local/sbin"])
        args.extend(["--chdir", cwd or "/ai_coding/project"])
        args.extend(["/bin/bash", "-lc", command])
        return args


class WslSandboxProvider:
    """Provision persistent per-session workspaces inside WSL2."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._root = Path(settings.wsl_root).expanduser().resolve()

    async def create(
        self,
        volumes: list[Any] | None = None,
        env: dict[str, str] | None = None,
        *,
        session_id: str | None = None,
    ) -> WslSandboxClient:
        del volumes
        sid = session_id or self._session_id_from_env(env)
        client = self._client(sid)
        self._create_layout(client.root)
        await self.initialize(client, env=env or {})
        return client

    async def reconnect(
        self,
        sandbox_id: str,
        *,
        session_id: str | None = None,
        env: dict[str, str] | None = None,
    ) -> WslSandboxClient | None:
        sid = session_id or sandbox_id.removeprefix("wsl:")
        client = self._client(sid)
        if not client.root.is_dir():
            return None
        self._create_layout(client.root)
        await self.initialize(client, env=env or {})
        return client

    async def initialize(
        self, client: WslSandboxClient, *, env: dict[str, str] | None = None
    ) -> None:
        self._create_layout(client.root)
        self._install_assets(client)
        await client.run(
            "git config --global user.name "
            + shlex.quote(self._settings.sandbox_git_user_name)
            + " && git config --global user.email "
            + shlex.quote(self._settings.sandbox_git_user_email),
            envs=env,
        )
        command = self._settings.sandbox_setup_command.strip()
        if command:
            result = await client.run(command, envs=env)
            if not result.ok:
                raise RuntimeError(
                    f"sandbox setup failed: {result.stderr or result.text}"
                )

    async def install_runtime_assets(self, client: WslSandboxClient) -> None:
        self._install_scripts(client)

    async def destroy(self, client: WslSandboxClient) -> None:
        await client.destroy()

    async def terminate(self, sandbox_id: str) -> None:
        sid = sandbox_id.removeprefix("wsl:")
        root = (self._root / sid).resolve()
        if root != self._root and self._root in root.parents and root.is_dir():
            shutil.rmtree(root)

    def _client(self, session_id: str) -> WslSandboxClient:
        if not session_id or "/" in session_id or session_id in (".", ".."):
            raise ValueError("invalid WSL sandbox session id")
        return WslSandboxClient(
            settings=self._settings,
            session_id=session_id,
            root=self._root / session_id,
        )

    def _create_layout(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        for name in ("home", "sbin"):
            (root / name).mkdir(parents=True, exist_ok=True)
        coding = root / "ai_coding"
        coding.mkdir(parents=True, exist_ok=True)
        for name in (
            "project",
            "policies",
            "skills",
            "maven",
            "output",
            "review",
            "tmp",
            ".secrets",
            ".m2",
        ):
            (coding / name).mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        os.chmod(coding / ".secrets", 0o700)

    def _install_assets(self, client: WslSandboxClient) -> None:
        self._install_scripts(client)
        copies = (
            (self._settings.sandbox_local_skills_dir, client.root / "ai_coding" / "skills"),
            (
                self._settings.sandbox_local_policies_dir,
                client.root / "ai_coding" / "policies",
            ),
            (self._settings.sandbox_local_maven_dir, client.root / "ai_coding" / "maven"),
        )
        for raw_source, target in copies:
            source = _repo_path(raw_source)
            if not source.is_dir():
                raise RuntimeError(f"沙箱资产目录不存在：{source}")
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)

    def _install_scripts(self, client: WslSandboxClient) -> None:
        assets = _repo_path(self._settings.sandbox_assets_dir)
        targets = [self._settings.git_askpass_path]
        setup = self._settings.sandbox_setup_command.strip()
        if setup:
            targets.append(setup)
        for target in targets:
            destination = client._physical(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = assets / Path(target).name
            if not source.is_file():
                raise RuntimeError(f"沙箱脚本缺失：{source}")
            content = source.read_text(encoding="utf-8").replace("\r\n", "\n")
            destination.write_text(content, encoding="utf-8", newline="\n")
            os.chmod(destination, 0o755)

    @staticmethod
    def _session_id_from_env(env: dict[str, str] | None) -> str:
        session_id = str((env or {}).get("HARNESS_SESSION_ID") or "").strip()
        if not session_id:
            raise ValueError("WSL sandbox create requires HARNESS_SESSION_ID")
        return session_id


def _repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _REPO_ROOT / path
