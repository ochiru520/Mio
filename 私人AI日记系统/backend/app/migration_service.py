from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from . import db
from .repositories import migration_repository as repository
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


def run_migrations() -> list[dict[str, object]]:
    """Apply pending migrations transactionally and return what changed."""
    applied: list[dict[str, object]] = []
    with repository.ledger() as ledger:
        existing = {int(row["version"]) for row in ledger.applied()}
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
            applied_at = ledger.apply(migration, db.now_iso)
            applied.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "applied_at": applied_at,
                }
            )
    return applied


def migration_status() -> dict[str, object]:
    with repository.ledger() as ledger:
        rows = ledger.applied()
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
