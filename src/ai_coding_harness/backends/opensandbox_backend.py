"""OpenSandbox-backed implementation of deepagents' ``SandboxBackendProtocol``.

Subclasses :class:`~deepagents.backends.sandbox.BaseSandbox`, which derives
``ls``/``read``/``write``/``edit``/``grep``/``glob`` from ``execute()`` plus
``upload_files()``/``download_files()``. The OpenSandbox SDK is async-only, so
this backend implements the async primitives and leaves the sync ones
unimplemented — the agent runs under asyncio and only exercises the async path.
"""

from __future__ import annotations

import posixpath
from datetime import timedelta

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from ai_coding_harness.safety.permissions import CommandSafety
from ai_coding_harness.sandbox.proxy import OpenSandboxProxy


class OpenSandboxBackend(BaseSandbox):
    """File + shell backend that delegates to an OpenSandbox sandbox."""

    def __init__(
        self,
        sandbox: OpenSandboxProxy,
        safety: CommandSafety | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._safety = safety

    @property
    def id(self) -> str:
        return self._sandbox.id

    # -- async primitives (used by the agent under asyncio) ---------------

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        if self._safety is not None:
            reason = self._safety.check(command)
            if reason is not None:
                return ExecuteResponse(output=reason, exit_code=1)
        result = await self._sandbox.run(
            command,
            timeout=timedelta(seconds=timeout) if timeout is not None else None,
        )
        output = result.stdout
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        return ExecuteResponse(output=output, exit_code=result.exit_code)

    async def aupload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        parents = {
            posixpath.dirname(path)
            for path, _ in files
            if posixpath.dirname(path) not in ("", "/")
        }
        if parents:
            await self._sandbox.run(f"mkdir -p {' '.join(sorted(parents))}")

        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                await self._sandbox.write_file(path, content)
                responses.append(FileUploadResponse(path=path, error=None))
            except Exception as exc:  # noqa: BLE001  # partial success per file
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    async def adownload_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                text = await self._sandbox.read_file(path)
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=text.encode("utf-8"),
                        error=None,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                responses.append(
                    FileDownloadResponse(path=path, content=None, error=str(exc))
                )
        return responses

    # -- sync primitives (unused in the async app; present for ABC) -------

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        raise NotImplementedError("OpenSandboxBackend is async-only; use aexecute")

    def upload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        raise NotImplementedError(
            "OpenSandboxBackend is async-only; use aupload_files"
        )

    def download_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        raise NotImplementedError(
            "OpenSandboxBackend is async-only; use adownload_files"
        )
