"""Message persistence and conversation query delegation."""
from __future__ import annotations

from typing import Any, Callable
from datetime import date as date_value
from datetime import datetime
import json
import sqlite3
from datetime import timedelta


class MessagesRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep__conversation_repository: Callable[..., Any],
                 dep_get_conn: Callable[..., Any],
                 dep_logical_date_for_datetime: Callable[..., Any],
                 dep_logical_day_bounds: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 dep_settings: Callable[..., Any],
                 dep_today_string: Callable[..., Any],
                 ) -> None:
        self._dep__conversation_repository = dep__conversation_repository
        self._dep_get_conn = dep_get_conn
        self._dep_logical_date_for_datetime = dep_logical_date_for_datetime
        self._dep_logical_day_bounds = dep_logical_day_bounds
        self._dep_now_iso = dep_now_iso
        self._dep_settings = dep_settings
        self._dep_today_string = dep_today_string

    def conversation_deleted(self, conversation_id: str, conn=None) -> bool:
        if conn is None:
            with self._dep_get_conn() as connection:
                return self.conversation_deleted(conversation_id, connection)
        return conn.execute("SELECT 1 FROM deleted_conversations WHERE id=?", (conversation_id,)).fetchone() is not None


    def assert_conversation_writable(self, conversation_id: str, conn=None) -> None:
        if self.conversation_deleted(conversation_id, conn):
            raise ValueError("对话已删除，不能继续执行或写入。")


    def save_message(self,
        role: str,
        content: str,
        source: str = "web",
        conversation_id: str = "default",
        request_id: str = "",
        model_id: str = "",
        provider_model: str = "",
        reasoning_level: str = "",
        prompt_tokens: int = 0,
        cached_prompt_tokens: int = 0,
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
        request_cost_yuan: float | None = None,
        request_cost_source: str = "",
        attachments_json: str = "[]",
        emotion: str = "",
        first_token_latency_ms: float | None = None,
        total_latency_ms: float | None = None,
        delivery_key: str = "",
        cached_tokens_reporting: str = "unknown",
        input_tokens_reported: bool = False,
        cache_write_tokens: int | None = None,
        expected_history_revision: int | None = None,
    ) -> int:
        with self._dep_get_conn() as conn:
            normalized_delivery_key = str(delivery_key or "")[:160]
            conn.execute("BEGIN IMMEDIATE")
            self.assert_conversation_writable(conversation_id, conn)
            if expected_history_revision is not None:
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='conversation_rewinds'").fetchone()
                revision = int(conn.execute('SELECT COALESCE(MAX(revision),0) FROM conversation_rewinds WHERE conversation_id=?', (conversation_id,)).fetchone()[0]) if exists else 0
                if revision != expected_history_revision:
                    raise ValueError('会话历史已变化，拒绝过期回复写入。')
            if role == 'assistant' and request_id and conn.execute("SELECT 1 FROM sqlite_master WHERE name='conversation_invalidated_requests'").fetchone():
                if conn.execute('SELECT 1 FROM conversation_invalidated_requests WHERE request_id=? AND conversation_id=?', (request_id, conversation_id)).fetchone():
                    raise ValueError('回复来源历史已回退，拒绝迟到写入。')
            if role == "assistant" and request_id and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_task_runs'").fetchone():
                stopped = conn.execute("SELECT 1 FROM agent_task_runs r JOIN agent_tasks t ON t.id=r.task_id WHERE r.request_id=? AND t.status IN ('paused','cancelled')", (request_id,)).fetchone()
                if stopped:
                    raise ValueError("任务已停止，不能写入迟到的回复。")
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO messages (
                        role, content, source, conversation_id, created_at, request_id,
                        model_id, provider_model, reasoning_level, prompt_tokens, cached_prompt_tokens,
                        completion_tokens, reasoning_tokens, request_cost_yuan,
                        request_cost_source, attachments_json, emotion,
                        first_token_latency_ms, total_latency_ms, delivery_key,
                        cached_tokens_reporting, input_tokens_reported, cache_write_tokens
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        role,
                        content,
                        source,
                        conversation_id,
                        self._dep_now_iso(),
                        request_id,
                        model_id,
                        provider_model,
                        reasoning_level,
                        max(0, int(prompt_tokens or 0)),
                        max(0, int(cached_prompt_tokens or 0)),
                        max(0, int(completion_tokens or 0)),
                        max(0, int(reasoning_tokens or 0)),
                        request_cost_yuan,
                        request_cost_source,
                        attachments_json,
                        str(emotion or "")[:40],
                        float(first_token_latency_ms) if first_token_latency_ms is not None else None,
                        float(total_latency_ms) if total_latency_ms is not None else None,
                        normalized_delivery_key,
                        cached_tokens_reporting if cached_tokens_reporting in {'reported', 'partial'} else 'unknown',
                        int(bool(input_tokens_reported)),
                        max(0, int(cache_write_tokens)) if cache_write_tokens is not None else None,
                    ),
                )
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                # 并发下相同 delivery_key 同时插入：唯一索引兜底，回查返回已有 id。
                if not normalized_delivery_key:
                    raise
                existing = conn.execute(
                    "SELECT id FROM messages WHERE delivery_key = ?",
                    (normalized_delivery_key,),
                ).fetchone()
                if existing is None:
                    raise
                return int(existing["id"])


    def claim_chat_request(self,
        client_request_id: str,
        request_hash: str,
        *,
        conversation_id: str,
        source: str,
    ) -> tuple[bool, sqlite3.Row]:
        clean_request_id = str(client_request_id or "").strip()[:80]
        clean_hash = str(request_hash or "").strip()[:128]
        if not clean_request_id or not clean_hash:
            raise ValueError("对话请求ID和请求摘要不能为空。")
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO chat_requests (
                    client_request_id, request_hash, conversation_id, source,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    clean_request_id,
                    clean_hash,
                    str(conversation_id or "")[:160],
                    str(source or "")[:40],
                    timestamp,
                    timestamp,
                ),
            )
            created = cursor.rowcount > 0
            row = conn.execute(
                "SELECT * FROM chat_requests WHERE client_request_id = ?",
                (clean_request_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("对话请求占位写入失败。")
        if str(row["request_hash"] or "") != clean_hash:
            raise ValueError("同一个对话请求ID不能用于不同内容。")
        return created, row


    def complete_chat_request(self, client_request_id: str, response: dict[str, object]) -> None:
        timestamp = self._dep_now_iso()
        serialized = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE chat_requests
                SET status = 'succeeded', response_json = ?, error_json = '',
                    http_status = 200, updated_at = ?
                WHERE client_request_id = ? AND status = 'pending'
                """,
                (serialized, timestamp, str(client_request_id or "").strip()[:80]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("对话请求结果无法写入：请求不在待完成状态。")


    def fail_chat_request(self,
        client_request_id: str,
        *,
        http_status: int,
        error: dict[str, object],
    ) -> None:
        timestamp = self._dep_now_iso()
        serialized = json.dumps(error, ensure_ascii=False, separators=(",", ":"))
        with self._dep_get_conn() as conn:
            conn.execute(
                """
                UPDATE chat_requests
                SET status = 'failed', response_json = '', error_json = ?,
                    http_status = ?, updated_at = ?
                WHERE client_request_id = ? AND status = 'pending'
                """,
                (
                    serialized,
                    max(400, min(599, int(http_status or 500))),
                    timestamp,
                    str(client_request_id or "").strip()[:80],
                ),
            )


    def get_desktop_chat_request(self, client_request_id: str, conversation_id: str):
        """Scope reconciliation to the selected desktop conversation and surface."""
        from contextlib import closing
        with closing(self._dep_get_conn()) as conn:
            return conn.execute(
                "SELECT * FROM chat_requests WHERE client_request_id=? AND conversation_id=? AND source IN ('desktop', 'desktop_pet')",
                (client_request_id,conversation_id),
            ).fetchone()

    def chat_request_payload(self, row: sqlite3.Row, field: str) -> dict[str, object]:
        try:
            value = json.loads(str(row[field] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


    def get_recent_messages(self, limit: int = 30, conversation_id: str = "default", before_id: int = 0) -> list[sqlite3.Row]:
        return self._dep__conversation_repository().get_recent_messages(limit, conversation_id, before_id)


    def get_total_message_token_usage(self) -> int:
        with self._dep_get_conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) AS total
                FROM messages
                WHERE role = 'assistant'
                """
            ).fetchone()
        return int(row["total"] or 0) if row is not None else 0


    def get_token_usage_summary(self, days: int = 30) -> dict[str, object]:
        day_count = max(1, min(365, int(days or 30)))
        current_date = date_value.fromisoformat(self._dep_today_string())
        first_date = current_date - timedelta(days=day_count - 1)
        first_started_at, _ = self._dep_logical_day_bounds(first_date.isoformat())

        def empty_usage(target_date: str = "") -> dict:
            return {
                "date": target_date,
                "prompt_tokens": 0,
                "cached_prompt_tokens": 0,
                "completion_tokens": 0,
                "reasoning_tokens": 0,
                "chat_tokens": 0,
                "screen_tokens": 0,
                "total_tokens": 0,
                'usage_samples': 0, 'input_reported_samples': 0, 'cache_reported_samples': 0,
                'partial_cache_samples': 0, 'reported_prompt_tokens': 0,
                'cache_reported_prompt_tokens': 0, 'cache_reported_cached_tokens': 0,
            }

        def add_coverage(usage, row):
            if row['request_cost_source'] == 'shared_request':
                return
            if not (row['model_id'] or row['provider_model'] or row['prompt_tokens'] or row['completion_tokens'] or row['input_tokens_reported']):
                return  # Local deterministic replies have no provider request.
            usage['usage_samples'] += 1
            measured = bool(row['input_tokens_reported'])
            if measured:
                usage['input_reported_samples'] += 1
                usage['reported_prompt_tokens'] += max(0, int(row['prompt_tokens'] or 0))
            if row['cached_tokens_reporting'] == 'partial':
                usage['partial_cache_samples'] += 1
            if measured and row['cached_tokens_reporting'] == 'reported':
                prompt, cache = max(0, int(row['prompt_tokens'] or 0)), max(0, int(row['cached_prompt_tokens'] or 0))
                if cache <= prompt:
                    usage['cache_reported_samples'] += 1
                    usage['cache_reported_prompt_tokens'] += prompt
                    usage['cache_reported_cached_tokens'] += cache

        def finalize(usage):
            samples, measured = usage['usage_samples'], usage['input_reported_samples']
            comparable, reported = usage['cache_reported_prompt_tokens'], usage['reported_prompt_tokens']
            usage['usage_reporting_coverage'] = measured / samples if samples else None
            usage['cache_reporting_coverage'] = usage['cache_reported_samples'] / measured if measured else None
            usage['cache_input_coverage'] = comparable / reported if reported else None
            usage['cache_hit_rate'] = usage['cache_reported_cached_tokens'] / comparable if comparable else None
            usage['cache_reporting'] = ('reported' if measured and usage['cache_reported_samples'] == samples else
                                        'partial' if usage['cache_reported_samples'] or usage['partial_cache_samples'] else 'unknown')
            return usage

        daily = {
            (first_date + timedelta(days=index)).isoformat(): empty_usage(
                (first_date + timedelta(days=index)).isoformat()
            )
            for index in range(day_count)
        }
        with self._dep_get_conn() as conn:
            message_rows = conn.execute(
                """
                SELECT created_at, prompt_tokens, cached_prompt_tokens,
                       completion_tokens, reasoning_tokens,cached_tokens_reporting,input_tokens_reported,
                       model_id,provider_model,request_cost_source
                FROM messages
                WHERE role = 'assistant'
                """,
            ).fetchall()
            screen_rows = conn.execute(
                """
                SELECT date, prompt_tokens, completion_tokens
                FROM screen_analysis_usage
                WHERE date >= ? AND date <= ?
                """,
                (first_date.isoformat(), current_date.isoformat()),
            ).fetchall()
            message_total = conn.execute(
                """
                SELECT COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(cached_prompt_tokens), 0) AS cached_prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens
                FROM messages
                WHERE role = 'assistant'
                """
            ).fetchone()
            screen_total = conn.execute(
                """
                SELECT COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens
                FROM screen_analysis_usage
                """
            ).fetchone()

        total, models = empty_usage(), {}
        for row in message_rows:
            add_coverage(total, row)
            try:
                logical_date = self._dep_logical_date_for_datetime(datetime.fromisoformat(str(row["created_at"])))
            except (TypeError, ValueError):
                continue
            usage = daily.get(logical_date)
            if usage is None:
                continue
            prompt_tokens = max(0, int(row["prompt_tokens"] or 0))
            completion_tokens = max(0, int(row["completion_tokens"] or 0))
            usage["prompt_tokens"] += prompt_tokens
            usage["cached_prompt_tokens"] += max(0, int(row["cached_prompt_tokens"] or 0))
            usage["completion_tokens"] += completion_tokens
            usage["reasoning_tokens"] += max(0, int(row["reasoning_tokens"] or 0))
            usage["chat_tokens"] += prompt_tokens + completion_tokens
            usage["total_tokens"] += prompt_tokens + completion_tokens
            add_coverage(usage, row)
            model = str(row['model_id'] or row['provider_model'] or 'unknown')
            grouped = models.setdefault(model, {**empty_usage(), 'model_id': model})
            for key, value in (('prompt_tokens', prompt_tokens), ('cached_prompt_tokens', max(0, int(row['cached_prompt_tokens'] or 0))),
                               ('completion_tokens', completion_tokens), ('reasoning_tokens', max(0, int(row['reasoning_tokens'] or 0))),
                               ('chat_tokens', prompt_tokens + completion_tokens), ('total_tokens', prompt_tokens + completion_tokens)):
                grouped[key] += value
            add_coverage(grouped, row)

        for row in screen_rows:
            usage = daily.get(str(row["date"] or ""))
            if usage is None:
                continue
            screen_tokens = max(0, int(row["prompt_tokens"] or 0)) + max(
                0, int(row["completion_tokens"] or 0)
            )
            usage["screen_tokens"] += screen_tokens
            usage["total_tokens"] += screen_tokens

        total["prompt_tokens"] = max(0, int(message_total["prompt_tokens"] or 0))
        total["cached_prompt_tokens"] = max(0, int(message_total["cached_prompt_tokens"] or 0))
        total["completion_tokens"] = max(0, int(message_total["completion_tokens"] or 0))
        total["reasoning_tokens"] = max(0, int(message_total["reasoning_tokens"] or 0))
        total["chat_tokens"] = total["prompt_tokens"] + total["completion_tokens"]
        total["screen_tokens"] = max(0, int(screen_total["prompt_tokens"] or 0)) + max(
            0, int(screen_total["completion_tokens"] or 0)
        )
        total["total_tokens"] = total["chat_tokens"] + total["screen_tokens"]

        return {
            "logical_day_boundary_hour": self._dep_settings().day_boundary_hour,
            "today": finalize(daily[current_date.isoformat()]),
            "total": finalize(total),
            "days": [finalize(item) for item in reversed(list(daily.values()))],
            'models': [finalize(item) for item in models.values()],
            'scope': 'saved_messages_and_screen_usage',
            'notice': '聊天统计仅含已保存的消息用量；缓存比例仅用同组明确上报输入和缓存的样本。屏幕用量另列。后台调用、失败及重试请看请求尝试账本，两者不能相加。',
        }


    def list_recent_private_user_messages(self, limit: int = 500) -> list[sqlite3.Row]:
        return self._dep__conversation_repository().list_recent_private_user_messages(limit)


    def get_message_by_id(self, message_id: int) -> sqlite3.Row | None:
        return self._dep__conversation_repository().get_message_by_id(message_id)

    def update_message_attachments(self, message_id: int, attachments_json: str) -> None:
        with self._dep_get_conn() as conn:
            conn.execute("UPDATE messages SET attachments_json=? WHERE id=?", (attachments_json, int(message_id)))


    def get_latest_message_id(self, role: str = "", conversation_id: str = "") -> int:
        return self._dep__conversation_repository().get_latest_message_id(role, conversation_id)


    def get_messages_after_id(self,
        after_id: int,
        role: str = "",
        limit: int = 50,
        conversation_id: str = "",
    ) -> list[sqlite3.Row]:
        return self._dep__conversation_repository().get_messages_after_id(after_id, role, limit, conversation_id)


    def create_agent_conversation(self, conversation_id: str, title: str = "新对话") -> sqlite3.Row:
        return self._dep__conversation_repository().create_agent_conversation(conversation_id, title)


    def get_agent_conversation(self, conversation_id: str) -> sqlite3.Row | None:
        return self._dep__conversation_repository().get_agent_conversation(conversation_id)


    def list_agent_conversations(self, limit: int = 40) -> list[sqlite3.Row]:
        return self._dep__conversation_repository().list_agent_conversations(limit)


    def touch_agent_conversation(self, conversation_id: str, first_message: str = "") -> None:
        self._dep__conversation_repository().touch_agent_conversation(conversation_id, first_message)


    def rename_agent_conversation(self, conversation_id: str, title: str) -> sqlite3.Row | None:
        return self._dep__conversation_repository().rename_agent_conversation(conversation_id, title)


    def list_conversation_attachment_records(self, conversation_id: str) -> list[str]:
        return self._dep__conversation_repository().list_conversation_attachment_records(conversation_id)


    def list_message_attachment_records(self, conversation_id: str, message_ids: list[int]) -> list[str]:
        return self._dep__conversation_repository().list_message_attachment_records(conversation_id, message_ids)


    def delete_agent_conversation(self, conversation_id: str) -> bool:
        return self._dep__conversation_repository().delete_agent_conversation(conversation_id)


    def delete_conversation_messages(self, conversation_id: str, message_ids: list[int]) -> dict[str, object]:
        return self._dep__conversation_repository().delete_conversation_messages(conversation_id, message_ids)


    def get_messages_since(self,
        start_date: str,
        conversation_id: str = "default",
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        return self._dep__conversation_repository().get_messages_since(start_date, conversation_id, limit)


    def get_last_message(self, conversation_id: str = "default", role: str | None = None) -> sqlite3.Row | None:
        return self._dep__conversation_repository().get_last_message(conversation_id, role)


    def get_today_messages(self, date: str | None = None) -> list[sqlite3.Row]:
        target_date = date or self._dep_today_string()
        start_at, end_at = self._dep_logical_day_bounds(target_date)
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, role, content, source, conversation_id, created_at
                FROM messages
                WHERE created_at >= ?
                  AND created_at < ?
                  AND conversation_id NOT LIKE 'qq_group_%'
                ORDER BY id ASC
                """,
                (start_at, end_at),
            ).fetchall()
