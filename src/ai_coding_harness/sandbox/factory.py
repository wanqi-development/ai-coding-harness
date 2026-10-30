"""Select the sandbox provider for the active deployment profile."""

from __future__ import annotations

from typing import Any

from ai_coding_harness.config import Settings
from ai_coding_harness.sandbox.provider import SandboxProvider


def build_sandbox_provider(settings: Settings) -> Any:
    if settings.effective_sandbox_provider == "wsl":
        from ai_coding_harness.sandbox.wsl import WslSandboxProvider

        return WslSandboxProvider(settings)
    return SandboxProvider(settings)
