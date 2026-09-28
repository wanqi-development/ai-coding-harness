"""沙箱工作区卷构造 —— 按部署环境选择卷后端。

- ``pvc``   ：本地 Docker 命名卷（单机）或 K8s PVC（集群，配 RWX）
- ``host``  ：宿主机 / NFS 共享路径挂载（Docker 集群）
- ``ossfs`` ：阿里云 OSS（对象存储）
- ``none``  ：不挂卷
"""

from __future__ import annotations

from opensandbox.models.sandboxes import OSSFS, PVC, Host, Volume

from ai_coding_harness.config import Settings


def _access_modes(raw: str) -> list[str] | None:
    modes = [m.strip() for m in raw.split(",") if m.strip()]
    return modes or None


def workspace_volume_name(settings: Settings, session_id: str) -> str:
    """会话工作区卷名（各后端统一用它作为标识）。"""
    return f"{settings.sandbox_volume_prefix}-{session_id}"


def _cache_volume_name(prefix: str, *, shared: bool, session_id: str) -> str:
    """依赖缓存的卷名。

    **默认跨会话共享一个卷**（``*_CACHE_SHARED=true``）：依赖包与具体会话无关，
    共享之后第二个、第三个会话一上来就是热的 —— 这正是「每次构建都要重新下依赖」的解药。
    代价是要求存储支持多机同时挂（Docker 单机没问题；K8s 要么 RWX，要么把它设成 false）。
    ``shared=false`` 时按会话各挂一个：独占、无并发写，但新会话冷启动。
    """
    return prefix if shared else f"{prefix}-{session_id}"


def maven_cache_volume_name(settings: Settings, session_id: str) -> str:
    """Maven 本地仓库的缓存卷名。"""
    return _cache_volume_name(
        settings.sandbox_maven_cache_prefix,
        shared=settings.sandbox_maven_cache_shared,
        session_id=session_id,
    )


def _build_volume(
    settings: Settings, *, name: str, volume_name: str, mount_path: str
) -> Volume:
    """按 ``sandbox_volume_backend`` 造一个卷（工作区与 Maven 缓存共用一个实现）。"""
    backend = settings.sandbox_volume_backend.strip().lower()

    if backend == "pvc":
        return Volume(
            name=name,
            pvc=PVC(
                claim_name=volume_name,
                create_if_not_exists=True,
                delete_on_sandbox_termination=settings.sandbox_volume_delete_on_termination,
                storage_class=settings.sandbox_volume_storage_class or None,
                storage=settings.sandbox_volume_storage or None,
                access_modes=_access_modes(settings.sandbox_volume_access_modes),
            ),
            mount_path=mount_path,
        )

    if backend == "host":
        root = settings.sandbox_volume_host_path.rstrip("/")
        if not root:
            raise ValueError("sandbox_volume_backend=host 需配置 SANDBOX_VOLUME_HOST_PATH")
        return Volume(
            name=name,
            host=Host(path=f"{root}/{volume_name}"),
            mount_path=mount_path,
        )

    if backend == "ossfs":
        if not (settings.sandbox_oss_bucket and settings.sandbox_oss_endpoint):
            raise ValueError(
                "sandbox_volume_backend=ossfs 需配置 SANDBOX_OSS_BUCKET / SANDBOX_OSS_ENDPOINT"
            )
        return Volume(
            name=name,
            ossfs=OSSFS(
                bucket=settings.sandbox_oss_bucket,
                endpoint=settings.sandbox_oss_endpoint,
                version=settings.sandbox_oss_version,
                access_key_id=settings.sandbox_oss_access_key_id or None,
                access_key_secret=settings.sandbox_oss_access_key_secret or None,
            ),
            mount_path=mount_path,
            sub_path=volume_name,
        )

    raise ValueError(
        f"未知的 sandbox_volume_backend：{settings.sandbox_volume_backend!r}（应为 pvc / host / ossfs / none）"
    )


def build_workspace_volume(settings: Settings, session_id: str) -> Volume | None:
    """按 ``sandbox_volume_backend`` 构造会话级工作区卷（挂到 project 目录）。"""
    if settings.sandbox_volume_backend.strip().lower() == "none":
        return None
    return _build_volume(
        settings,
        name="workspace",
        volume_name=workspace_volume_name(settings, session_id),
        mount_path=settings.sandbox_project_dir,
    )


def build_maven_cache_volume(settings: Settings, session_id: str) -> Volume | None:
    """Maven 本地仓库的缓存卷（挂到 ``/ai_coding/.m2``，即 settings.xml 里的 localRepository 父目录）。

    不启用（``SANDBOX_MAVEN_CACHE_ENABLED=false``）或后端是 ``none`` 时返回 ``None``：
    此时 ``/ai_coding/.m2/repository`` 只是个容器内目录，本地仓库随容器一起没。
    """
    if not settings.sandbox_maven_cache_enabled:
        return None
    if settings.sandbox_volume_backend.strip().lower() == "none":
        return None
    return _build_volume(
        settings,
        name="maven-cache",
        volume_name=maven_cache_volume_name(settings, session_id),
        mount_path=settings.sandbox_maven_cache_mount_path,
    )


