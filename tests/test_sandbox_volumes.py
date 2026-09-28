"""沙箱卷的构造：工作区卷（挂 project）与**依赖缓存卷**（Maven）。

Maven 那条是 2026-09-25 加的：本地仓库放在卷上才活得过容器重建；默认跨会话共享一个卷，
这样「每次构建都要重新下依赖」最多只发生在第一次。这里只验卷名与挂载点，不连真存储。
"""

from __future__ import annotations

import pytest

from ai_coding_harness.config import Settings
from ai_coding_harness.sandbox.volumes import (
    build_maven_cache_volume,
    build_workspace_volume,
    maven_cache_volume_name,
    workspace_volume_name,
)


def test_the_workspace_volume_still_mounts_the_project_dir() -> None:
    volume = build_workspace_volume(Settings(), "pub-1")

    assert volume is not None
    assert volume.mount_path == "/ai_coding/project"
    assert volume.pvc.claim_name == workspace_volume_name(Settings(), "pub-1")


def test_the_maven_cache_is_shared_across_sessions_by_default() -> None:
    """默认共享：依赖包与会话无关，第二个会话一上来就该是热的。"""
    settings = Settings()

    assert maven_cache_volume_name(settings, "pub-1") == "ai-coding-m2"
    assert maven_cache_volume_name(settings, "pub-2") == "ai-coding-m2"

    volume = build_maven_cache_volume(settings, "pub-1")
    assert volume is not None
    assert volume.mount_path == "/ai_coding/.m2", "与 settings.xml 里的 localRepository 对齐"
    assert volume.pvc.claim_name == "ai-coding-m2"


def test_a_per_session_cache_is_available_when_sharing_is_off() -> None:
    """共享要求存储支持多机同时挂；不支持就退化成按会话各挂一个（代价：新会话冷启动）。"""
    settings = Settings(sandbox_maven_cache_shared=False)

    assert maven_cache_volume_name(settings, "pub-1") == "ai-coding-m2-pub-1"
    volume = build_maven_cache_volume(settings, "pub-1")
    assert volume is not None and volume.pvc.claim_name == "ai-coding-m2-pub-1"


def test_the_cache_can_be_turned_off() -> None:
    assert build_maven_cache_volume(Settings(sandbox_maven_cache_enabled=False), "pub-1") is None


@pytest.mark.parametrize("backend", ["none"])
def test_no_volumes_when_the_backend_is_none(backend: str) -> None:
    settings = Settings(sandbox_volume_backend=backend)

    assert build_workspace_volume(settings, "pub-1") is None
    assert build_maven_cache_volume(settings, "pub-1") is None
