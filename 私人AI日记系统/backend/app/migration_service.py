from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from . import db
from .config import settings


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _baseline(_conn: sqlite3.Connection) -> None:
    """Mark the schema shipped before versioned migrations as the baseline."""


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "0.5.0 以前的数据库结构基线", _baseline),
)


def _ensure_ledger(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def run_migrations() -> list[dict[str, object]]:
    """Apply pending migrations transactionally and return what changed."""
    applied: list[dict[str, object]] = []
    with db.get_conn() as conn:
        _ensure_ledger(conn)
        conn.commit()
        existing = {
            int(row["version"])
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        pending = [migration for migration in MIGRATIONS if migration.version not in existing]
        database_is_managed = False
        try:
            database_is_managed = settings.db_path.resolve().is_relative_to(settings.data_dir.resolve())
        except OSError:
            database_is_managed = False
        if pending and database_is_managed and settings.db_path.exists() and settings.db_path.stat().st_size > 0:
            # Keep the import local so the migration ledger remains usable by
            # backup verification without introducing an import cycle.
            from .backup_service import create_complete_backup

            create_complete_backup(kind="migration", reason="数据库迁移前自动创建")
        for migration in pending:
            with conn:
                migration.apply(conn)
                applied_at = db.now_iso()
                conn.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, applied_at),
                )
            applied.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "applied_at": applied_at,
                }
            )
    return applied


def migration_status() -> dict[str, object]:
    with db.get_conn() as conn:
        _ensure_ledger(conn)
        rows = conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
    applied = [dict(row) for row in rows]
    applied_versions = {int(item["version"]) for item in applied}
    pending = [
        {"version": migration.version, "name": migration.name}
        for migration in MIGRATIONS
        if migration.version not in applied_versions
    ]
    return {
        "current_version": max(applied_versions, default=0),
        "latest_version": max((item.version for item in MIGRATIONS), default=0),
        "applied": applied,
        "pending": pending,
        "up_to_date": not pending,
    }
