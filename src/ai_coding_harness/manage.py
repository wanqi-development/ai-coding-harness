"""Small operator CLI for initial authentication bootstrap."""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from ai_coding_harness.auth.security import (
    hash_password,
    normalize_username,
    password_problem,
)
from ai_coding_harness.backends.composite import (
    MEMORIES_NAMESPACE,
    memory_store_key,
)
from ai_coding_harness.config import Settings, get_settings
from ai_coding_harness.db import Database, dao
from ai_coding_harness.db.models import AgentSession, Repository
from ai_coding_harness.session.state import build_store, close_persistence
from ai_coding_harness.utils.path_util import ROOT_DIR


def _read_password(*, confirm: bool) -> str:
    password = getpass.getpass("密码：")
    problem = password_problem(password)
    if problem is not None:
        raise ValueError(problem)
    if confirm and getpass.getpass("再次输入密码：") != password:
        raise ValueError("两次输入的密码不一致。")
    return password


def _write_private_text(path: Path, content: str) -> None:
    """Atomically write a credential file readable only by the current user."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _database_error_message(settings: Settings, exc: OperationalError) -> str:
    """Connection diagnostics without echoing a DSN or credential."""
    reason = type(getattr(exc, "orig", exc)).__name__
    if settings.is_standalone:
        return (
            f"无法打开 SQLite：{settings.standalone_database_path}\n"
            f"配置文件：{ROOT_DIR / '.env'}\n"
            f"底层错误：{reason}"
        )
    return (
        "无法连接或访问 PostgreSQL："
        f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}\n"
        f"配置文件：{ROOT_DIR / '.env'}\n"
        f"连接超时：{max(1, settings.postgres_connect_timeout_seconds)} 秒；"
        f"底层错误：{reason}\n"
        "请确认数据库已启动、端口可访问，并检查 POSTGRES_HOST / POSTGRES_PORT / "
        "POSTGRES_DB。"
    )


async def _create_admin(args: argparse.Namespace) -> int:
    username = normalize_username(args.username)
    if not username:
        print("用户名不能为空。", file=sys.stderr)
        return 2
    password = _read_password(confirm=True)
    settings = get_settings()
    settings.validate_deployment()
    database = Database(settings)
    try:
        await database.prepare()
        async with database.session() as db:
            if await dao.count_users(db):
                print("系统已有用户；请通过管理员设置页创建其他账号。", file=sys.stderr)
                return 1
            user = await dao.create_local_user(
                db,
                username=username,
                display_name=args.display_name or username,
                password_hash=hash_password(password),
                role="admin",
            )
            claimed = 0
            if args.claim_existing:
                claimed = await dao.claim_unowned_sessions(db, user_id=user.id)
            print(f"管理员已创建：{username}（id={user.id}）")
            if args.claim_existing:
                print(f"已认领历史会话：{claimed} 个")
    except OperationalError as exc:
        print(_database_error_message(settings, exc), file=sys.stderr)
        return 1
    finally:
        await database.dispose()
    return 0


async def _ensure_standalone_admin(args: argparse.Namespace) -> int:
    """Create the first standalone admin with a generated one-time password."""
    settings = get_settings()
    if not settings.is_standalone:
        print(
            "multi 模式不自动创建管理员；请由运维人员交互执行 "
            "create-admin --username <用户名> --claim-existing。",
            file=sys.stderr,
        )
        return 2
    settings.validate_deployment()

    username = normalize_username(args.username)
    if not username:
        print("用户名不能为空。", file=sys.stderr)
        return 2

    credentials_file = (
        Path(args.credentials_file).expanduser()
        if args.credentials_file
        else Path(settings.log_dir).expanduser() / "initial-admin.txt"
    )
    database = Database(settings)
    try:
        await database.prepare()
        async with database.session() as db:
            if await dao.count_users(db):
                print("管理员已存在，未生成新密码。")
                return 0

            password = secrets.token_urlsafe(24)
            user = await dao.create_local_user(
                db,
                username=username,
                display_name=username,
                password_hash=hash_password(password),
                role="admin",
                must_change_password=True,
            )
            claimed = await dao.claim_unowned_sessions(db, user_id=user.id)
            created_at = datetime.now(UTC).isoformat()
            content = (
                "ai-coding-harness standalone 初始管理员\n"
                f"用户名：{username}\n"
                f"密码：{password}\n"
                f"数据库：{settings.standalone_database_path}\n"
                f"创建时间：{created_at}\n"
                "首次登录后必须修改密码；修改后本文件中的临时密码即失效。\n"
            )
            _write_private_text(credentials_file, content)
            print("Standalone 初始管理员已创建")
            print(f"用户名：{username}")
            print(f"密码：{password}")
            print(f"凭据文件：{credentials_file}")
            print("首次登录后必须修改密码。")
            print(f"已认领历史会话：{claimed} 个")
    except OperationalError as exc:
        print(_database_error_message(settings, exc), file=sys.stderr)
        return 1
    finally:
        await database.dispose()
    return 0


async def _reset_password(args: argparse.Namespace) -> int:
    username = normalize_username(args.username)
    password = _read_password(confirm=True)
    settings = get_settings()
    settings.validate_deployment()
    database = Database(settings)
    try:
        await database.prepare()
        async with database.session() as db:
            user = await dao.get_user_by_username(db, username)
            if user is None:
                print(f"用户不存在：{username}", file=sys.stderr)
                return 1
            changed = await dao.reset_local_password(
                db,
                user_id=user.id,
                password_hash=hash_password(password),
            )
            if not changed:
                print("该用户没有本地密码凭据。", file=sys.stderr)
                return 1
            await dao.revoke_user_sessions(db, user_id=user.id)
            print(f"密码已重置，用户 {username} 的所有现有会话已撤销。")
    except OperationalError as exc:
        print(_database_error_message(settings, exc), file=sys.stderr)
        return 1
    finally:
        await database.dispose()
    return 0


async def _migrate_repo_memory(args: argparse.Namespace) -> int:
    """Copy legacy owner/repo memory keys to platform/owner/repo keys once."""
    settings = get_settings()
    settings.validate_deployment()
    database = Database(settings)
    await database.prepare()
    store = await build_store(settings)
    moved = 0
    skipped = 0
    conflicts = 0
    try:
        async with database.session() as db:
            rows = (
                await db.execute(
                    select(AgentSession, Repository)
                    .join(Repository, Repository.session_id == AgentSession.id)
                    .order_by(AgentSession.id)
                )
            ).all()
        seen: set[tuple[str, str, str]] = set()
        for session, repo in rows:
            platform = str(repo.platform or session.git_platform or "gitee").lower()
            identity = (platform, repo.owner, repo.name)
            if identity in seen:
                continue
            seen.add(identity)
            old_key = f"/{repo.owner}/{repo.name}.md"
            new_key = memory_store_key(
                platform, repo.owner, repo.name, settings.memories_prefix
            )
            old = await store.aget(MEMORIES_NAMESPACE, old_key)
            if old is None:
                skipped += 1
                continue
            current = await store.aget(MEMORIES_NAMESPACE, new_key)
            if current is not None and not args.overwrite:
                conflicts += 1
                print(
                    f"冲突，未覆盖：{platform}:{repo.owner}/{repo.name} "
                    f"({old_key} → {new_key})",
                    file=sys.stderr,
                )
                continue
            if args.dry_run:
                print(f"[dry-run] {old_key} → {new_key}")
                moved += 1
                continue
            await store.aput(MEMORIES_NAMESPACE, new_key, old.value)
            await store.adelete(MEMORIES_NAMESPACE, old_key)
            moved += 1
            print(f"已迁移：{old_key} → {new_key}")
        print(f"仓库记忆迁移完成：迁移 {moved}，跳过 {skipped}，冲突 {conflicts}")
        return 1 if conflicts else 0
    finally:
        await close_persistence(store)
        await database.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ai-coding-harness 运维命令")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="创建初始管理员")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", default="")
    create.add_argument(
        "--claim-existing",
        action="store_true",
        help="把认证上线前无 user_id 的历史会话归给该管理员",
    )
    ensure = sub.add_parser(
        "ensure-standalone-admin",
        help="standalone 首次启动时生成随机管理员密码并写入凭据文件",
    )
    ensure.add_argument("--username", default="admin")
    ensure.add_argument(
        "--credentials-file",
        default="",
        help="凭据文件路径；留空使用 LOG_DIR/initial-admin.txt",
    )
    reset = sub.add_parser("reset-password", help="重置本地密码并撤销旧会话")
    reset.add_argument("--username", required=True)
    migrate_memory = sub.add_parser(
        "migrate-repo-memory",
        help="把旧 owner/repo 仓库记忆迁移为 platform/owner/repo",
    )
    migrate_memory.add_argument("--dry-run", action="store_true")
    migrate_memory.add_argument(
        "--overwrite",
        action="store_true",
        help="目标新 key 已存在时也覆盖（默认记为冲突并退出非零）",
    )
    sub.add_parser(
        "generate-session-cache-key",
        help="生成 Redis Session 快照使用的 32 字节 base64 密钥",
    )
    return parser


async def _main(args: argparse.Namespace) -> int:
    if args.command == "create-admin":
        print("create-admin args:", args)
        return await _create_admin(args)
    if args.command == "ensure-standalone-admin":
        return await _ensure_standalone_admin(args)
    if args.command == "reset-password":
        return await _reset_password(args)
    if args.command == "migrate-repo-memory":
        return await _migrate_repo_memory(args)
    if args.command == "generate-session-cache-key":
        print(base64.urlsafe_b64encode(os.urandom(32)).decode())
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print("sys.platform:", sys.platform)
    try:
        if sys.platform == "win32":
            return asyncio.run(
                _main(args),
                loop_factory=asyncio.SelectorEventLoop,
            )
        return asyncio.run(_main(args))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
