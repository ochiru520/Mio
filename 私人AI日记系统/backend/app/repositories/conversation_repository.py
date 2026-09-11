from __future__ import annotations

import sqlite3
from collections.abc import Callable


ConnectionFactory = Callable[[], sqlite3.Connection]
TimestampProvider = Callable[[], str]
DayBoundsProvider = Callable[[str], tuple[str, str]]


class ConversationRepository:
    """Persistence for messages and desktop conversation windows."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        timestamp_provider: TimestampProvider,
        day_bounds_provider: DayBoundsProvider,
    ) -> None:
        self._connection_factory = connection_factory
        self._timestamp_provider = timestamp_provider
        self._day_bounds_provider = day_bounds_provider

    def get_recent_messages(self, limit: int = 30, conversation_id: str = "default") -> list[sqlite3.Row]:
        with self._connection_factory() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, source, conversation_id, created_at,
                       request_id, model_id, provider_model, reasoning_level, prompt_tokens,
                       cached_prompt_tokens, completion_tokens, reasoning_tokens,
                       request_cost_yuan, request_cost_source, attachments_json,
                       emotion, first_token_latency_ms, total_latency_ms
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def list_recent_private_user_messages(self, limit: int = 500) -> list[sqlite3.Row]:
        with self._connection_factory() as conn:
            rows = conn.execute(
                """
                SELECT id, content, conversation_id, created_at
                FROM messages
                WHERE role = 'user' AND conversation_id NOT LIKE 'qq_group_%'
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 2000)),),
            ).fetchall()
        return list(reversed(rows))

    def get_message_by_id(self, message_id: int) -> sqlite3.Row | None:
        conn = self._connection_factory()
        try:
            return conn.execute(
                """
                SELECT id, role, content, source, conversation_id, created_at,
                       first_token_latency_ms, total_latency_ms
                FROM messages
                WHERE id = ?
                """,
                (int(message_id),),
            ).fetchone()
        finally:
            conn.close()

    def get_latest_message_id(self, role: str = "", conversation_id: str = "") -> int:
        clauses: list[str] = []
        params: tuple[object, ...] = ()
        if role:
            clauses.append("role = ?")
            params += (role,)
        if conversation_id:
            clauses.append("conversation_id = ?")
            params += (conversation_id,)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection_factory() as conn:
            row = conn.execute(
                f"SELECT COALESCE(MAX(id), 0) AS latest_id FROM messages {where}",
                params,
            ).fetchone()
        return int(row["latest_id"] if row else 0)

    def get_messages_after_id(
        self,
        after_id: int,
        role: str = "",
        limit: int = 50,
        conversation_id: str = "",
    ) -> list[sqlite3.Row]:
        where = "WHERE id > ?"
        params: tuple[object, ...] = (max(0, int(after_id)),)
        if role:
            where += " AND role = ?"
            params += (role,)
        if conversation_id:
            where += " AND conversation_id = ?"
            params += (conversation_id,)
        params += (max(1, min(int(limit), 200)),)
        with self._connection_factory() as conn:
            return conn.execute(
                f"""
                SELECT id, role, content, source, conversation_id, created_at,
                       request_id, emotion, model_id, provider_model,
                       first_token_latency_ms, total_latency_ms
                FROM messages
                {where}
                ORDER BY id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()

    def create_agent_conversation(self, conversation_id: str, title: str = "新对话") -> sqlite3.Row:
        timestamp = self._timestamp_provider()
        with self._connection_factory() as conn:
            conn.execute(
                """
                INSERT INTO agent_conversations (id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, title.strip() or "新对话", timestamp, timestamp),
            )
            return conn.execute(
                "SELECT id, title, created_at, updated_at FROM agent_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()

    def get_agent_conversation(self, conversation_id: str) -> sqlite3.Row | None:
        with self._connection_factory() as conn:
            return conn.execute(
                "SELECT id, title, created_at, updated_at FROM agent_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()

    def list_agent_conversations(self, limit: int = 40) -> list[sqlite3.Row]:
        with self._connection_factory() as conn:
            return conn.execute(
                """
                SELECT c.id, c.title, c.created_at,
                       CASE
                           WHEN MAX(m.created_at) > c.updated_at THEN MAX(m.created_at)
                           ELSE c.updated_at
                       END AS updated_at,
                       COALESCE((
                           SELECT content
                           FROM messages latest
                           WHERE latest.conversation_id = c.id
                           ORDER BY latest.id DESC
                           LIMIT 1
                       ), '') AS preview
                FROM agent_conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                GROUP BY c.id, c.title, c.created_at, c.updated_at
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()

    def touch_agent_conversation(self, conversation_id: str, first_message: str = "") -> None:
        timestamp = self._timestamp_provider()
        title = " ".join(first_message.split()).strip()[:28]
        with self._connection_factory() as conn:
            row = conn.execute(
                "SELECT title FROM agent_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO agent_conversations (id, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (conversation_id, title or "新对话", timestamp, timestamp),
                )
                return
            next_title = title if str(row["title"] or "") == "新对话" and title else str(row["title"])
            conn.execute(
                "UPDATE agent_conversations SET title = ?, updated_at = ? WHERE id = ?",
                (next_title, timestamp, conversation_id),
            )

    def rename_agent_conversation(self, conversation_id: str, title: str) -> sqlite3.Row | None:
        normalized = " ".join(title.split()).strip()[:60]
        if not normalized:
            raise ValueError("对话名称不能为空。")
        with self._connection_factory() as conn:
            cursor = conn.execute(
                "UPDATE agent_conversations SET title = ?, updated_at = ? WHERE id = ?",
                (normalized, self._timestamp_provider(), conversation_id),
            )
            if cursor.rowcount <= 0:
                return None
            return conn.execute(
                "SELECT id, title, created_at, updated_at FROM agent_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()

    def list_conversation_attachment_records(self, conversation_id: str) -> list[str]:
        with self._connection_factory() as conn:
            rows = conn.execute(
                """
                SELECT attachments_json
                FROM messages
                WHERE conversation_id = ? AND attachments_json <> '[]'
                """,
                (conversation_id,),
            ).fetchall()
        return [str(row["attachments_json"] or "[]") for row in rows]

    def list_message_attachment_records(
        self,
        conversation_id: str,
        message_ids: list[int],
    ) -> list[str]:
        normalized_ids = sorted({int(message_id) for message_id in message_ids if int(message_id) > 0})
        if not normalized_ids:
            return []
        placeholders = ", ".join("?" for _ in normalized_ids)
        with self._connection_factory() as conn:
            rows = conn.execute(
                f"""
                SELECT attachments_json
                FROM messages
                WHERE conversation_id = ? AND id IN ({placeholders}) AND attachments_json <> '[]'
                """,
                (conversation_id, *normalized_ids),
            ).fetchall()
        return [str(row["attachments_json"] or "[]") for row in rows]

    def delete_agent_conversation(self, conversation_id: str) -> bool:
        with self._connection_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT 1 FROM agent_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if exists is None:
                return False
            conn.execute("INSERT OR IGNORE INTO deleted_conversations VALUES(?)", (conversation_id,))
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "agent_task_runs" in tables:
                conn.execute("DELETE FROM agent_task_runs WHERE task_id IN (SELECT id FROM agent_tasks WHERE conversation_id=?)", (conversation_id,))
                conn.execute("DELETE FROM agent_tasks WHERE conversation_id=?", (conversation_id,))
            conn.execute("DELETE FROM agent_run_steps WHERE run_id IN (SELECT run_id FROM agent_runs WHERE conversation_id=?)", (conversation_id,))
            conn.execute("DELETE FROM tool_execution_receipts WHERE conversation_id=?", (conversation_id,))
            conn.execute("DELETE FROM agent_runs WHERE conversation_id=?", (conversation_id,))
            if "creation_jobs" in tables:
                conn.execute("DELETE FROM creation_jobs WHERE conversation_id=?", (conversation_id,))
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM pending_threads WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM companion_actions WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM chat_requests WHERE conversation_id = ?", (conversation_id,))
            conn.execute(
                "DELETE FROM memories WHERE type = 'conversation_summary' AND tags = ?",
                (conversation_id,),
            )
            conn.execute("DELETE FROM agent_conversations WHERE id = ?", (conversation_id,))
            return True

    def delete_conversation_messages(
        self,
        conversation_id: str,
        message_ids: list[int],
    ) -> dict[str, object]:
        normalized_ids = sorted({int(message_id) for message_id in message_ids if int(message_id) > 0})
        empty_result: dict[str, object] = {
            "message_ids": [],
            "messages": 0,
            "pending_threads": 0,
            "companion_actions": 0,
            "chat_requests": 0,
            "conversation_summaries": 0,
        }
        if not normalized_ids:
            return empty_result
        placeholders = ", ".join("?" for _ in normalized_ids)
        with self._connection_factory() as conn:
            rows = conn.execute(
                f"""
                SELECT id, request_id
                FROM messages
                WHERE conversation_id = ? AND id IN ({placeholders})
                """,
                (conversation_id, *normalized_ids),
            ).fetchall()
            existing_ids = sorted(int(row["id"]) for row in rows)
            if not existing_ids:
                return empty_result
            existing_placeholders = ", ".join("?" for _ in existing_ids)
            request_ids = sorted({str(row["request_id"] or "") for row in rows if str(row["request_id"] or "")})

            def count(table: str, where: str, parameters: tuple[object, ...]) -> int:
                row = conn.execute(
                    f"SELECT COUNT(*) AS total FROM {table} WHERE {where}",
                    parameters,
                ).fetchone()
                return int(row["total"] if row else 0)

            source_parameters = (conversation_id, *existing_ids)
            source_where = f"conversation_id = ? AND source_message_id IN ({existing_placeholders})"
            deleted: dict[str, object] = {
                "message_ids": existing_ids,
                "messages": len(existing_ids),
                "pending_threads": count("pending_threads", source_where, source_parameters),
                "companion_actions": count("companion_actions", source_where, source_parameters),
                "chat_requests": 0,
                "conversation_summaries": count(
                    "memories",
                    "type = 'conversation_summary' AND tags = ?",
                    (conversation_id,),
                ),
            }
            if request_ids:
                request_placeholders = ", ".join("?" for _ in request_ids)
                request_where = f"conversation_id = ? AND client_request_id IN ({request_placeholders})"
                request_parameters = (conversation_id, *request_ids)
                deleted["chat_requests"] = count("chat_requests", request_where, request_parameters)
                conn.execute(f"DELETE FROM chat_requests WHERE {request_where}", request_parameters)

            conn.execute(f"DELETE FROM pending_threads WHERE {source_where}", source_parameters)
            conn.execute(f"DELETE FROM companion_actions WHERE {source_where}", source_parameters)
            conn.execute(
                "DELETE FROM memories WHERE type = 'conversation_summary' AND tags = ?",
                (conversation_id,),
            )
            conn.execute(
                f"DELETE FROM messages WHERE conversation_id = ? AND id IN ({existing_placeholders})",
                (conversation_id, *existing_ids),
            )
        return deleted

    def get_messages_since(
        self,
        start_date: str,
        conversation_id: str = "default",
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        start_at = self._day_bounds_provider(start_date)[0]
        with self._connection_factory() as conn:
            return conn.execute(
                """
                SELECT id, role, content, source, conversation_id, created_at
                FROM messages
                WHERE conversation_id = ?
                  AND created_at >= ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (conversation_id, start_at, limit),
            ).fetchall()

    def get_last_message(self, conversation_id: str = "default", role: str | None = None) -> sqlite3.Row | None:
        where = "WHERE conversation_id = ?"
        params: tuple[object, ...] = (conversation_id,)
        if role:
            where += " AND role = ?"
            params = (conversation_id, role)
        with self._connection_factory() as conn:
            return conn.execute(
                f"""
                SELECT id, role, content, source, conversation_id, created_at
                FROM messages
                {where}
                ORDER BY id DESC
                LIMIT 1
                """,
                params,
            ).fetchone()


__all__ = ["ConversationRepository"]
