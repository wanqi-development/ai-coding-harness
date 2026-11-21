"""Standalone SQLite schema inspection and legacy-schema repair.

Standalone used to rely on ``Base.metadata.create_all``. That creates missing
tables but cannot add columns to existing tables. Keep ``create_all`` for new
tables, then reconcile missing nullable/defaulted columns before the API opens.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite

from ai_coding_harness.db import models as _models  # noqa: F401
from ai_coding_harness.db.base import Base

SCHEMA_VERSION = 1
SCHEMA_NAME = "standalone-schema-reconcile-v1"


@dataclass(frozen=True)
class ColumnFix:
    table: str
    column: str
    ddl: str


@dataclass(frozen=True)
class SchemaPlan:
    missing_tables: tuple[str, ...]
    column_fixes: tuple[ColumnFix, ...]
    blockers: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        return not self.missing_tables and not self.column_fixes and not self.blockers


@dataclass(frozen=True)
class SchemaReport:
    plan: SchemaPlan
    backup_path: Path | None = None
    created_tables: tuple[str, ...] = ()
    added_columns: tuple[str, ...] = ()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _default_sql(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip()
    return text or None


def _column_ddl(table_name: str, column: object) -> tuple[str | None, str | None]:
    """Return ``(DDL, blocker)`` for one missing SQLAlchemy column."""
    name = str(getattr(column, "name", ""))
    if not name:
        return None, f"{table_name}: 无法识别列名"
    if bool(getattr(column, "primary_key", False)):
        return None, f"{table_name}.{name}: SQLite 不能给已有表补主键列"

    type_sql = str(column.type.compile(dialect=sqlite.dialect()))
    parts = [_quote(table_name), "ADD COLUMN", _quote(name), type_sql]
    nullable = bool(getattr(column, "nullable", True))
    server_default = getattr(column, "server_default", None)
    default_sql = _default_sql(getattr(server_default, "arg", None))
    if not nullable and default_sql is None:
        return (
            None,
            f"{table_name}.{name}: 非空列且无 server_default，无法安全补齐旧行",
        )
    if not nullable:
        parts.append("NOT NULL")
    if default_sql is not None:
        parts.extend(["DEFAULT", default_sql])
    return "ALTER TABLE " + " ".join(parts), None


def inspect_standalone_schema(path: Path) -> SchemaPlan:
    """Compare SQLAlchemy metadata with an existing SQLite database."""
    path = path.expanduser().resolve()
    missing_tables: list[str] = []
    fixes: list[ColumnFix] = []
    blockers: list[str] = []
    if not path.exists():
        return SchemaPlan(
            missing_tables=tuple(sorted(Base.metadata.tables)),
            column_fixes=(),
            blockers=(),
        )

    connection = sqlite3.connect(path)
    try:
        existing_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table_name, table in Base.metadata.tables.items():
            if table_name not in existing_tables:
                missing_tables.append(table_name)
                continue
            columns = {
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({_quote(table_name)})"
                )
            }
            for column in table.columns:
                if column.name in columns:
                    continue
                ddl, blocker = _column_ddl(table_name, column)
                if blocker:
                    blockers.append(blocker)
                elif ddl:
                    fixes.append(ColumnFix(table_name, str(column.name), ddl))
    finally:
        connection.close()
    return SchemaPlan(
        missing_tables=tuple(sorted(missing_tables)),
        column_fixes=tuple(fixes),
        blockers=tuple(blockers),
    )


@contextmanager
def _schema_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".schema.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def backup_sqlite(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"{path.stem}-{stamp}.sqlite3"
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    return target


def _record_version(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS harness_schema_migrations ("
            "version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO harness_schema_migrations(version, name, applied_at) "
            "VALUES (?, ?, ?)",
            (SCHEMA_VERSION, SCHEMA_NAME, datetime.now(UTC).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


def repair_standalone_schema(
    path: Path,
    *,
    backup_dir: Path | None = None,
    dry_run: bool = False,
) -> SchemaReport:
    """Create missing tables and add safe missing columns to a SQLite database."""
    path = path.expanduser().resolve()
    plan = inspect_standalone_schema(path)
    if plan.blockers:
        return SchemaReport(plan=plan)
    if dry_run:
        return SchemaReport(plan=plan)
    if plan.healthy:
        if path.exists():
            _record_version(path)
        return SchemaReport(plan=plan)

    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup_sqlite(path, backup_dir or path.parent / "backups") if path.exists() else None
    with _schema_lock(path):
        engine = create_engine(f"sqlite:///{path.as_posix()}")
        try:
            Base.metadata.create_all(engine)
        finally:
            engine.dispose()

        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            for fix in plan.column_fixes:
                connection.execute(fix.ddl)
            connection.commit()
        finally:
            connection.close()
        _record_version(path)

    repaired = inspect_standalone_schema(path)
    return SchemaReport(
        plan=repaired,
        backup_path=backup_path,
        created_tables=plan.missing_tables,
        added_columns=tuple(
            f"{fix.table}.{fix.column}" for fix in plan.column_fixes
        ),
    )


__all__ = [
    "ColumnFix",
    "SchemaPlan",
    "SchemaReport",
    "backup_sqlite",
    "inspect_standalone_schema",
    "repair_standalone_schema",
]
