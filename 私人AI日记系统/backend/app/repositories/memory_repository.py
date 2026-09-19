"""Structured memory storage and optional FTS index."""
from __future__ import annotations

from typing import Any, Callable
import re
import sqlite3


class MemoryRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_get_conn: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 ) -> None:
        self._dep_get_conn = dep_get_conn
        self._dep_now_iso = dep_now_iso

    def _memory_search_text(self, *values: object) -> str:
        text = " ".join(str(value or "") for value in values).strip().casefold()
        if not text:
            return ""
        tokens: list[str] = []
        for run in re.findall(r"[a-z0-9_]+", text):
            tokens.append(run)
        compact_cjk = "".join(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
        tokens.extend(compact_cjk[index : index + 2] for index in range(max(0, len(compact_cjk) - 1)))
        return " ".join(dict.fromkeys(token for token in tokens if token))


    def _ensure_structured_memory_fts(self, conn: sqlite3.Connection) -> bool:
        """Create/backfill the optional FTS index without making startup depend on FTS5."""
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS structured_memories_fts
                USING fts5(memory_id UNINDEXED, search_text)
                """
            )
            rows = conn.execute(
                """
                SELECT id, memory_key, category, content
                FROM structured_memories
                """
            ).fetchall()
            for row in rows:
                conn.execute(
                    "DELETE FROM structured_memories_fts WHERE memory_id = ?",
                    (str(row["id"]),),
                )
                conn.execute(
                    "INSERT INTO structured_memories_fts(memory_id, search_text) VALUES (?, ?)",
                    (
                        str(row["id"]),
                        self._memory_search_text(row["memory_key"], row["category"], row["content"]),
                    ),
                )
            return True
        except sqlite3.OperationalError:
            return False


    def _upsert_structured_memory_fts(self,
        conn: sqlite3.Connection,
        memory_id: int,
        memory_key: str,
        category: str,
        content: str,
    ) -> None:
        try:
            conn.execute("DELETE FROM structured_memories_fts WHERE memory_id = ?", (str(memory_id),))
            conn.execute(
                "INSERT INTO structured_memories_fts(memory_id, search_text) VALUES (?, ?)",
                (str(memory_id), self._memory_search_text(memory_key, category, content)),
            )
        except sqlite3.OperationalError:
            # FTS5 is optional; memory_service has a deterministic fallback.
            return


    def refresh_manual_memories(self, manuals: list[dict[str, object]]) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            conn.execute("DELETE FROM memories WHERE type = 'manual'")
            for manual in manuals:
                content = str(manual.get("content") or "")
                if not content:
                    continue
                conn.execute(
                    """
                    INSERT INTO memories (type, content, importance, tags, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "manual",
                        content,
                        5,
                        str(manual.get("name") or ""),
                        timestamp,
                        timestamp,
                    ),
                )


    def get_latest_memory(self, memory_type: str, tags: str = "") -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, type, content, importance, tags, created_at, updated_at
                FROM memories
                WHERE type = ?
                  AND tags = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (memory_type, tags),
            ).fetchone()


    def replace_memory(self, memory_type: str, content: str, importance: int = 3, tags: str = "") -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            conn.execute(
                "DELETE FROM memories WHERE type = ? AND tags = ?",
                (memory_type, tags),
            )
            conn.execute(
                """
                INSERT INTO memories (type, content, importance, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (memory_type, content, importance, tags, timestamp, timestamp),
            )


    def delete_memory(self, memory_type: str, tags: str = "") -> bool:
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM memories WHERE type = ? AND tags = ?",
                (memory_type, tags),
            )
            return cursor.rowcount > 0


    def save_structured_memory(self,
        layer: str,
        category: str,
        memory_key: str,
        content: str,
        source_conversation_id: str = "",
        source_message_id: int = 0,
        confidence: float = 0.0,
        occurred_at: str = "",
        learned_at: str = "",
        valid_from: str = "",
        valid_until: str = "",
        last_confirmed_at: str = "",
        time_confidence: float = 0.0,
        temporal_status: str = "time_unknown",
        source_window: str = "",
    ) -> tuple[int, str]:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_user_controls'").fetchone()
            if exists:
                controlled = conn.execute('SELECT c.action,c.memory_id,m.content FROM memory_user_controls c JOIN structured_memories m ON m.id=c.memory_id WHERE c.category=? AND c.memory_key=?', (category, memory_key)).fetchone()
                if controlled and (controlled['action'] == 'archive' or controlled['content'] != content):
                    raise ValueError('该记忆已有用户修订，请保留修订并将新推断作为候选等待确认。')
                if controlled:
                    return int(controlled['memory_id']), 'reinforced'
            current = conn.execute(
                """
                SELECT id, content, confidence
                FROM structured_memories
                WHERE memory_key = ? AND category = ? AND status = 'active'
                ORDER BY id DESC
                LIMIT 1
                """,
                (memory_key, category),
            ).fetchone()
            if current is not None and str(current["content"] or "").casefold() == content.casefold():
                conn.execute(
                    """
                    UPDATE structured_memories
                    SET layer = ?, source_conversation_id = ?, source_message_id = ?,
                        source_window = CASE WHEN ? <> '' THEN ? ELSE source_window END,
                        confidence = MAX(confidence, ?),
                        occurred_at = CASE WHEN ? <> '' THEN ? ELSE occurred_at END,
                        learned_at = CASE WHEN ? <> '' THEN ? ELSE learned_at END,
                        valid_from = CASE WHEN ? <> '' THEN ? ELSE valid_from END,
                        valid_until = CASE WHEN ? <> '' THEN ? ELSE valid_until END,
                        last_confirmed_at = CASE WHEN ? <> '' THEN ? ELSE last_confirmed_at END,
                        time_confidence = MAX(time_confidence, ?),
                        temporal_status = CASE WHEN ? <> 'time_unknown' THEN ? ELSE temporal_status END,
                        last_seen_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        layer,
                        source_conversation_id,
                        int(source_message_id or 0),
                        source_window, source_window,
                        float(confidence),
                        occurred_at, occurred_at,
                        learned_at, learned_at,
                        valid_from, valid_from,
                        valid_until, valid_until,
                        last_confirmed_at, last_confirmed_at,
                        float(time_confidence),
                        temporal_status, temporal_status,
                        timestamp,
                        timestamp,
                        int(current["id"]),
                    ),
                )
                self._upsert_structured_memory_fts(conn, int(current["id"]), memory_key, category, content)
                return int(current["id"]), "reinforced"

            cursor = conn.execute(
                """
                INSERT INTO structured_memories (
                    layer, category, memory_key, content, source_conversation_id, source_window,
                    source_message_id, confidence, status, superseded_by,
                    occurred_at, learned_at, valid_from, valid_until,
                    last_confirmed_at, time_confidence, temporal_status,
                    last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    layer,
                    category,
                    memory_key,
                    content,
                    source_conversation_id,
                    source_window,
                    int(source_message_id or 0),
                    float(confidence),
                    occurred_at,
                    learned_at,
                    valid_from,
                    valid_until,
                    last_confirmed_at,
                    float(time_confidence),
                    temporal_status,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            memory_id = int(cursor.lastrowid)
            self._upsert_structured_memory_fts(conn, memory_id, memory_key, category, content)
            if current is not None:
                conn.execute(
                    """
                    UPDATE structured_memories
                    SET status = 'superseded', superseded_by = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (memory_id, timestamp, int(current["id"])),
                )
                return memory_id, "superseded"
            return memory_id, "created"


    def save_structured_memory_candidate(self,
        layer: str,
        category: str,
        memory_key: str,
        content: str,
        source_conversation_id: str = "",
        source_message_id: int = 0,
        confidence: float = 0.0,
        occurred_at: str = "",
        learned_at: str = "",
        valid_from: str = "",
        valid_until: str = "",
        last_confirmed_at: str = "",
        time_confidence: float = 0.0,
        temporal_status: str = "time_unknown",
        source_window: str = "",
    ) -> int:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO structured_memories (
                    layer, category, memory_key, content, source_conversation_id, source_window,
                    source_message_id, confidence, status, superseded_by,
                    occurred_at, learned_at, valid_from, valid_until,
                    last_confirmed_at, time_confidence, temporal_status,
                    last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'candidate', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    layer,
                    category,
                    memory_key,
                    content,
                    source_conversation_id,
                    source_window,
                    int(source_message_id or 0),
                    float(confidence),
                    occurred_at,
                    learned_at,
                    valid_from,
                    valid_until,
                    last_confirmed_at,
                    float(time_confidence),
                    temporal_status,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            memory_id = int(cursor.lastrowid)
            self._upsert_structured_memory_fts(conn, memory_id, memory_key, category, content)
            return memory_id


    def list_structured_memories(self,
        *,
        status: str = "active",
        layer: str = "",
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        conditions: list[str] = []
        parameters: list[object] = []
        if status:
            conditions.append("status = ?")
            parameters.append(status)
        if layer:
            conditions.append("layer = ?")
            parameters.append(layer)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        parameters.append(max(1, min(int(limit), 500)))
        with self._dep_get_conn() as conn:
            return conn.execute(
                f"""
                SELECT id, layer, category, memory_key, content, source_conversation_id, source_window,
                       source_message_id, confidence, status, superseded_by,
                       occurred_at, learned_at, valid_from, valid_until,
                       last_confirmed_at, time_confidence, temporal_status,
                       last_seen_at, created_at, updated_at
                FROM structured_memories
                {where}
                ORDER BY
                    CASE layer WHEN 'L0' THEN 0 WHEN 'L1' THEN 1 ELSE 2 END,
                    confidence DESC,
                    updated_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()


    def get_structured_memory(self, memory_id: int) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, layer, category, memory_key, content, source_conversation_id, source_window,
                       source_message_id, confidence, status, superseded_by,
                       occurred_at, learned_at, valid_from, valid_until,
                       last_confirmed_at, time_confidence, temporal_status,
                       last_seen_at, created_at, updated_at
                FROM structured_memories
                WHERE id = ?
                """,
                (int(memory_id),),
            ).fetchone()


    def restore_structured_memory(self, memory_id: int) -> bool:
        """Restore a superseded or archived version as the only active value for its slot."""
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            target = conn.execute(
                """
                SELECT id, category, memory_key, status
                FROM structured_memories
                WHERE id = ?
                """,
                (int(memory_id),),
            ).fetchone()
            if target is None or str(target["status"] or "") not in {"superseded", "archived"}:
                return False

            conn.execute(
                """
                UPDATE structured_memories
                SET status = 'superseded', superseded_by = ?, updated_at = ?
                WHERE category = ? AND memory_key = ? AND status = 'active' AND id <> ?
                """,
                (
                    int(memory_id),
                    timestamp,
                    str(target["category"]),
                    str(target["memory_key"]),
                    int(memory_id),
                ),
            )
            cursor = conn.execute(
                """
                UPDATE structured_memories
                SET status = 'active', superseded_by = 0, last_seen_at = ?, updated_at = ?
                WHERE id = ? AND status IN ('superseded', 'archived')
                """,
                (timestamp, timestamp, int(memory_id)),
            )
            return cursor.rowcount > 0


    def search_structured_memories(self,
        query: str,
        *,
        status: str = "active",
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        """Search memory with FTS5, falling back to LIKE when FTS5 is unavailable."""
        clean_query = str(query or "").strip().casefold()
        if not clean_query:
            return self.list_structured_memories(status=status, limit=limit)
        terms = self._memory_search_text(clean_query).split()
        match_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:32])
        with self._dep_get_conn() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT memories.id, memories.layer, memories.category, memories.memory_key,
                           memories.content, memories.source_conversation_id, memories.source_window,
                           memories.source_message_id, memories.confidence, memories.status,
                           memories.superseded_by, memories.occurred_at, memories.learned_at,
                           memories.valid_from, memories.valid_until, memories.last_confirmed_at,
                           memories.time_confidence, memories.temporal_status,
                           memories.last_seen_at, memories.created_at,
                           memories.updated_at
                    FROM structured_memories_fts
                    JOIN structured_memories memories
                      ON memories.id = CAST(structured_memories_fts.memory_id AS INTEGER)
                    WHERE structured_memories_fts.search_text MATCH ? AND memories.status = ?
                    ORDER BY bm25(structured_memories_fts), memories.confidence DESC,
                             memories.updated_at DESC
                    LIMIT ?
                    """,
                    (match_query, status, max(1, min(int(limit), 500))),
                ).fetchall()
                if rows:
                    return list(rows)
            except sqlite3.OperationalError:
                pass

            like = f"%{clean_query}%"
            return list(
                conn.execute(
                    """
                    SELECT id, layer, category, memory_key, content, source_conversation_id, source_window,
                           source_message_id, confidence, status, superseded_by,
                           occurred_at, learned_at, valid_from, valid_until,
                           last_confirmed_at, time_confidence, temporal_status,
                           last_seen_at, created_at, updated_at
                    FROM structured_memories
                    WHERE status = ?
                      AND (content LIKE ? OR memory_key LIKE ? OR category LIKE ?)
                    ORDER BY confidence DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (status, like, like, like, max(1, min(int(limit), 500))),
                ).fetchall()
            )


    def set_structured_memory_status(self, memory_id: int, status: str) -> bool:
        if status not in {"active", "archived", "sleeping"}:
            raise ValueError("记忆状态不受支持。")
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                "UPDATE structured_memories SET status = ?, updated_at = ? WHERE id = ?",
                (status, self._dep_now_iso(), int(memory_id)),
            )
            return cursor.rowcount > 0


    def sleep_stale_structured_memories(self, before_timestamp: str) -> int:
        """Move stale recent-state memories out of the active prompt context."""
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE structured_memories
                SET status = 'sleeping'
                WHERE status = 'active'
                  AND layer = 'L1'
                  AND last_seen_at < ?
                """,
                (str(before_timestamp),),
            )
            return max(0, int(cursor.rowcount))


    def mark_structured_memories_seen(self, memory_ids: list[int]) -> int:
        clean_ids = sorted({int(memory_id) for memory_id in memory_ids if int(memory_id) > 0})
        if not clean_ids:
            return 0
        placeholders = ", ".join("?" for _ in clean_ids)
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                f"""
                UPDATE structured_memories
                SET last_seen_at = ?
                WHERE id IN ({placeholders}) AND status = 'active'
                """,
                (self._dep_now_iso(), *clean_ids),
            )
            return max(0, int(cursor.rowcount))


    def confirm_structured_memory_candidate(self, memory_id: int) -> bool:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            candidate = conn.execute(
                "SELECT * FROM structured_memories WHERE id = ? AND status = 'candidate'",
                (int(memory_id),),
            ).fetchone()
            if candidate is None:
                return False
            current = conn.execute(
                """
                SELECT id FROM structured_memories
                WHERE memory_key = ? AND category = ? AND status = 'active' AND id <> ?
                ORDER BY id DESC LIMIT 1
                """,
                (candidate["memory_key"], candidate["category"], int(memory_id)),
            ).fetchone()
            if current is not None:
                conn.execute(
                    """
                    UPDATE structured_memories
                    SET status = 'superseded', superseded_by = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (int(memory_id), timestamp, int(current["id"])),
                )
            conn.execute(
                "UPDATE structured_memories SET status = 'active', updated_at = ? WHERE id = ?",
                (timestamp, int(memory_id)),
            )
            return True


    def archive_structured_memory(self, memory_id: int) -> bool:
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE structured_memories
                SET status = 'archived', updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (self._dep_now_iso(), int(memory_id)),
            )
            return cursor.rowcount > 0


    def list_memories_by_type(self, memory_type: str) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, type, content, importance, tags, created_at, updated_at
                FROM memories
                WHERE type = ?
                ORDER BY updated_at DESC
                """,
                (memory_type,),
            ).fetchall()
