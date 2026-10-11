"""Versioned read/write access to user-authorized Markdown folders."""
from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import closing
from pathlib import Path

from . import db, workspace_service as workspace
from . import agent_file_service as files


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def initialize() -> None:
    workspace.initialize()
    with closing(db.get_conn()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspace_external_roots(
              id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,label TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspace_external_notes(
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,root_id TEXT NOT NULL,
              relative_path TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1,
              content_sha256 TEXT NOT NULL DEFAULT '',file_mtime_ns INTEGER NOT NULL DEFAULT 0,
              file_size INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
              UNIQUE(project_id,root_id,relative_path)
            );
            CREATE INDEX IF NOT EXISTS idx_external_notes_project
              ON workspace_external_notes(project_id,updated_at DESC);
            """
        )


def _safe_root(value: str) -> Path:
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir() or path.parent == path or files._private_path(path, grant=True):
        raise ValueError("只能授权具体的普通资料目录，不能授权磁盘根目录或 Mio 私人数据目录。")
    return path


def _root_row(root_id: str):
    initialize()
    with closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM workspace_external_roots WHERE id=?", (root_id,)).fetchone()
    if row is None:
        raise ValueError("外部笔记目录不存在。")
    root = _safe_root(row["path"])
    if str(root) != str(row["path"]):
        raise ValueError("外部笔记目录路径已变化，请重新授权。")
    return row, root


def _note_row(project_id: str, note_id: str):
    workspace.require_project(project_id)
    with closing(db.get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM workspace_external_notes WHERE project_id=? AND id=?",
            (project_id, note_id),
        ).fetchone()
    if row is None:
        raise ValueError("外部笔记不存在或不属于当前项目。")
    return row


def _resolve_file(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ":" in relative_path or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("外部笔记必须是授权目录内的相对路径。")
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_file() or not candidate.is_relative_to(root) or candidate.suffix.lower() != ".md":
        raise ValueError("只支持授权目录内的 Markdown 文件。")
    if any(part.startswith(".") for part in candidate.relative_to(root).parts):
        raise ValueError("不允许读取隐藏目录或隐藏文件。")
    return candidate


def _public(row, root: Path, path: Path, *, content: str = "", conflict: bool = False) -> dict:
    stat = path.stat()
    return {
        "id": row["id"], "project_id": row["project_id"], "root_id": row["root_id"],
        "root_path": str(root), "relative_path": row["relative_path"], "path": str(path),
        "title": row["title"], "revision": int(row["revision"]),
        "content_sha256": row["content_sha256"], "current_sha256": _digest(content) if content else _digest(path.read_text(encoding="utf-8-sig")),
        "file_size": stat.st_size, "file_mtime_ns": stat.st_mtime_ns, "conflict": conflict,
        "content": content,
        "updated_at": row["updated_at"],
    }


def roots() -> list[dict]:
    initialize()
    with closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM workspace_external_roots ORDER BY path").fetchall()
    result = []
    for row in rows:
        try:
            root = _safe_root(row["path"])
            result.append({**dict(row), "path": str(root), "available": True})
        except (ValueError, OSError):
            result.append({**dict(row), "available": False})
    return result


def authorize(path: str, label: str = "") -> dict:
    initialize()
    root = _safe_root(path)
    now = db.now_iso()
    root_id = "root_" + uuid.uuid4().hex[:20]
    with closing(db.get_conn()) as conn, conn:
        conn.execute(
            """INSERT INTO workspace_external_roots(id,path,label,created_at,updated_at)
               VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET label=excluded.label,updated_at=excluded.updated_at""",
            (root_id, str(root), label.strip()[:160], now, now),
        )
        row = conn.execute("SELECT * FROM workspace_external_roots WHERE path=?", (str(root),)).fetchone()
    return {**dict(row), "available": True}


def list_notes(project_id: str) -> list[dict]:
    initialize(); workspace.require_project(project_id)
    with closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM workspace_external_notes WHERE project_id=? ORDER BY updated_at DESC,id", (project_id,)).fetchall()
    result = []
    for row in rows:
        try:
            _, root = _root_row(row["root_id"])
            path = _resolve_file(root, row["relative_path"])
            raw = path.read_text(encoding="utf-8-sig")
            result.append(_public(row, root, path, conflict=_digest(raw) != row["content_sha256"]))
        except (ValueError, OSError, UnicodeDecodeError):
            result.append({**dict(row), "available": False, "conflict": True})
    return result


def link(project_id: str, root_id: str, relative_path: str) -> dict:
    initialize(); workspace.require_project(project_id, active=True)
    row, root = _root_row(root_id)
    path = _resolve_file(root, relative_path)
    raw = path.read_text(encoding="utf-8-sig")
    now = db.now_iso(); digest = _digest(raw)
    title = next((line[1:].strip() for line in raw.splitlines() if line.startswith("# ")), path.stem)
    note_id = "external_" + uuid.uuid4().hex[:20]
    with closing(db.get_conn()) as conn, conn:
        existing = conn.execute(
            "SELECT * FROM workspace_external_notes WHERE project_id=? AND root_id=? AND relative_path=?",
            (project_id, root_id, str(Path(relative_path))),
        ).fetchone()
        if existing:
            note_id = existing["id"]
            conn.execute("UPDATE workspace_external_notes SET title=?,content_sha256=?,file_mtime_ns=?,file_size=?,updated_at=? WHERE id=?", (title, digest, path.stat().st_mtime_ns, path.stat().st_size, now, note_id))
        else:
            conn.execute("INSERT INTO workspace_external_notes VALUES(?,?,?,?,?,?,?,?,?,?,?)", (note_id, project_id, root_id, str(Path(relative_path)), title, 1, digest, path.stat().st_mtime_ns, path.stat().st_size, now, now))
    return read(project_id, note_id)


def read(project_id: str, note_id: str) -> dict:
    row = _note_row(project_id, note_id)
    _, root = _root_row(row["root_id"])
    path = _resolve_file(root, row["relative_path"])
    raw = path.read_text(encoding="utf-8-sig")
    return _public(row, root, path, content=raw, conflict=_digest(raw) != row["content_sha256"])


def refresh(project_id: str, note_id: str) -> dict:
    """Accept the current on-disk file as the next explicit edit baseline."""
    row = _note_row(project_id, note_id)
    _, root = _root_row(row["root_id"]); path = _resolve_file(root, row["relative_path"])
    raw = path.read_text(encoding="utf-8-sig"); digest = _digest(raw); now = db.now_iso(); stat = path.stat()
    with closing(db.get_conn()) as conn, conn:
        conn.execute(
            "UPDATE workspace_external_notes SET title=?,revision=revision+1,content_sha256=?,file_mtime_ns=?,file_size=?,updated_at=? WHERE id=?",
            (next((line[1:].strip() for line in raw.splitlines() if line.startswith('# ')), path.stem), digest, stat.st_mtime_ns, stat.st_size, now, note_id),
        )
    return read(project_id, note_id)


def write(project_id: str, note_id: str, content: str, expected_revision: int, expected_sha256: str) -> dict:
    row = _note_row(project_id, note_id)
    if int(expected_revision) != int(row["revision"]):
        raise ValueError(f"外部笔记已在其他窗口更新，请重新读取（当前版本 {row['revision']}）。")
    _, root = _root_row(row["root_id"]); path = _resolve_file(root, row["relative_path"])
    current = path.read_text(encoding="utf-8-sig")
    current_digest = _digest(current)
    if expected_sha256 != current_digest or current_digest != row["content_sha256"]:
        raise ValueError("外部笔记原文已被目录中的其他程序修改，请先重新读取并确认差异。")
    if not content.strip():
        raise ValueError("外部笔记内容不能为空。")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    digest = _digest(content); now = db.now_iso(); stat = path.stat()
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE workspace_external_notes SET title=?,revision=revision+1,content_sha256=?,file_mtime_ns=?,file_size=?,updated_at=? WHERE id=?", (next((line[1:].strip() for line in content.splitlines() if line.startswith('# ')), path.stem), digest, stat.st_mtime_ns, stat.st_size, now, note_id))
    return read(project_id, note_id)
