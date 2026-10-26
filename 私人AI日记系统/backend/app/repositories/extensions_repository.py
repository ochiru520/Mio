"""Persistence for extensions. Schema is applied by db.init_db."""
from __future__ import annotations
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, nullcontext
import json
from .. import db
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS extensions (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    latest_version TEXT NOT NULL,
    active_version TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extension_versions (
    extension_id TEXT NOT NULL,
    version TEXT NOT NULL,
    bundle_path TEXT NOT NULL,
    bundle_hash TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(extension_id, version)
);
CREATE TABLE IF NOT EXISTS extension_activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    extension_id TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    with closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        yield conn


def get(identifier: str, conn: sqlite3.Connection | None=None):
    with nullcontext(conn) if conn is not None else closing(db.get_conn()) as connection:
        row = connection.execute('SELECT * FROM extensions WHERE id=?', (identifier,)).fetchone()
    return dict(row) if row else None


def version(identifier: str, version, conn: sqlite3.Connection | None=None):
    with nullcontext(conn) if conn is not None else closing(db.get_conn()) as connection:
        return connection.execute('SELECT * FROM extension_versions WHERE extension_id=? AND version=?', (identifier, version)).fetchone()


def versions(identifier: str):
    with closing(db.get_conn()) as conn:
        return conn.execute('SELECT version,validation_json,created_at FROM extension_versions WHERE extension_id=? ORDER BY rowid DESC', (identifier,)).fetchall()


def ids():
    with closing(db.get_conn()) as conn:
        return [row[0] for row in conn.execute('SELECT id FROM extensions ORDER BY updated_at DESC,id')]


def active():
    with closing(db.get_conn()) as conn:
        return conn.execute('SELECT id,active_version FROM extensions WHERE enabled=1').fetchall()


def save(conn: sqlite3.Connection, identifier: str, revision: int, version, bundle_path: str, bundle_hash: str, validation) -> None:
    stamp = db.now_iso()
    conn.execute('INSERT INTO extensions VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,latest_version=excluded.latest_version,updated_at=excluded.updated_at', (identifier, revision, 0, version, '', stamp))
    conn.execute('INSERT INTO extension_versions VALUES(?,?,?,?,?,?)', (identifier, version, bundle_path, bundle_hash, json.dumps(validation), stamp))


def set_validation(conn: sqlite3.Connection, identifier: str, version, validation) -> None:
    conn.execute('UPDATE extension_versions SET validation_json=? WHERE extension_id=? AND version=?', (json.dumps(validation), identifier, version))
    conn.execute('UPDATE extensions SET revision=revision+1,updated_at=? WHERE id=?', (db.now_iso(), identifier))


def set_enabled(conn: sqlite3.Connection, identifier: str, enabled: bool, target: str, previous_active: str) -> None:
    stamp = db.now_iso()
    conn.execute('UPDATE extensions SET enabled=?,active_version=?,revision=revision+1,updated_at=? WHERE id=?', (bool(enabled), target if enabled else previous_active, stamp, identifier))
    if enabled and target != previous_active:
        conn.execute('INSERT INTO extension_activations(extension_id,version,created_at) VALUES(?,?,?)', (identifier, target, stamp))


def activation_history(identifier: str):
    with closing(db.get_conn()) as conn:
        return [row[0] for row in conn.execute('SELECT version FROM extension_activations WHERE extension_id=? ORDER BY id DESC', (identifier,))]
