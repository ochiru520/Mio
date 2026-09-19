"""Pending topics and follow-up result persistence."""
from __future__ import annotations

from typing import Any, Callable
import sqlite3


class FollowupsRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_get_conn: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 ) -> None:
        self._dep_get_conn = dep_get_conn
        self._dep_now_iso = dep_now_iso

    def remember_pending_thread(self,
        conversation_id: str,
        content: str,
        follow_up_after: str = "",
        source_message_id: int = 0,
    ) -> int:
        normalized = " ".join(content.split()).strip()[:500]
        if not normalized:
            raise ValueError("待跟进话题不能为空。")
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, content
                FROM pending_threads
                WHERE conversation_id = ? AND status = 'open'
                ORDER BY updated_at DESC
                LIMIT 30
                """,
                (conversation_id,),
            ).fetchall()
            lowered = normalized.casefold()
            for row in rows:
                current = str(row["content"] or "")
                current_lower = current.casefold()
                if lowered == current_lower or (len(lowered) >= 8 and (lowered in current_lower or current_lower in lowered)):
                    conn.execute(
                        """
                        UPDATE pending_threads
                        SET follow_up_after = CASE WHEN ? <> '' THEN ? ELSE follow_up_after END,
                            source_message_id = CASE WHEN ? > 0 THEN ? ELSE source_message_id END,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (follow_up_after, follow_up_after, source_message_id, source_message_id, timestamp, row["id"]),
                    )
                    return int(row["id"])
            cursor = conn.execute(
                """
                INSERT INTO pending_threads (
                    conversation_id, content, status, follow_up_after, source_message_id,
                    last_mentioned_at, created_at, updated_at
                )
                VALUES (?, ?, 'open', ?, ?, '', ?, ?)
                """,
                (conversation_id, normalized, follow_up_after, source_message_id, timestamp, timestamp),
            )
            return int(cursor.lastrowid)


    def list_open_pending_threads(self, conversation_id: str, limit: int = 8) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, conversation_id, content, status, follow_up_after,
                       source_message_id, last_mentioned_at, created_at, updated_at
                FROM pending_threads
                WHERE conversation_id = ? AND status = 'open'
                ORDER BY
                    CASE WHEN follow_up_after = '' THEN 1 ELSE 0 END,
                    follow_up_after ASC,
                    updated_at DESC
                LIMIT ?
                """,
                (conversation_id, max(1, limit)),
            ).fetchall()


    def list_due_pending_threads(self, conversation_id: str, current_iso: str, limit: int = 5) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, conversation_id, content, status, follow_up_after,
                       source_message_id, last_mentioned_at, created_at, updated_at
                FROM pending_threads
                WHERE conversation_id = ?
                  AND status = 'open'
                  AND follow_up_after <> ''
                  AND follow_up_after <= ?
                ORDER BY follow_up_after ASC
                LIMIT ?
                """,
                (conversation_id, current_iso, max(1, limit)),
            ).fetchall()


    def resolve_pending_thread(self, conversation_id: str, query: str) -> int | None:
        normalized = " ".join(query.split()).strip().casefold()
        if not normalized:
            return None
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, content
                FROM pending_threads
                WHERE conversation_id = ? AND status = 'open'
                ORDER BY updated_at DESC
                LIMIT 30
                """,
                (conversation_id,),
            ).fetchall()
            for row in rows:
                content = str(row["content"] or "").casefold()
                if normalized == content or normalized in content or content in normalized:
                    conn.execute(
                        "UPDATE pending_threads SET status = 'resolved', updated_at = ? WHERE id = ?",
                        (timestamp, row["id"]),
                    )
                    return int(row["id"])
        return None


    def mark_pending_thread_mentioned(self, thread_id: int) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            conn.execute(
                """
                UPDATE pending_threads
                SET last_mentioned_at = ?, follow_up_after = '', updated_at = ?
                WHERE id = ?
                """,
                (timestamp, timestamp, thread_id),
            )


    def get_night_close_prompted_date(self, user_id: str) -> str:
        with self._dep_get_conn() as conn:
            row = conn.execute(
                "SELECT prompted_date FROM night_close_states WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return str(row["prompted_date"]) if row else ""


    def set_night_close_prompted_date(self, user_id: str, date: str) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            conn.execute(
                """
                INSERT INTO night_close_states (user_id, prompted_date, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    prompted_date = excluded.prompted_date,
                    updated_at = excluded.updated_at
                """,
                (user_id, date, timestamp),
            )


    def list_all_open_pending_threads(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, conversation_id, content, status, follow_up_after,
                       source_message_id, last_mentioned_at, created_at, updated_at
                FROM pending_threads
                WHERE status = 'open'
                ORDER BY
                    CASE WHEN follow_up_after = '' THEN 1 ELSE 0 END,
                    follow_up_after ASC,
                    updated_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()


    def get_pending_thread(self, thread_id: int) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, conversation_id, content, status, follow_up_after,
                       source_message_id, last_mentioned_at, created_at, updated_at
                FROM pending_threads
                WHERE id = ?
                """,
                (int(thread_id),),
            ).fetchone()


    def find_open_pending_thread(self, conversation_id: str, query: str) -> sqlite3.Row | None:
        normalized = " ".join(str(query or "").split()).strip().casefold()
        if not normalized:
            return None
        with self._dep_get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, content, status, follow_up_after,
                       source_message_id, last_mentioned_at, created_at, updated_at
                FROM pending_threads
                WHERE conversation_id = ? AND status = 'open'
                ORDER BY updated_at DESC
                LIMIT 30
                """,
                (conversation_id,),
            ).fetchall()
        for row in rows:
            content = str(row["content"] or "").casefold()
            if normalized == content or normalized in content or content in normalized:
                return row
        return None


    def record_follow_up_result(self,
        thread_id: int,
        outcome: str,
        summary: str = "",
        adjustment: str = "",
        next_follow_up_after: str = "",
        source_message_id: int = 0,
    ) -> int:
        normalized_outcome = str(outcome or "").strip().lower()
        if normalized_outcome not in {"completed", "partial", "not_completed"}:
            raise ValueError("跟进结果必须是 completed、partial 或 not_completed。")
        clean_summary = " ".join(str(summary or "").split()).strip()[:800]
        clean_adjustment = " ".join(str(adjustment or "").split()).strip()[:500]
        clean_next = str(next_follow_up_after or "").strip()[:40]
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            thread = conn.execute(
                """
                SELECT id, conversation_id, status, follow_up_after
                FROM pending_threads
                WHERE id = ?
                """,
                (int(thread_id),),
            ).fetchone()
            if thread is None:
                raise ValueError("没有找到这个待跟进话题。")
            if str(thread["status"] or "") != "open":
                raise ValueError("这个待跟进话题已经结束。")

            cursor = conn.execute(
                """
                INSERT INTO follow_up_results (
                    thread_id, conversation_id, outcome, summary, adjustment,
                    next_follow_up_after, source_message_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(thread_id),
                    str(thread["conversation_id"]),
                    normalized_outcome,
                    clean_summary,
                    clean_adjustment,
                    clean_next,
                    int(source_message_id or 0),
                    timestamp,
                    timestamp,
                ),
            )
            if normalized_outcome == "completed":
                conn.execute(
                    """
                    UPDATE pending_threads
                    SET status = 'resolved', follow_up_after = '', updated_at = ?
                    WHERE id = ? AND status = 'open'
                    """,
                    (timestamp, int(thread_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE pending_threads
                    SET follow_up_after = ?, updated_at = ?
                    WHERE id = ? AND status = 'open'
                    """,
                    (clean_next, timestamp, int(thread_id)),
                )
            return int(cursor.lastrowid)


    def list_follow_up_results(self,
        *,
        conversation_id: str = "",
        thread_id: int = 0,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        conditions: list[str] = []
        parameters: list[object] = []
        if conversation_id:
            conditions.append("results.conversation_id = ?")
            parameters.append(conversation_id)
        if int(thread_id or 0) > 0:
            conditions.append("results.thread_id = ?")
            parameters.append(int(thread_id))
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        parameters.append(max(1, min(int(limit), 200)))
        with self._dep_get_conn() as conn:
            return conn.execute(
                f"""
                SELECT results.id, results.thread_id, results.conversation_id,
                       results.outcome, results.summary, results.adjustment,
                       results.next_follow_up_after, results.source_message_id,
                       results.created_at, results.updated_at,
                       threads.content AS thread_content
                FROM follow_up_results AS results
                LEFT JOIN pending_threads AS threads ON threads.id = results.thread_id
                {where}
                ORDER BY results.created_at DESC, results.id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()


    def resolve_pending_thread_by_id(self, thread_id: int) -> bool:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                "UPDATE pending_threads SET status = 'resolved', updated_at = ? WHERE id = ? AND status = 'open'",
                (timestamp, thread_id),
            )
            return cursor.rowcount > 0


    def update_pending_thread_by_id(self, thread_id: int, content: str, follow_up_after: str = "") -> bool:
        normalized = " ".join(content.split()).strip()[:500]
        if not normalized:
            raise ValueError("待跟进话题不能为空。")
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_threads
                SET content = ?, follow_up_after = ?, updated_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (normalized, follow_up_after.strip(), self._dep_now_iso(), thread_id),
            )
            return cursor.rowcount > 0


    def delete_pending_thread_by_id(self, thread_id: int) -> bool:
        with self._dep_get_conn() as conn:
            cursor = conn.execute("DELETE FROM pending_threads WHERE id = ?", (thread_id,))
            return cursor.rowcount > 0
