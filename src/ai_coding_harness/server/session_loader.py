"""Resolve a durable Session for an Agent worker job."""

from __future__ import annotations

import logging
import uuid

from ai_coding_harness.config import Settings
from ai_coding_harness.db import Database, dao
from ai_coding_harness.session.manager import Session, SessionManager
from ai_coding_harness.session.session_cache import SessionSnapshot, SessionSnapshotCache
from ai_coding_harness.session.user_runtime import load_user_runtime_settings

logger = logging.getLogger(__name__)


async def load_worker_session(
    *,
    settings: Settings,
    database: Database,
    manager: SessionManager,
    cache: SessionSnapshotCache | None,
    public_id: str,
) -> Session | None:
    cached = await cache.get(public_id) if cache is not None else None
    if cached is not None:
        session = cached.to_session()
    else:
        try:
            parsed = uuid.UUID(public_id)
        except ValueError:
            return None
        async with database.session() as db:
            found = await dao.get_session_with_repo(db, public_id=parsed)
        if found is None:
            return None
        row, repo = found
        if repo is None or row.status != "active":
            return None
        session = manager.session_from_row(
            public_id=public_id,
            db_id=row.id,
            repo_url=repo.url,
            owner=repo.owner,
            name=repo.name,
            gitee_token=row.gitee_token,
            gitee_username=row.gitee_username,
            git_platform=row.git_platform,
            user_id=getattr(row, "user_id", None),
            runtime_settings=None,
            status=row.status,
            credential_version=getattr(row, "credential_version", 0),
            session_version=getattr(row, "session_version", 0),
        )
        if cache is not None:
            await cache.put_if_absent(
                SessionSnapshot.from_parts(
                    public_id=session.public_id,
                    db_id=session.db_id,
                    user_id=session.user_id,
                    status=session.status,
                    repo_url=session.repo_url,
                    repo=session.repo,
                    credential_version=session.credential_version,
                    session_version=session.session_version,
                )
            )
    if session.status != "active":
        return None
    session.runtime_settings = await load_user_runtime_settings(
        settings,
        database,
        session.user_id,
    )
    return session


__all__ = ["load_worker_session"]
