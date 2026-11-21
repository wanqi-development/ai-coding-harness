"""Standalone SQLite legacy-schema inspection and repair."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ai_coding_harness.db.standalone_schema import (
    inspect_standalone_schema,
    repair_standalone_schema,
)


def _legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE model_providers (
                provider VARCHAR(32) PRIMARY KEY NOT NULL,
                display_name TEXT NOT NULL,
                default_base_url TEXT,
                ui_group TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                status INTEGER NOT NULL DEFAULT 1,
                is_dynamic_model_list BOOLEAN NOT NULL DEFAULT 1,
                models_path TEXT,
                auth_header TEXT,
                auth_prefix TEXT,
                default_models JSON,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE model_endpoints (
                user_id BIGINT NOT NULL,
                agent VARCHAR(32) NOT NULL,
                base_url TEXT,
                model_name TEXT,
                api_key TEXT,
                context_window INTEGER NOT NULL DEFAULT 0,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, agent)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def _columns(path: Path, table: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
    finally:
        connection.close()


def test_repair_adds_provider_protocol_and_headers_columns(tmp_path: Path) -> None:
    path = tmp_path / "harness.sqlite3"
    _legacy_database(path)

    plan = inspect_standalone_schema(path)
    assert {fix.table for fix in plan.column_fixes} >= {
        "model_endpoints",
        "model_providers",
    }

    report = repair_standalone_schema(path, backup_dir=tmp_path / "backups")

    assert report.plan.healthy
    assert report.backup_path is not None and report.backup_path.exists()
    assert "provider" in _columns(path, "model_endpoints")
    assert {"protocol", "extra_headers"} <= _columns(path, "model_providers")

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "SELECT user_id, agent, base_url, provider, model_name FROM model_endpoints"
        ).fetchall()
        version = connection.execute(
            "SELECT name FROM harness_schema_migrations WHERE version = 1"
        ).fetchone()
    finally:
        connection.close()
    assert version is not None


def test_repair_is_idempotent_and_does_not_create_more_backups(
    tmp_path: Path,
) -> None:
    path = tmp_path / "harness.sqlite3"
    backup_dir = tmp_path / "backups"
    _legacy_database(path)

    first = repair_standalone_schema(path, backup_dir=backup_dir)
    second = repair_standalone_schema(path, backup_dir=backup_dir)

    assert first.backup_path is not None
    assert second.backup_path is None
    assert list(backup_dir.glob("*.sqlite3")) == [first.backup_path]


def test_dry_run_does_not_modify_the_database(tmp_path: Path) -> None:
    path = tmp_path / "harness.sqlite3"
    _legacy_database(path)

    report = repair_standalone_schema(path, dry_run=True)

    assert not report.plan.healthy
    assert "provider" not in _columns(path, "model_endpoints")
