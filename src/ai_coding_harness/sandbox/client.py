"""OpenSandbox SDK wrapper — connection config and a thin sandbox client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.filesystem import DirectoryListEntry, EntryInfo, SearchEntry

from ai_coding_harness.config import Settings


def build_connection_config(settings: Settings) -> ConnectionConfig:
    """Build an OpenSandbox ``ConnectionConfig`` from application settings."""
    return ConnectionConfig(
        domain=settings.opensandbox_domain or None,
        api_key=settings.opensandbox_api_key or None,
        request_timeout=timedelta(seconds=settings.opensandbox_request_timeout_seconds),
    )


@dataclass
class CommandResult:
    """Normalized result of a sandbox command execution."""

    exit_code: int | None
    stdout: str
    stderr: str
    text: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class SandboxClient:
    """Thin wrapper over ``opensandbox.Sandbox`` with normalized helpers."""

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox

    @property
    def id(self) -> str:
        return self._sandbox.id

    async def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: timedelta | None = None,
    ) -> CommandResult:
        """Run a shell command in the sandbox and return a normalized result."""
        opts = RunCommandOpts(working_directory=cwd, timeout=timeout)
        execution = await self._sandbox.commands.run(command, opts=opts)
        stdout = "\n".join(m.text.rstrip("\n") for m in execution.logs.stdout)
        stderr = "\n".join(m.text.rstrip("\n") for m in execution.logs.stderr)
        return CommandResult(
            exit_code=execution.exit_code,
            stdout=stdout,
            stderr=stderr,
            text=execution.text,
        )

    async def is_healthy(self) -> bool:
        """探活：沙箱是否仍存在且可响应（SDK 内部按 id ping 健康服务）。"""
        return await self._sandbox.is_healthy()

    async def renew(self, timeout: timedelta) -> datetime:
        """续期：把沙箱的到期时间设为「现在 + ``timeout``」，返回新的到期时间（UTC）。

        SDK 的语义是**重设**而不是叠加 —— 每次续期都以调用时刻为起点算 ``timeout``，
        所以传同一个 TTL 就是「从现在起再活这么久」。
        """
        response = await self._sandbox.renew(timeout)
        return response.expires_at

    async def write_file(self, path: str, content: str) -> None:
        """Write text content to a file in the sandbox."""
        await self._sandbox.files.write_file(path, content)

    async def read_file(self, path: str) -> str:
        """Read a text file from the sandbox."""
        return await self._sandbox.files.read_file(path)

    async def list_dir(self, path: str, *, depth: int | None = None) -> list[EntryInfo]:
        """List directory contents, optionally limited to a child depth."""
        return await self._sandbox.files.list_directory(
            DirectoryListEntry(path=path, depth=depth)
        )

    async def search(self, path: str, pattern: str) -> list[EntryInfo]:
        """Search for files matching a glob pattern under ``path``."""
        return await self._sandbox.files.search(
            SearchEntry(path=path, pattern=pattern)
        )

    async def destroy(self) -> None:
        """Terminate the sandbox and close local resources."""
        await self._sandbox.destroy()
