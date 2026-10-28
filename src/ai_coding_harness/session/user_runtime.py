"""Per-user values that override the process-wide runtime settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_coding_harness.config import ModelConfig, Settings
from ai_coding_harness.db import Database, dao


@dataclass(frozen=True)
class UserRuntimeSettings:
    user_id: int
    repo_view_dir: str
    repo_view_dir_source: str
    gitee_base_branch: str
    github_base_branch: str
    gitee_access_token: str
    gitee_username: str
    github_access_token: str
    github_username: str
    agent_models: dict[str, ModelConfig]
    intent_mode: str


def default_repo_view_dir(user_id: int) -> Path:
    """One logical cache directory per platform user.

    The service runs under one operating-system account, so ``Path.home()`` alone would make
    every platform user share the same cache. The platform-user id is therefore part of the
    default path.
    """
    return Path.home() / ".ai-coding-harness" / "users" / str(user_id) / "repo_cache"


def normalized_repo_view_dir(raw: str | None, *, user_id: int) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return str(default_repo_view_dir(user_id)), "default"
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError("代码缓存路径必须是绝对路径，或留空使用默认目录。")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("代码缓存路径不能是文件系统根目录。")
    return str(resolved), "db"


async def load_user_runtime_settings(
    settings: Settings,
    database: Database,
    user_id: int | None,
) -> UserRuntimeSettings:
    if user_id is None:
        return UserRuntimeSettings(
            user_id=0,
            repo_view_dir=settings.repo_view_dir,
            repo_view_dir_source="default",
            gitee_base_branch=settings.gitee_base_branch,
            github_base_branch=settings.github_base_branch,
            gitee_access_token=settings.gitee_access_token,
            gitee_username=settings.gitee_username,
            github_access_token=settings.github_access_token,
            github_username=settings.github_username,
            agent_models=dict(settings.agent_models),
            intent_mode=settings.intent_mode,
        )
    async with database.session() as db:
        row = await dao.get_user_settings(db, user_id=user_id)
        endpoints = await dao.list_model_endpoints(db, user_id=user_id)
    repo_dir, source = normalized_repo_view_dir(
        row.repo_view_dir if row is not None else None,
        user_id=user_id,
    )
    agent_models: dict[str, ModelConfig] = {}
    intent_mode = "jev"
    for endpoint in endpoints:
        if endpoint.agent == "intent_mode":
            mode = (endpoint.model_name or "").strip().lower()
            if mode in ("jev", "llm"):
                intent_mode = mode
            continue
        if endpoint.agent != "jev" and not (endpoint.model_name or "").strip():
            continue
        agent_models[endpoint.agent] = ModelConfig(
            model=(endpoint.model_name or "").strip()
            or ("jev-latest" if endpoint.agent == "jev" else ""),
            api_key=endpoint.api_key or "",
            base_url=endpoint.base_url or "",
            context_window=int(endpoint.context_window or 0),
        )
    return UserRuntimeSettings(
        user_id=user_id,
        repo_view_dir=repo_dir,
        repo_view_dir_source=source,
        gitee_base_branch=(
            row.gitee_base_branch
            if row is not None and row.gitee_base_branch is not None
            else ""
        ),
        github_base_branch=(
            row.github_base_branch
            if row is not None and row.github_base_branch is not None
            else ""
        ),
        gitee_access_token=(
            row.gitee_access_token
            if row is not None and row.gitee_access_token is not None
            else ""
        ),
        gitee_username=(
            row.gitee_username
            if row is not None and row.gitee_username is not None
            else ""
        ),
        github_access_token=(
            row.github_access_token
            if row is not None and row.github_access_token is not None
            else ""
        ),
        github_username=(
            row.github_username
            if row is not None and row.github_username is not None
            else ""
        ),
        agent_models=agent_models,
        intent_mode=intent_mode,
    )


def settings_for_user(
    settings: Settings, values: UserRuntimeSettings
) -> Settings:
    """A request/session-local settings view with personal path and branch overrides."""
    user_models = dict(settings.user_agent_models)
    user_models[values.user_id] = values.agent_models
    intent_modes = dict(settings.user_intent_modes)
    intent_modes[values.user_id] = values.intent_mode
    return settings.model_copy(
        update={
            "repo_view_dir": values.repo_view_dir,
            "gitee_base_branch": values.gitee_base_branch,
            "github_base_branch": values.github_base_branch,
            "gitee_access_token": values.gitee_access_token,
            "gitee_username": values.gitee_username,
            "github_access_token": values.github_access_token,
            "github_username": values.github_username,
            "user_agent_models": user_models,
            "user_intent_modes": intent_modes,
        }
    )
