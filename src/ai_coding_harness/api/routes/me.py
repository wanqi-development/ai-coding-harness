"""Current-user settings that are safe for every authenticated role."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from ai_coding_harness.api.schemas import (
    MeProfileInfo,
    UserRuntimeSettingsInfo,
    UserRuntimeSettingsUpdate,
)
from ai_coding_harness.core.codeplatform import Platform, RepoSpec
from ai_coding_harness.db import dao
from ai_coding_harness.db.dao.models import UNSET
from ai_coding_harness.db.models import AuthSession
from ai_coding_harness.safety.redact import mask_secret
from ai_coding_harness.session.session_cache import SessionSnapshot
from ai_coding_harness.session.user_runtime import (
    default_repo_view_dir,
    normalized_repo_view_dir,
)
from ai_coding_harness.tools.repo_access import (
    RepoAccessError,
    verify_repo_access,
    verify_token,
)

router = APIRouter(prefix="/me", tags=["me"])


def _user_id(request: Request) -> int:
    return int(request.state.auth.user.id)


@router.get("/profile", response_model=MeProfileInfo)
async def get_my_profile(request: Request) -> MeProfileInfo:
    user_id = _user_id(request)
    async with request.app.state.database.session() as db:
        found = await dao.get_user_with_credential(db, user_id=user_id)
        if found is None:
            raise HTTPException(status_code=404, detail="当前用户不存在。")
        user, credential = found
        activity = await dao.list_users_with_activity(db, now=datetime.now(UTC))
        current_session = await db.get(AuthSession, request.state.auth.session_id)
    active_sessions = next(
        (count for item, _cred, count, _seen in activity if int(item.id) == user_id),
        0,
    )
    last_active_at = next(
        (seen for item, _cred, _count, seen in activity if int(item.id) == user_id),
        None,
    )
    return MeProfileInfo(
        id=int(user.id),
        username=str(user.username),
        display_name=str(user.display_name or user.username),
        role=str(user.role),
        created_at=user.created_at,
        password_changed_at=(
            credential.password_changed_at if credential is not None else None
        ),
        active_sessions=int(active_sessions),
        last_active_at=last_active_at,
        current_session_started_at=(
            current_session.created_at if current_session is not None else None
        ),
        current_session_expires_at=(
            current_session.expires_at if current_session is not None else None
        ),
    )


@router.get("/settings", response_model=UserRuntimeSettingsInfo)
async def get_my_settings(request: Request) -> UserRuntimeSettingsInfo:
    user_id = _user_id(request)
    async with request.app.state.database.session() as db:
        row = await dao.get_user_settings(db, user_id=user_id)
    return _settings_info(row, user_id=user_id, echo_secrets=True)


def _settings_info(row: object | None, *, user_id: int, echo_secrets: bool) -> UserRuntimeSettingsInfo:
    repo_dir, source = normalized_repo_view_dir(
        getattr(row, "repo_view_dir", None),
        user_id=user_id,
    )
    gitee_token = str(getattr(row, "gitee_access_token", None) or "")
    github_token = str(getattr(row, "github_access_token", None) or "")
    return UserRuntimeSettingsInfo(
        repo_view_dir=repo_dir,
        repo_view_dir_source=source,
        repo_view_dir_default=str(default_repo_view_dir(user_id)),
        gitee_base_branch=str(getattr(row, "gitee_base_branch", None) or ""),
        github_base_branch=str(getattr(row, "github_base_branch", None) or ""),
        gitee_access_token=(
            mask_secret(gitee_token) if echo_secrets and gitee_token else ""
        ),
        gitee_access_token_configured=bool(gitee_token),
        gitee_username=str(getattr(row, "gitee_username", None) or ""),
        github_access_token=(
            mask_secret(github_token) if echo_secrets and github_token else ""
        ),
        github_access_token_configured=bool(github_token),
        github_username=str(getattr(row, "github_username", None) or ""),
        updated_at=getattr(row, "updated_at", None),
    )


@router.put("/settings", response_model=UserRuntimeSettingsInfo)
async def put_my_settings(
    payload: UserRuntimeSettingsUpdate, request: Request
) -> UserRuntimeSettingsInfo:
    user_id = _user_id(request)
    values = payload.model_dump(exclude_unset=True)
    credential_keys = {
        "gitee_access_token",
        "clear_gitee_token",
        "gitee_username",
        "github_access_token",
        "clear_github_token",
        "github_username",
    }
    touches_credentials = bool(credential_keys & set(values))
    async with request.app.state.database.session() as db:
        current = await dao.get_user_settings(db, user_id=user_id)
        active = (
            await dao.list_active_sessions_with_repo_for_user(db, user_id=user_id)
            if touches_credentials
            else []
        )

    current_tokens = {
        Platform.GITEE: str(getattr(current, "gitee_access_token", None) or ""),
        Platform.GITHUB: str(getattr(current, "github_access_token", None) or ""),
    }
    current_usernames = {
        Platform.GITEE: str(getattr(current, "gitee_username", None) or ""),
        Platform.GITHUB: str(getattr(current, "github_username", None) or ""),
    }
    next_tokens = dict(current_tokens)
    next_usernames = dict(current_usernames)
    changed_platforms: set[Platform] = set()
    for platform in (Platform.GITEE, Platform.GITHUB):
        token_key = f"{platform.value}_access_token"
        clear_key = f"clear_{platform.value}_token"
        username_key = f"{platform.value}_username"
        if values.get(clear_key):
            next_tokens[platform] = ""
        elif token_key in values:
            raw = str(values[token_key] or "").strip()
            if raw != mask_secret(current_tokens[platform]):
                next_tokens[platform] = raw
        if username_key in values:
            next_usernames[platform] = str(values[username_key] or "").strip()
        if (
            next_tokens[platform] != current_tokens[platform]
            or next_usernames[platform] != current_usernames[platform]
        ):
            changed_platforms.add(platform)

    app_settings = request.app.state.settings
    for platform in sorted(changed_platforms, key=lambda item: item.value):
        affected = [
            (session_row, stored)
            for session_row, stored in active
            if session_row.git_platform == platform.value
        ]
        if next_tokens[platform]:
            try:
                if affected:
                    for _session_row, stored in affected:
                        await verify_repo_access(
                            app_settings,
                            RepoSpec(
                                platform=platform,
                                owner=stored.owner,
                                repo=stored.name,
                                token=next_tokens[platform],
                                git_username=next_usernames[platform],
                            ),
                        )
                else:
                    await verify_token(app_settings, platform, next_tokens[platform])
            except RepoAccessError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None

    repo_dir: str | None = None
    repo_value: str | None | object = UNSET
    if "repo_view_dir" in values:
        try:
            normalized, _source = normalized_repo_view_dir(
                values["repo_view_dir"],
                user_id=user_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        default = str(default_repo_view_dir(user_id))
        repo_dir = None if normalized == default else normalized
        repo_value = repo_dir
    async with request.app.state.database.session() as db:
        await dao.upsert_user_settings(
            db,
            user_id=user_id,
            repo_view_dir=repo_value,
            gitee_base_branch=values.get("gitee_base_branch", UNSET),
            github_base_branch=values.get("github_base_branch", UNSET),
            gitee_access_token=next_tokens[Platform.GITEE],
            gitee_username=next_usernames[Platform.GITEE],
            github_access_token=next_tokens[Platform.GITHUB],
            github_username=next_usernames[Platform.GITHUB],
        )
        bumped: list[tuple[Platform, list[object]]] = []
        for platform in changed_platforms:
            rows = await dao.bump_user_session_credentials(
                db,
                user_id=user_id,
                platform=platform.value,
                token=next_tokens[platform],
                git_username=next_usernames[platform],
            )
            bumped.append((platform, list(rows)))
        fresh = await dao.get_user_settings(db, user_id=user_id)

    cache = getattr(request.app.state, "session_cache", None)
    if cache is not None:
        stored_by_session = {session_row.id: stored for session_row, stored in active}
        for platform, rows in bumped:
            for row in rows:
                stored = stored_by_session.get(row.id)
                if stored is None:
                    continue
                snapshot = SessionSnapshot.from_parts(
                    public_id=str(row.public_id),
                    db_id=int(row.id),
                    user_id=row.user_id,
                    status=row.status,
                    repo_url=stored.url,
                    repo=RepoSpec(
                        platform=platform,
                        owner=stored.owner,
                        repo=stored.name,
                        token=next_tokens[platform],
                        git_username=next_usernames[platform],
                    ),
                    credential_version=row.credential_version,
                    session_version=row.session_version,
                )
                await cache.put(snapshot)
    return _settings_info(fresh, user_id=user_id, echo_secrets=False)
