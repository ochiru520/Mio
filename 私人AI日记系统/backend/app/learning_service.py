"""Small, durable learning loop built on project knowledge and notes."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from contextlib import closing

from . import db, workspace_service as workspace


def initialize() -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS learning_sessions(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
          goal TEXT NOT NULL DEFAULT '', source_document_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS learning_items(
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL, prompt TEXT NOT NULL,
          answer_key TEXT NOT NULL DEFAULT '', source_ref TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS learning_attempts(
          id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, answer TEXT NOT NULL,
          score REAL NOT NULL, feedback TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_learning_sessions_project ON learning_sessions(project_id,updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_learning_items_session ON learning_items(session_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_learning_attempts_item ON learning_attempts(item_id,created_at DESC);
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(learning_items)")}
        if "review_at" not in columns:
            conn.execute("ALTER TABLE learning_items ADD COLUMN review_at TEXT NOT NULL DEFAULT ''")
        if "review_streak" not in columns:
            conn.execute("ALTER TABLE learning_items ADD COLUMN review_streak INTEGER NOT NULL DEFAULT 0")


def _session(session_id: str, project_id: str = ""):
    initialize()
    with closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM learning_sessions WHERE id=? AND (?='' OR project_id=?)",
                           (session_id, project_id, project_id)).fetchone()
    if row is None:
        raise ValueError("学习会话不存在或不属于当前项目。")
    return row


def _public(session_id: str, project_id: str = "") -> dict:
    row = _session(session_id, project_id)
    with closing(db.get_conn()) as conn:
        items = conn.execute("SELECT * FROM learning_items WHERE session_id=? ORDER BY created_at,id", (session_id,)).fetchall()
        attempts = conn.execute("""SELECT a.* FROM learning_attempts a JOIN learning_items i ON i.id=a.item_id
            WHERE i.session_id=? ORDER BY a.created_at DESC,a.id DESC""", (session_id,)).fetchall()
    attempt_map = {}
    for attempt in attempts:
        attempt_map.setdefault(attempt["item_id"], dict(attempt))
    data = dict(row)
    data["items"] = [{**dict(item), "latest_attempt": attempt_map.get(item["id"])} for item in items]
    scores = [float(item["latest_attempt"]["score"]) for item in data["items"] if item["latest_attempt"]]
    data["progress"] = {"total": len(items), "answered": len(scores), "average_score": round(sum(scores) / len(scores), 3) if scores else 0}
    return data


def sessions(project_id: str) -> list[dict]:
    workspace.require_project(project_id)
    initialize()
    with closing(db.get_conn()) as conn:
        ids = [row["id"] for row in conn.execute("SELECT id FROM learning_sessions WHERE project_id=? ORDER BY updated_at DESC,id", (project_id,))]
    return [_public(identifier, project_id) for identifier in ids]


def create_session(project_id: str, title: str, goal: str = "", source_document_id: str = "") -> dict:
    workspace.require_project(project_id, active=True)
    initialize()
    if not str(title or "").strip():
        raise ValueError("学习会话需要标题。")
    if source_document_id:
        with closing(db.get_conn()) as conn:
            if not conn.execute("SELECT 1 FROM knowledge_documents WHERE id=? AND project_id=?", (source_document_id, project_id)).fetchone():
                raise ValueError("资料不属于当前项目。")
    identifier = "learn_" + uuid.uuid4().hex[:24]
    now = db.now_iso()
    with closing(db.get_conn()) as conn, conn:
        conn.execute("INSERT INTO learning_sessions VALUES(?,?,?,?,?,?,?,?)",
                     (identifier, project_id, title.strip()[:160], str(goal or "").strip()[:2000], source_document_id, "active", now, now))
    return _public(identifier, project_id)


def add_item(project_id: str, session_id: str, prompt: str, answer_key: str = "", source_ref: str = "") -> dict:
    _session(session_id, project_id)
    if not str(prompt or "").strip():
        raise ValueError("练习题目不能为空。")
    identifier = "learn_item_" + uuid.uuid4().hex[:24]
    with closing(db.get_conn()) as conn, conn:
        conn.execute("""INSERT INTO learning_items
            (id,session_id,prompt,answer_key,source_ref,created_at,review_at,review_streak)
            VALUES(?,?,?,?,?,?,?,?)""",
                     (identifier, session_id, prompt.strip()[:5000], str(answer_key or "").strip()[:2000],
                      str(source_ref or "").strip()[:500], db.now_iso(), '', 0))
        conn.execute("UPDATE learning_sessions SET updated_at=? WHERE id=?", (db.now_iso(), session_id))
    return next(item for item in _public(session_id, project_id)["items"] if item["id"] == identifier)


def answer(project_id: str, item_id: str, value: str) -> dict:
    if not str(value or "").strip():
        raise ValueError("作答内容不能为空。")
    initialize()
    with closing(db.get_conn()) as conn:
        item = conn.execute("""SELECT i.*,s.project_id FROM learning_items i JOIN learning_sessions s ON s.id=i.session_id
            WHERE i.id=? AND s.project_id=?""", (item_id, project_id)).fetchone()
    if item is None:
        raise ValueError("练习不存在或不属于当前项目。")
    expected = [part.strip().casefold() for part in re.split(r"[,，、;；|]", str(item["answer_key"] or "")) if part.strip()]
    actual = str(value).strip().casefold()
    if not expected:
        score, feedback = 0.0, "已保存作答；这道题没有设置可自动核对的答案，请结合资料人工复盘。"
    else:
        matched = sum(1 for term in expected if term in actual)
        score = round(matched / len(expected), 3)
        feedback = "要点已覆盖。" if score == 1 else f"已覆盖 {matched}/{len(expected)} 个要点，回看资料后再补充。"
    with closing(db.get_conn()) as conn, conn:
        cursor = conn.execute("INSERT INTO learning_attempts(item_id,answer,score,feedback,created_at) VALUES(?,?,?,?,?)",
                              (item_id, value.strip()[:10000], score, feedback, db.now_iso()))
        previous = int(item["review_streak"] or 0)
        streak = previous + 1 if score >= 0.8 else 0
        intervals = (1, 3, 7, 14, 30)
        days = intervals[min(streak, len(intervals)) - 1] if streak else 1
        review_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        conn.execute("UPDATE learning_items SET review_at=?, review_streak=? WHERE id=?", (review_at, streak, item_id))
        conn.execute("UPDATE learning_sessions SET updated_at=? WHERE id=?", (db.now_iso(), item["session_id"]))
        attempt_id = cursor.lastrowid
    return {"id": attempt_id, "item_id": item_id, "answer": value.strip(), "score": score, "feedback": feedback, "review_at": review_at, "review_streak": streak}


def review(project_id: str, limit: int = 50) -> dict:
    """Return unanswered, low-scoring, or due items without changing their schedule."""
    workspace.require_project(project_id)
    initialize()
    now = datetime.now(timezone.utc).isoformat()
    with closing(db.get_conn()) as conn:
        rows = conn.execute("""
            SELECT i.*, s.title AS session_title, s.project_id,
                   a.answer AS latest_answer, a.score AS latest_score,
                   a.feedback AS latest_feedback, a.created_at AS answered_at
            FROM learning_items i
            JOIN learning_sessions s ON s.id=i.session_id
            LEFT JOIN learning_attempts a ON a.id=(
              SELECT a2.id FROM learning_attempts a2 WHERE a2.item_id=i.id ORDER BY a2.created_at DESC,a2.id DESC LIMIT 1)
            WHERE s.project_id=? AND (i.review_at='' OR i.review_at<=? OR a.score<0.8)
            ORDER BY CASE WHEN a.score IS NULL THEN 0 ELSE a.score END ASC,
                     CASE WHEN i.review_at='' THEN '' ELSE i.review_at END ASC, i.created_at ASC
            LIMIT ?
        """, (project_id, now, max(1, min(int(limit), 200)))).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows), "as_of": now}


def session(project_id: str, session_id: str) -> dict:
    return _public(session_id, project_id)
