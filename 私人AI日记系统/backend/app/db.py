from __future__ import annotations

import json
import re
import sqlite3
from datetime import date as date_value
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import settings
from .repositories.conversation_repository import ConversationRepository


def _local_timezone():
    try:
        return ZoneInfo(settings.timezone)
    except Exception:
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(_local_timezone()).isoformat(timespec="seconds")


def logical_date_for_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        local = value.replace(tzinfo=_local_timezone())
    else:
        local = value.astimezone(_local_timezone())
    return (local - timedelta(hours=settings.day_boundary_hour)).date().isoformat()


def today_string(current: datetime | None = None) -> str:
    local = current or datetime.fromisoformat(now_iso())
    return logical_date_for_datetime(local)


def logical_day_bounds(date: str) -> tuple[str, str]:
    day = date_value.fromisoformat(date)
    start = datetime.combine(
        day,
        time(hour=settings.day_boundary_hour),
        tzinfo=_local_timezone(),
    )
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    settings.ensure_directories()
    conn = sqlite3.connect(settings.db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'web',
                conversation_id TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_created_at
                ON messages(created_at);

            CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
                ON messages(conversation_id, id);

            CREATE TABLE IF NOT EXISTS agent_conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '新对话',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS diaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                markdown_content TEXT NOT NULL,
                mood_tags TEXT NOT NULL DEFAULT '',
                daily_thirty_status TEXT NOT NULL DEFAULT 'unknown',
                confirmed_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                importance INTEGER NOT NULL DEFAULT 1,
                tags TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS structured_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                layer TEXT NOT NULL,
                category TEXT NOT NULL,
                memory_key TEXT NOT NULL,
                content TEXT NOT NULL,
                source_conversation_id TEXT NOT NULL DEFAULT '',
                source_message_id INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                superseded_by INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_structured_memories_active
                ON structured_memories(status, layer, updated_at);

            CREATE INDEX IF NOT EXISTS idx_structured_memories_key
                ON structured_memories(memory_key, category, status);

            CREATE TABLE IF NOT EXISTS daily_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                daily_thirty_status TEXT NOT NULL DEFAULT 'unknown',
                daily_thirty_reason TEXT NOT NULL DEFAULT '',
                mood TEXT NOT NULL DEFAULT '',
                key_events TEXT NOT NULL DEFAULT '',
                avoidance_signals TEXT NOT NULL DEFAULT '',
                next_min_action TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS diary_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'web',
                used_in_diary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_diary_materials_date
                ON diary_materials(date);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_diary_materials_date_content
                ON diary_materials(date, content);

            CREATE TABLE IF NOT EXISTS daily_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                markdown_content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS qq_proactive_states (
                user_id TEXT PRIMARY KEY,
                last_user_message_at TEXT NOT NULL DEFAULT '',
                next_prompt_at TEXT NOT NULL DEFAULT '',
                last_prompt_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS proactive_topic_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                topic_key TEXT NOT NULL,
                topic_kind TEXT NOT NULL,
                topic_text TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_proactive_topics_conversation
                ON proactive_topic_history(conversation_id, created_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS autonomy_policies (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                paused INTEGER NOT NULL DEFAULT 0,
                autonomy_level TEXT NOT NULL DEFAULT 'suggest',
                quiet_start_hour INTEGER NOT NULL DEFAULT 22,
                quiet_end_hour INTEGER NOT NULL DEFAULT 8,
                minimum_interval_minutes INTEGER NOT NULL DEFAULT 120,
                daily_behavior_limit INTEGER NOT NULL DEFAULT 3,
                daily_budget_yuan REAL NOT NULL DEFAULT 0.05,
                capability_overrides_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                source_kind TEXT NOT NULL DEFAULT 'manual',
                source_ref TEXT NOT NULL DEFAULT '',
                autonomy_level TEXT NOT NULL DEFAULT '',
                capabilities_json TEXT NOT NULL DEFAULT '[]',
                due_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_agent_goals_status_updated
                ON agent_goals(status, updated_at DESC, id DESC);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_goals_source
                ON agent_goals(source_kind, source_ref)
                WHERE source_ref != '';

            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                conversation_id TEXT NOT NULL DEFAULT '',
                goal_id INTEGER NOT NULL DEFAULT 0,
                capability TEXT NOT NULL DEFAULT '',
                risk_level TEXT NOT NULL DEFAULT 'read_only',
                payload_json TEXT NOT NULL DEFAULT '{}',
                relevance REAL NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0,
                urgency REAL NOT NULL DEFAULT 0,
                interruption_cost REAL NOT NULL DEFAULT 0,
                occurred_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                claim_token TEXT NOT NULL DEFAULT '',
                claimed_at TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                decision_reason TEXT NOT NULL DEFAULT '',
                processed_at TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_agent_events_pending
                ON agent_events(status, available_at, id);

            CREATE TABLE IF NOT EXISTS autonomy_behaviors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                behavior_key TEXT NOT NULL UNIQUE,
                event_id INTEGER NOT NULL DEFAULT 0,
                goal_id INTEGER NOT NULL DEFAULT 0,
                conversation_id TEXT NOT NULL DEFAULT '',
                behavior_type TEXT NOT NULL,
                capability TEXT NOT NULL DEFAULT '',
                risk_level TEXT NOT NULL DEFAULT 'read_only',
                permission_mode TEXT NOT NULL DEFAULT 'observe',
                status TEXT NOT NULL DEFAULT 'planned',
                reason TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                content TEXT NOT NULL DEFAULT '',
                destination TEXT NOT NULL DEFAULT 'app',
                delivery_status TEXT NOT NULL DEFAULT 'not_attempted',
                app_message_id INTEGER NOT NULL DEFAULT 0,
                qq_delivery_status TEXT NOT NULL DEFAULT 'not_attempted',
                request_id TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                provider_id TEXT NOT NULL DEFAULT '',
                provider_name TEXT NOT NULL DEFAULT '',
                provider_model TEXT NOT NULL DEFAULT '',
                provider_request_id TEXT NOT NULL DEFAULT '',
                reasoning_level TEXT NOT NULL DEFAULT '',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                cached_prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                first_token_latency_ms REAL,
                total_latency_ms REAL,
                cost_yuan REAL NOT NULL DEFAULT 0,
                cost_source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_autonomy_behaviors_status
                ON autonomy_behaviors(status, created_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS pending_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                follow_up_after TEXT NOT NULL DEFAULT '',
                source_message_id INTEGER NOT NULL DEFAULT 0,
                last_mentioned_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_pending_threads_conversation_status
                ON pending_threads(conversation_id, status, updated_at);

            CREATE TABLE IF NOT EXISTS follow_up_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL,
                conversation_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                adjustment TEXT NOT NULL DEFAULT '',
                next_follow_up_after TEXT NOT NULL DEFAULT '',
                source_message_id INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_follow_up_results_thread
                ON follow_up_results(thread_id, created_at DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_follow_up_results_conversation
                ON follow_up_results(conversation_id, created_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS weekly_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL UNIQUE,
                markdown_content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monthly_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL UNIQUE,
                markdown_content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS night_close_states (
                user_id TEXT PRIMARY KEY,
                prompted_date TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS companion_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tool_execution_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL DEFAULT '',
                tool_name TEXT NOT NULL,
                permission TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL DEFAULT '{}',
                result TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_tool_receipts_created
                ON tool_execution_receipts(created_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL DEFAULT '',
                conversation_id TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                source_message_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'planning',
                plan_json TEXT NOT NULL DEFAULT '{}',
                observation_json TEXT NOT NULL DEFAULT '[]',
                summary_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                reasoning_level TEXT NOT NULL DEFAULT '',
                model_calls INTEGER NOT NULL DEFAULT 0,
                tool_calls INTEGER NOT NULL DEFAULT 0,
                replan_count INTEGER NOT NULL DEFAULT 0,
                max_steps INTEGER NOT NULL DEFAULT 8,
                max_model_calls INTEGER NOT NULL DEFAULT 3,
                max_tool_calls INTEGER NOT NULL DEFAULT 6,
                deadline_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation_created
                ON agent_runs(conversation_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS agent_run_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                step_kind TEXT NOT NULL,
                tool_call_id TEXT NOT NULL DEFAULT '',
                tool_name TEXT NOT NULL DEFAULT '',
                permission TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                arguments_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL UNIQUE,
                action_id INTEGER NOT NULL DEFAULT 0,
                receipt_id INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, step_index)
            );

            CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run
                ON agent_run_steps(run_id, step_index);

            CREATE TABLE IF NOT EXISTS model_route_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT 'manual',
                task_type TEXT NOT NULL DEFAULT 'conversation',
                difficulty TEXT NOT NULL DEFAULT '',
                selected_model_id TEXT NOT NULL DEFAULT '',
                actual_model_id TEXT NOT NULL DEFAULT '',
                reasoning_level TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT '',
                first_token_latency_ms REAL,
                total_latency_ms REAL,
                request_cost_yuan REAL,
                request_cost_source TEXT NOT NULL DEFAULT '',
                candidates_json TEXT NOT NULL DEFAULT '[]',
                task_profile_json TEXT NOT NULL DEFAULT '{}',
                escalated_from_model_id TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_model_route_model_task_created
                ON model_route_observations(actual_model_id, task_type, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_model_route_request
                ON model_route_observations(request_id, id);

            CREATE TABLE IF NOT EXISTS screen_analysis_usage (
                date TEXT PRIMARY KEY,
                request_count INTEGER NOT NULL DEFAULT 0,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                priced_request_count INTEGER NOT NULL DEFAULT 0,
                unknown_cost_count INTEGER NOT NULL DEFAULT 0,
                total_cost_yuan REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS screen_analysis_costs (
                request_id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                request_kind TEXT NOT NULL DEFAULT 'analysis',
                model_id TEXT NOT NULL DEFAULT '',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost_yuan REAL,
                confirmed_cost_yuan REAL,
                cost_source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'unconfirmed',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_screen_analysis_costs_date
                ON screen_analysis_costs(date, created_at);

            CREATE TABLE IF NOT EXISTS game_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                vision_model TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS screen_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id INTEGER NOT NULL DEFAULT 0,
                session_id INTEGER NOT NULL DEFAULT 0,
                frame_id INTEGER NOT NULL DEFAULT 0,
                event_type TEXT NOT NULL DEFAULT 'scene_change',
                event_summary TEXT NOT NULL DEFAULT '',
                importance REAL NOT NULL DEFAULT 0,
                should_speak INTEGER NOT NULL DEFAULT 0,
                emotion TEXT NOT NULL DEFAULT 'neutral',
                change_percent REAL NOT NULL DEFAULT 0,
                model_id TEXT NOT NULL DEFAULT '',
                request_cost_yuan REAL,
                occurred_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_screen_events_session_created
                ON screen_events(session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL DEFAULT 0,
                frame_id INTEGER NOT NULL DEFAULT 0,
                game_name TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT 'unknown',
                summary TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                details_json TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'vision',
                model_id TEXT NOT NULL DEFAULT '',
                request_cost_yuan REAL,
                occurred_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_observations_session_created
                ON observations(session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS game_session_states (
                session_id INTEGER PRIMARY KEY,
                game_name TEXT NOT NULL DEFAULT '',
                state_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS companion_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                screen_event_id INTEGER NOT NULL DEFAULT 0,
                request_id TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                emotion TEXT NOT NULL DEFAULT 'neutral',
                trigger_reason TEXT NOT NULL DEFAULT '',
                voice_status TEXT NOT NULL DEFAULT 'pending',
                model_id TEXT NOT NULL DEFAULT '',
                request_cost_yuan REAL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cost_reconciliation_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_request_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL DEFAULT '',
                provider_request_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                base_url TEXT NOT NULL,
                estimated_cost_yuan REAL,
                estimated_cost_source TEXT NOT NULL DEFAULT '',
                resolved_cost_yuan REAL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(local_request_id, provider_request_id)
            );

            CREATE INDEX IF NOT EXISTS idx_cost_reconciliation_due
                ON cost_reconciliation_jobs(status, next_attempt_at, id);

            CREATE TABLE IF NOT EXISTS chat_requests (
                client_request_id TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                conversation_id TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                response_json TEXT NOT NULL DEFAULT '',
                error_json TEXT NOT NULL DEFAULT '',
                http_status INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chat_requests_status_updated
                ON chat_requests(status, updated_at);
            """
        )
        _ensure_column(conn, "diaries", "confirmed_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "daily_states", "daily_thirty_reason", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "daily_states", "mood_score", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "messages", "request_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "messages", "model_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "messages", "provider_model", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "messages", "reasoning_level", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "messages", "prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "messages", "cached_prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "messages", "completion_tokens", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "messages", "reasoning_tokens", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "messages", "request_cost_yuan", "REAL")
        _ensure_column(conn, "messages", "request_cost_source", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "messages", "attachments_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "messages", "emotion", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "messages", "first_token_latency_ms", "REAL")
        _ensure_column(conn, "messages", "total_latency_ms", "REAL")
        _ensure_column(conn, "messages", "delivery_key", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "screen_events", "observation_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "companion_reactions", "model_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "companion_reactions", "request_cost_yuan", "REAL")
        _ensure_column(conn, "companion_actions", "source_message_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "companion_actions", "requires_confirmation", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "companion_actions", "approved_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "companion_actions", "finished_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "companion_actions", "request_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "companion_actions", "trace_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "companion_actions", "agent_run_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "companion_actions", "agent_step_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "companion_actions", "idempotency_key", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "tool_execution_receipts", "request_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "tool_execution_receipts", "trace_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "tool_execution_receipts", "agent_run_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "tool_execution_receipts", "agent_step_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "tool_execution_receipts", "action_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "tool_execution_receipts", "idempotency_key", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "autonomy_behaviors", "model_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "autonomy_behaviors", "provider_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "autonomy_behaviors", "provider_name", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "autonomy_behaviors", "provider_model", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "autonomy_behaviors", "provider_request_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "autonomy_behaviors", "reasoning_level", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "autonomy_behaviors", "prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "autonomy_behaviors", "cached_prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "autonomy_behaviors", "completion_tokens", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "autonomy_behaviors", "reasoning_tokens", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "autonomy_behaviors", "first_token_latency_ms", "REAL")
        _ensure_column(conn, "autonomy_behaviors", "total_latency_ms", "REAL")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_companion_actions_idempotency
            ON companion_actions(idempotency_key)
            WHERE idempotency_key != ''
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_receipts_idempotency
            ON tool_execution_receipts(idempotency_key)
            WHERE idempotency_key != ''
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_delivery_key
            ON messages(delivery_key)
            WHERE delivery_key != ''
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO autonomy_policies (
                id, paused, autonomy_level, quiet_start_hour, quiet_end_hour,
                minimum_interval_minutes, daily_behavior_limit, daily_budget_yuan,
                capability_overrides_json, updated_at
            ) VALUES (1, 0, 'suggest', 22, 8, 120, 3, 0.05, '{}', ?)
            """,
            (now_iso(),),
        )
        _ensure_structured_memory_fts(conn)
        # Earlier desktop-pet builds wrote to the primary conversation. Keep
        # those records in the new isolated pet conversation on first startup.
        conn.execute(
            """
            UPDATE messages
            SET conversation_id = 'desktop_pet'
            WHERE source = 'desktop_pet' AND conversation_id != 'desktop_pet'
            """
        )
        conn.execute(
            """
            UPDATE messages
            SET request_cost_yuan = 0, request_cost_source = 'local_fallback'
            WHERE role = 'assistant' AND source = 'qq'
              AND request_id = '' AND request_cost_source = ''
              AND (
                content LIKE '昨天的日记回顾写好了。%'
                OR content LIKE '上周（%周复盘写好了%'
              )
            """
        )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _memory_search_text(*values: object) -> str:
    text = " ".join(str(value or "") for value in values).strip().casefold()
    if not text:
        return ""
    tokens: list[str] = []
    for run in re.findall(r"[a-z0-9_]+", text):
        tokens.append(run)
    compact_cjk = "".join(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
    tokens.extend(compact_cjk[index : index + 2] for index in range(max(0, len(compact_cjk) - 1)))
    return " ".join(dict.fromkeys(token for token in tokens if token))


def _ensure_structured_memory_fts(conn: sqlite3.Connection) -> bool:
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
                    _memory_search_text(row["memory_key"], row["category"], row["content"]),
                ),
            )
        return True
    except sqlite3.OperationalError:
        return False


def _upsert_structured_memory_fts(
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
            (str(memory_id), _memory_search_text(memory_key, category, content)),
        )
    except sqlite3.OperationalError:
        # FTS5 is optional; memory_service has a deterministic fallback.
        return


def refresh_manual_memories(manuals: list[dict[str, object]]) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
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


def get_latest_memory(memory_type: str, tags: str = "") -> sqlite3.Row | None:
    with get_conn() as conn:
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


def replace_memory(memory_type: str, content: str, importance: int = 3, tags: str = "") -> None:
    timestamp = now_iso()
    with get_conn() as conn:
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


def delete_memory(memory_type: str, tags: str = "") -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM memories WHERE type = ? AND tags = ?",
            (memory_type, tags),
        )
        return cursor.rowcount > 0


def save_structured_memory(
    layer: str,
    category: str,
    memory_key: str,
    content: str,
    source_conversation_id: str = "",
    source_message_id: int = 0,
    confidence: float = 0.0,
) -> tuple[int, str]:
    timestamp = now_iso()
    with get_conn() as conn:
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
                    confidence = MAX(confidence, ?), last_seen_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    layer,
                    source_conversation_id,
                    int(source_message_id or 0),
                    float(confidence),
                    timestamp,
                    timestamp,
                    int(current["id"]),
                ),
            )
            _upsert_structured_memory_fts(conn, int(current["id"]), memory_key, category, content)
            return int(current["id"]), "reinforced"

        cursor = conn.execute(
            """
            INSERT INTO structured_memories (
                layer, category, memory_key, content, source_conversation_id,
                source_message_id, confidence, status, superseded_by,
                last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)
            """,
            (
                layer,
                category,
                memory_key,
                content,
                source_conversation_id,
                int(source_message_id or 0),
                float(confidence),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        memory_id = int(cursor.lastrowid)
        _upsert_structured_memory_fts(conn, memory_id, memory_key, category, content)
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


def save_structured_memory_candidate(
    layer: str,
    category: str,
    memory_key: str,
    content: str,
    source_conversation_id: str = "",
    source_message_id: int = 0,
    confidence: float = 0.0,
) -> int:
    timestamp = now_iso()
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO structured_memories (
                layer, category, memory_key, content, source_conversation_id,
                source_message_id, confidence, status, superseded_by,
                last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', 0, ?, ?, ?)
            """,
            (
                layer,
                category,
                memory_key,
                content,
                source_conversation_id,
                int(source_message_id or 0),
                float(confidence),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        memory_id = int(cursor.lastrowid)
        _upsert_structured_memory_fts(conn, memory_id, memory_key, category, content)
        return memory_id


def list_structured_memories(
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
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT id, layer, category, memory_key, content, source_conversation_id,
                   source_message_id, confidence, status, superseded_by,
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


def get_structured_memory(memory_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, layer, category, memory_key, content, source_conversation_id,
                   source_message_id, confidence, status, superseded_by,
                   last_seen_at, created_at, updated_at
            FROM structured_memories
            WHERE id = ?
            """,
            (int(memory_id),),
        ).fetchone()


def restore_structured_memory(memory_id: int) -> bool:
    """Restore a superseded or archived version as the only active value for its slot."""
    timestamp = now_iso()
    with get_conn() as conn:
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


def search_structured_memories(
    query: str,
    *,
    status: str = "active",
    limit: int = 100,
) -> list[sqlite3.Row]:
    """Search memory with FTS5, falling back to LIKE when FTS5 is unavailable."""
    clean_query = str(query or "").strip().casefold()
    if not clean_query:
        return list_structured_memories(status=status, limit=limit)
    terms = _memory_search_text(clean_query).split()
    match_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:32])
    with get_conn() as conn:
        try:
            rows = conn.execute(
                """
                SELECT memories.id, memories.layer, memories.category, memories.memory_key,
                       memories.content, memories.source_conversation_id,
                       memories.source_message_id, memories.confidence, memories.status,
                       memories.superseded_by, memories.last_seen_at, memories.created_at,
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
                SELECT id, layer, category, memory_key, content, source_conversation_id,
                       source_message_id, confidence, status, superseded_by,
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


def set_structured_memory_status(memory_id: int, status: str) -> bool:
    if status not in {"active", "archived", "sleeping"}:
        raise ValueError("记忆状态不受支持。")
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE structured_memories SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), int(memory_id)),
        )
        return cursor.rowcount > 0


def sleep_stale_structured_memories(before_timestamp: str) -> int:
    """Move stale recent-state memories out of the active prompt context."""
    with get_conn() as conn:
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


def mark_structured_memories_seen(memory_ids: list[int]) -> int:
    clean_ids = sorted({int(memory_id) for memory_id in memory_ids if int(memory_id) > 0})
    if not clean_ids:
        return 0
    placeholders = ", ".join("?" for _ in clean_ids)
    with get_conn() as conn:
        cursor = conn.execute(
            f"""
            UPDATE structured_memories
            SET last_seen_at = ?
            WHERE id IN ({placeholders}) AND status = 'active'
            """,
            (now_iso(), *clean_ids),
        )
        return max(0, int(cursor.rowcount))


def confirm_structured_memory_candidate(memory_id: int) -> bool:
    timestamp = now_iso()
    with get_conn() as conn:
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


def archive_structured_memory(memory_id: int) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE structured_memories
            SET status = 'archived', updated_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (now_iso(), int(memory_id)),
        )
        return cursor.rowcount > 0


def save_message(
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
) -> int:
    with get_conn() as conn:
        normalized_delivery_key = str(delivery_key or "")[:160]
        try:
            cursor = conn.execute(
                """
                INSERT INTO messages (
                    role, content, source, conversation_id, created_at, request_id,
                    model_id, provider_model, reasoning_level, prompt_tokens, cached_prompt_tokens,
                    completion_tokens, reasoning_tokens, request_cost_yuan,
                    request_cost_source, attachments_json, emotion,
                    first_token_latency_ms, total_latency_ms, delivery_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    role,
                    content,
                    source,
                    conversation_id,
                    now_iso(),
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


def claim_chat_request(
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
    timestamp = now_iso()
    with get_conn() as conn:
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


def complete_chat_request(client_request_id: str, response: dict[str, object]) -> None:
    timestamp = now_iso()
    serialized = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    with get_conn() as conn:
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


def fail_chat_request(
    client_request_id: str,
    *,
    http_status: int,
    error: dict[str, object],
) -> None:
    timestamp = now_iso()
    serialized = json.dumps(error, ensure_ascii=False, separators=(",", ":"))
    with get_conn() as conn:
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


def chat_request_payload(row: sqlite3.Row, field: str) -> dict[str, object]:
    try:
        value = json.loads(str(row[field] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def enqueue_cost_reconciliation(
    *,
    local_request_id: str,
    conversation_id: str,
    provider_request_id: str,
    profile_id: str,
    base_url: str,
    estimated_cost_yuan: float | None,
    estimated_cost_source: str,
) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO cost_reconciliation_jobs (
                local_request_id, conversation_id, provider_request_id,
                profile_id, base_url, estimated_cost_yuan,
                estimated_cost_source, next_attempt_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(local_request_id or "")[:80],
                str(conversation_id or "")[:160],
                str(provider_request_id or "")[:160],
                str(profile_id or "")[:160],
                str(base_url or "")[:500],
                estimated_cost_yuan,
                str(estimated_cost_source or "")[:60],
                timestamp,
                timestamp,
                timestamp,
            ),
        )
    refresh_reconciled_message_cost(local_request_id)


def list_due_cost_reconciliation_jobs(limit: int = 1) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT * FROM cost_reconciliation_jobs
            WHERE status = 'pending' AND next_attempt_at <= ?
            ORDER BY next_attempt_at ASC, id ASC
            LIMIT ?
            """,
            (now_iso(), max(1, min(int(limit), 20))),
        ).fetchall()


def resolve_cost_reconciliation_job(job_id: int, cost_yuan: float) -> str:
    timestamp = now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT local_request_id FROM cost_reconciliation_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        if row is None:
            return ""
        conn.execute(
            """
            UPDATE cost_reconciliation_jobs
            SET resolved_cost_yuan = ?, status = 'resolved', attempts = attempts + 1,
                last_error = '', updated_at = ?
            WHERE id = ?
            """,
            (max(0.0, float(cost_yuan)), timestamp, int(job_id)),
        )
        local_request_id = str(row["local_request_id"])
    refresh_reconciled_message_cost(local_request_id)
    return local_request_id


def retry_cost_reconciliation_job(
    job_id: int,
    *,
    delay_seconds: int,
    last_error: str,
    max_attempts: int = 6,
) -> str:
    timestamp = datetime.fromisoformat(now_iso())
    with get_conn() as conn:
        row = conn.execute(
            "SELECT local_request_id, attempts FROM cost_reconciliation_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        if row is None:
            return ""
        attempts = int(row["attempts"] or 0) + 1
        status = "exhausted" if attempts >= max_attempts else "pending"
        next_attempt = (timestamp + timedelta(seconds=max(1, int(delay_seconds)))).isoformat(
            timespec="seconds"
        )
        conn.execute(
            """
            UPDATE cost_reconciliation_jobs
            SET attempts = ?, status = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (attempts, status, next_attempt, str(last_error or "")[:300], now_iso(), int(job_id)),
        )
        local_request_id = str(row["local_request_id"])
    refresh_reconciled_message_cost(local_request_id)
    return local_request_id


def refresh_reconciled_message_cost(local_request_id: str) -> None:
    if not local_request_id:
        return
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT estimated_cost_yuan, estimated_cost_source,
                   resolved_cost_yuan, status
            FROM cost_reconciliation_jobs
            WHERE local_request_id = ?
            ORDER BY id ASC
            """,
            (local_request_id,),
        ).fetchall()
        if not rows:
            return
        total = 0.0
        has_cost = False
        pending = False
        resolved_count = 0
        estimate_sources: list[str] = []
        for row in rows:
            resolved = row["resolved_cost_yuan"]
            estimated = row["estimated_cost_yuan"]
            value = resolved if resolved is not None else estimated
            if value is not None:
                total += max(0.0, float(value))
                has_cost = True
            if resolved is not None:
                resolved_count += 1
            if str(row["status"]) == "pending":
                pending = True
            source = str(row["estimated_cost_source"] or "")
            if source:
                estimate_sources.append(source)
        if pending:
            source = "provider_reconciliation_pending"
        elif resolved_count == len(rows):
            source = "provider_reported"
        elif resolved_count:
            source = "provider_partial"
        else:
            source = estimate_sources[0] if estimate_sources else "unavailable"
        target = conn.execute(
            """
            SELECT id FROM messages
            WHERE request_id = ? AND role = 'assistant'
            ORDER BY id ASC LIMIT 1
            """,
            (local_request_id,),
        ).fetchone()
        if target is not None:
            conn.execute(
                """
                UPDATE messages
                SET request_cost_yuan = ?, request_cost_source = ?
                WHERE id = ?
                """,
                (total if has_cost else None, source, int(target["id"])),
            )
        screen_cost = conn.execute(
            "SELECT request_id FROM screen_analysis_costs WHERE request_id = ?",
            (local_request_id,),
        ).fetchone()
        if screen_cost is not None:
            fully_resolved = resolved_count == len(rows)
            conn.execute(
                """
                UPDATE screen_analysis_costs
                SET confirmed_cost_yuan = ?, cost_source = ?, status = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (
                    total if has_cost and fully_resolved else None,
                    source,
                    "confirmed" if fully_resolved else "pending" if pending else "unconfirmed",
                    now_iso(),
                    local_request_id,
                ),
            )


def record_screen_analysis_usage(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_yuan: float | None = None,
    request_id: str = "",
    request_kind: str = "analysis",
    model_id: str = "",
    cost_source: str = "",
    date: str | None = None,
) -> None:
    target_date = date or today_string()
    timestamp = now_iso()
    priced = cost_yuan is not None
    normalized_cost = max(0.0, float(cost_yuan or 0.0))
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO screen_analysis_usage (
                date, request_count, prompt_tokens, completion_tokens,
                priced_request_count, unknown_cost_count, total_cost_yuan, updated_at
            )
            VALUES (?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                request_count = screen_analysis_usage.request_count + 1,
                prompt_tokens = screen_analysis_usage.prompt_tokens + excluded.prompt_tokens,
                completion_tokens = screen_analysis_usage.completion_tokens + excluded.completion_tokens,
                priced_request_count = screen_analysis_usage.priced_request_count + excluded.priced_request_count,
                unknown_cost_count = screen_analysis_usage.unknown_cost_count + excluded.unknown_cost_count,
                total_cost_yuan = screen_analysis_usage.total_cost_yuan + excluded.total_cost_yuan,
                updated_at = excluded.updated_at
            """,
            (
                target_date,
                max(0, int(prompt_tokens or 0)),
                max(0, int(completion_tokens or 0)),
                1 if priced else 0,
                0 if priced else 1,
                normalized_cost,
                timestamp,
            ),
        )
        if request_id:
            normalized_source = str(cost_source or "")[:60]
            is_confirmed = normalized_source in {"provider_reported", "local", "local_fallback"}
            status = (
                "confirmed"
                if is_confirmed
                else "pending"
                if normalized_source == "provider_reconciliation_pending"
                else "unconfirmed"
            )
            conn.execute(
                """
                INSERT INTO screen_analysis_costs (
                    request_id, date, request_kind, model_id,
                    prompt_tokens, completion_tokens, estimated_cost_yuan,
                    confirmed_cost_yuan, cost_source, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    estimated_cost_yuan = excluded.estimated_cost_yuan,
                    confirmed_cost_yuan = excluded.confirmed_cost_yuan,
                    cost_source = excluded.cost_source,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    request_id,
                    target_date,
                    str(request_kind or "analysis")[:30],
                    str(model_id or "")[:200],
                    max(0, int(prompt_tokens or 0)),
                    max(0, int(completion_tokens or 0)),
                    normalized_cost if priced else None,
                    normalized_cost if is_confirmed and priced else None,
                    normalized_source,
                    status,
                    timestamp,
                    timestamp,
                ),
            )


def get_screen_analysis_usage(date: str | None = None) -> dict[str, int | float | str]:
    target_date = date or today_string()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT date, request_count, prompt_tokens, completion_tokens,
                   priced_request_count, unknown_cost_count, total_cost_yuan, updated_at
            FROM screen_analysis_usage
            WHERE date = ?
            """,
            (target_date,),
        ).fetchone()
        cost_row = conn.execute(
            """
            SELECT COUNT(*) AS tracked_request_count,
                   SUM(CASE WHEN status = 'confirmed' AND cost_source = 'provider_reported' THEN 1 ELSE 0 END)
                       AS confirmed_request_count,
                   SUM(CASE WHEN status = 'confirmed' AND cost_source = 'provider_reported'
                            THEN COALESCE(confirmed_cost_yuan, 0) ELSE 0 END)
                       AS confirmed_cost_yuan,
                   SUM(CASE WHEN status IN ('pending', 'unconfirmed') THEN 1 ELSE 0 END)
                       AS pending_request_count,
                   SUM(CASE
                           WHEN status = 'confirmed' THEN COALESCE(confirmed_cost_yuan, 0)
                           WHEN status IN ('pending', 'unconfirmed') THEN COALESCE(estimated_cost_yuan, 0)
                           ELSE 0
                       END) AS budget_cost_yuan
            FROM screen_analysis_costs
            WHERE date = ?
            """,
            (target_date,),
        ).fetchone()
    tracked_request_count = int(cost_row["tracked_request_count"] or 0)
    confirmed_request_count = int(cost_row["confirmed_request_count"] or 0)
    confirmed_cost_yuan = float(cost_row["confirmed_cost_yuan"] or 0.0)
    pending_request_count = int(cost_row["pending_request_count"] or 0)
    budget_cost_yuan = float(cost_row["budget_cost_yuan"] or 0.0)
    if row is None:
        return {
            "date": target_date,
            "request_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "priced_request_count": 0,
            "unknown_cost_count": 0,
            "total_cost_yuan": 0.0,
            "confirmed_request_count": confirmed_request_count,
            "confirmed_cost_yuan": confirmed_cost_yuan,
            "pending_request_count": pending_request_count,
            "legacy_unconfirmed_count": 0,
            "budget_cost_yuan": budget_cost_yuan,
            "updated_at": "",
        }
    request_count = int(row["request_count"] or 0)
    legacy_unconfirmed_count = max(0, request_count - tracked_request_count)
    return {
        "date": str(row["date"]),
        "request_count": request_count,
        "prompt_tokens": int(row["prompt_tokens"] or 0),
        "completion_tokens": int(row["completion_tokens"] or 0),
        "priced_request_count": int(row["priced_request_count"] or 0),
        "unknown_cost_count": int(row["unknown_cost_count"] or 0),
        "total_cost_yuan": float(row["total_cost_yuan"] or 0.0),
        "confirmed_request_count": confirmed_request_count,
        "confirmed_cost_yuan": confirmed_cost_yuan,
        "pending_request_count": pending_request_count,
        "legacy_unconfirmed_count": legacy_unconfirmed_count,
        "budget_cost_yuan": budget_cost_yuan,
        "updated_at": str(row["updated_at"] or ""),
    }


def get_screen_analysis_costs_since(started_at: str) -> dict[str, int | float]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS tracked_request_count,
                   SUM(CASE WHEN status = 'confirmed' AND cost_source = 'provider_reported' THEN 1 ELSE 0 END)
                       AS confirmed_request_count,
                   SUM(CASE WHEN status = 'confirmed' AND cost_source = 'provider_reported'
                            THEN COALESCE(confirmed_cost_yuan, 0) ELSE 0 END)
                       AS confirmed_cost_yuan,
                   SUM(CASE WHEN status IN ('pending', 'unconfirmed') THEN 1 ELSE 0 END)
                       AS pending_request_count
            FROM screen_analysis_costs
            WHERE created_at >= ?
            """,
            (str(started_at or ""),),
        ).fetchone()
    return {
        "tracked_request_count": int(row["tracked_request_count"] or 0),
        "confirmed_request_count": int(row["confirmed_request_count"] or 0),
        "confirmed_cost_yuan": float(row["confirmed_cost_yuan"] or 0.0),
        "pending_request_count": int(row["pending_request_count"] or 0),
    }


def _screen_session_is_recent(last_activity_at: str, current_at: str, *, minutes: int = 30) -> bool:
    try:
        last_activity = datetime.fromisoformat(last_activity_at)
        current = datetime.fromisoformat(current_at)
    except (TypeError, ValueError):
        return False
    local_timezone = _local_timezone()
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=local_timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=local_timezone)
    last_activity = last_activity.astimezone(local_timezone)
    current = current.astimezone(local_timezone)
    return timedelta(0) <= current - last_activity <= timedelta(minutes=max(1, minutes))


def start_screen_session(mode: str, title: str, vision_model: str = "") -> int:
    timestamp = now_iso()
    normalized_mode = "window" if mode == "window" else "screen"
    normalized_title = str(title or "")[:300]
    with get_conn() as conn:
        current = conn.execute(
            """
            SELECT sessions.id, sessions.mode, sessions.title,
                   COALESCE(MAX(observations.occurred_at), sessions.started_at) AS last_activity_at
            FROM game_sessions AS sessions
            LEFT JOIN observations ON observations.session_id = sessions.id
            WHERE status = 'active'
            GROUP BY sessions.id
            ORDER BY sessions.id DESC
            LIMIT 1
            """
        ).fetchone()
        if (
            current is not None
            and current["mode"] == normalized_mode
            and current["title"] == normalized_title
            and _screen_session_is_recent(str(current["last_activity_at"] or ""), timestamp)
        ):
            if vision_model:
                conn.execute(
                    "UPDATE game_sessions SET vision_model = ? WHERE id = ?",
                    (vision_model, int(current["id"])),
                )
            return int(current["id"])

        previous = conn.execute(
            """
            SELECT sessions.id,
                   COALESCE(MAX(observations.occurred_at), sessions.started_at) AS last_activity_at,
                   states.game_name, states.state_json
            FROM game_sessions AS sessions
            LEFT JOIN observations ON observations.session_id = sessions.id
            LEFT JOIN game_session_states AS states ON states.session_id = sessions.id
            WHERE sessions.mode = ? AND sessions.title = ? AND states.session_id IS NOT NULL
            GROUP BY sessions.id
            ORDER BY sessions.id DESC
            LIMIT 1
            """,
            (normalized_mode, normalized_title),
        ).fetchone()
        conn.execute(
            "UPDATE game_sessions SET status = 'ended', ended_at = ? WHERE status = 'active'",
            (timestamp,),
        )
        cursor = conn.execute(
            """
            INSERT INTO game_sessions (mode, title, vision_model, status, started_at, ended_at)
            VALUES (?, ?, ?, 'active', ?, '')
            """,
            (normalized_mode, normalized_title, vision_model[:120], timestamp),
        )
        session_id = int(cursor.lastrowid)
        if (
            previous is not None
            and _screen_session_is_recent(
                str(previous["last_activity_at"] or ""),
                timestamp,
                minutes=12 * 60,
            )
        ):
            conn.execute(
                """
                INSERT INTO game_session_states (session_id, game_name, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(previous["game_name"] or "")[:200],
                    str(previous["state_json"] or "{}")[:12000],
                    timestamp,
                ),
            )
        return session_id


def end_screen_sessions() -> None:
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            "UPDATE game_sessions SET status = 'ended', ended_at = ? WHERE status = 'active'",
            (timestamp,),
        )


def cleanup_screen_observation_history(
    *,
    retention_days: int = 30,
    max_rows_per_table: int = 20000,
    current_at: str | None = None,
) -> dict[str, int]:
    """Trim disposable screen-observation telemetry without touching chats or diaries."""
    current = datetime.fromisoformat(current_at or now_iso())
    if current.tzinfo is None:
        current = current.replace(tzinfo=_local_timezone())
    cutoff = (current - timedelta(days=max(1, int(retention_days)))).isoformat(timespec="seconds")
    row_limit = max(1000, int(max_rows_per_table))
    deleted: dict[str, int] = {}

    with get_conn() as conn:
        cursor = conn.execute(
            """
            DELETE FROM companion_reactions
            WHERE created_at < ?
               OR id NOT IN (
                    SELECT id FROM companion_reactions ORDER BY id DESC LIMIT ?
               )
            """,
            (cutoff, row_limit),
        )
        deleted["companion_reactions"] = max(0, int(cursor.rowcount))

        cursor = conn.execute(
            """
            DELETE FROM screen_events
            WHERE occurred_at < ?
               OR id NOT IN (
                    SELECT id FROM screen_events ORDER BY id DESC LIMIT ?
               )
            """,
            (cutoff, row_limit),
        )
        deleted["screen_events"] = max(0, int(cursor.rowcount))

        cursor = conn.execute(
            """
            DELETE FROM observations
            WHERE occurred_at < ?
               OR id NOT IN (
                    SELECT id FROM observations ORDER BY id DESC LIMIT ?
               )
            """,
            (cutoff, row_limit),
        )
        deleted["observations"] = max(0, int(cursor.rowcount))

        cursor = conn.execute(
            """
            DELETE FROM game_session_states
            WHERE session_id IN (
                SELECT sessions.id
                FROM game_sessions AS sessions
                WHERE sessions.status = 'ended'
                  AND sessions.ended_at != ''
                  AND sessions.ended_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM observations WHERE observations.session_id = sessions.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM screen_events WHERE screen_events.session_id = sessions.id
                  )
            )
            """,
            (cutoff,),
        )
        deleted["game_session_states"] = max(0, int(cursor.rowcount))

        cursor = conn.execute(
            """
            DELETE FROM game_sessions
            WHERE status = 'ended'
              AND ended_at != ''
              AND ended_at < ?
              AND NOT EXISTS (
                  SELECT 1 FROM observations WHERE observations.session_id = game_sessions.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM screen_events WHERE screen_events.session_id = game_sessions.id
              )
            """,
            (cutoff,),
        )
        deleted["game_sessions"] = max(0, int(cursor.rowcount))
    return deleted


def save_screen_event(
    *,
    session_id: int,
    frame_id: int,
    event_type: str,
    event_summary: str,
    importance: float,
    should_speak: bool,
    emotion: str,
    change_percent: float,
    model_id: str,
    request_cost_yuan: float | None,
    occurred_at: str,
    observation_id: int = 0,
    conversation_id: str = "",
) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO screen_events (
                observation_id, session_id, frame_id, event_type, event_summary, importance,
                should_speak, emotion, change_percent, model_id,
                request_cost_yuan, occurred_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(observation_id or 0),
                int(session_id or 0),
                int(frame_id or 0),
                str(event_type or "scene_change")[:80],
                str(event_summary or "")[:500],
                max(0.0, min(1.0, float(importance or 0))),
                1 if should_speak else 0,
                str(emotion or "neutral")[:40],
                max(0.0, float(change_percent or 0)),
                str(model_id or "")[:120],
                request_cost_yuan,
                str(occurred_at or now_iso()),
                now_iso(),
            ),
        )
        event_id = int(cursor.lastrowid)
        normalized_importance = max(0.0, min(1.0, float(importance or 0)))
        if should_speak or normalized_importance >= 0.7:
            timestamp = now_iso()
            conn.execute(
                """
                INSERT OR IGNORE INTO agent_events (
                    event_key, event_type, source, conversation_id, goal_id, capability,
                    risk_level, payload_json, relevance, confidence, urgency,
                    interruption_cost, occurred_at, available_at, status, created_at, updated_at
                ) VALUES (?, 'screen_event', 'screen_observation', ?, 0, 'screen_event',
                          'read_only', ?, ?, 1.0, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    f"screen_event:{event_id}",
                    str(conversation_id or "desktop_agent")[:120],
                    json.dumps(
                        {
                            "screen_event_id": event_id,
                            "observation_id": int(observation_id or 0),
                            "event_type": str(event_type or "scene_change")[:80],
                            "summary": str(event_summary or "")[:500],
                            "importance": normalized_importance,
                            "legacy_should_speak": bool(should_speak),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    normalized_importance,
                    normalized_importance,
                    0.8 if should_speak else 0.55,
                    str(occurred_at or timestamp),
                    str(occurred_at or timestamp),
                    timestamp,
                    timestamp,
                ),
            )
        return event_id


def save_observation(
    *,
    session_id: int,
    frame_id: int,
    game_name: str,
    event_type: str,
    summary: str,
    confidence: float,
    details_json: str,
    source: str,
    model_id: str,
    request_cost_yuan: float | None,
    occurred_at: str,
) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO observations (
                session_id, frame_id, game_name, event_type, summary, confidence,
                details_json, source, model_id, request_cost_yuan, occurred_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(session_id or 0),
                int(frame_id or 0),
                str(game_name or "")[:200],
                str(event_type or "unknown")[:80],
                str(summary or "")[:500],
                max(0.0, min(1.0, float(confidence or 0))),
                str(details_json or "{}")[:8000],
                str(source or "vision")[:40],
                str(model_id or "")[:120],
                request_cost_yuan,
                str(occurred_at or now_iso()),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def recent_observations(session_id: int, limit: int = 8) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, session_id, frame_id, game_name, event_type, summary,
                   confidence, details_json, source, model_id,
                   request_cost_yuan, occurred_at, created_at
            FROM observations
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(session_id or 0), max(1, min(50, int(limit)))),
        ).fetchall()


def get_game_session_state(session_id: int) -> dict[str, object]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT game_name, state_json, updated_at FROM game_session_states WHERE session_id = ?",
            (int(session_id or 0),),
        ).fetchone()
    if row is None:
        return {}
    try:
        state = json.loads(str(row["state_json"] or "{}"))
    except json.JSONDecodeError:
        state = {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("game_name", str(row["game_name"] or ""))
    state["updated_at"] = str(row["updated_at"] or "")
    return state


def upsert_game_session_state(session_id: int, game_name: str, state: dict[str, object]) -> None:
    timestamp = now_iso()
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))[:12000]
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO game_session_states (session_id, game_name, state_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                game_name = excluded.game_name,
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (int(session_id or 0), str(game_name or "")[:200], payload, timestamp),
        )


def recent_screen_event_summaries(session_id: int, limit: int = 8) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT event_summary
            FROM screen_events
            WHERE session_id = ? AND event_summary != ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(session_id or 0), max(1, min(50, int(limit)))),
        ).fetchall()
    return [str(row["event_summary"] or "") for row in rows]


def save_companion_reaction(
    *,
    screen_event_id: int,
    request_id: str,
    text: str,
    emotion: str,
    trigger_reason: str,
    voice_status: str = "pending",
    model_id: str = "",
    request_cost_yuan: float | None = None,
) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO companion_reactions (
                screen_event_id, request_id, text, emotion,
                trigger_reason, voice_status, model_id, request_cost_yuan, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(screen_event_id or 0),
                str(request_id or "")[:80],
                str(text or "")[:1000],
                str(emotion or "neutral")[:40],
                str(trigger_reason or "")[:500],
                str(voice_status or "pending")[:40],
                str(model_id or "")[:120],
                request_cost_yuan,
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


_conversation_repository = ConversationRepository(
    get_conn,
    lambda: now_iso(),
    lambda target_date: logical_day_bounds(target_date),
)


def get_recent_messages(limit: int = 30, conversation_id: str = "default") -> list[sqlite3.Row]:
    return _conversation_repository.get_recent_messages(limit, conversation_id)


def get_total_message_token_usage() -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) AS total
            FROM messages
            WHERE role = 'assistant'
            """
        ).fetchone()
    return int(row["total"] or 0) if row is not None else 0


def get_token_usage_summary(days: int = 30) -> dict[str, object]:
    day_count = max(1, min(365, int(days or 30)))
    current_date = date_value.fromisoformat(today_string())
    first_date = current_date - timedelta(days=day_count - 1)
    first_started_at, _ = logical_day_bounds(first_date.isoformat())

    def empty_usage(target_date: str = "") -> dict[str, int | str]:
        return {
            "date": target_date,
            "prompt_tokens": 0,
            "cached_prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "chat_tokens": 0,
            "screen_tokens": 0,
            "total_tokens": 0,
        }

    daily = {
        (first_date + timedelta(days=index)).isoformat(): empty_usage(
            (first_date + timedelta(days=index)).isoformat()
        )
        for index in range(day_count)
    }
    with get_conn() as conn:
        message_rows = conn.execute(
            """
            SELECT created_at, prompt_tokens, cached_prompt_tokens,
                   completion_tokens, reasoning_tokens
            FROM messages
            WHERE role = 'assistant' AND created_at >= ?
            """,
            (first_started_at,),
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

    for row in message_rows:
        try:
            logical_date = logical_date_for_datetime(datetime.fromisoformat(str(row["created_at"])))
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

    for row in screen_rows:
        usage = daily.get(str(row["date"] or ""))
        if usage is None:
            continue
        screen_tokens = max(0, int(row["prompt_tokens"] or 0)) + max(
            0, int(row["completion_tokens"] or 0)
        )
        usage["screen_tokens"] += screen_tokens
        usage["total_tokens"] += screen_tokens

    total = empty_usage()
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
        "logical_day_boundary_hour": settings.day_boundary_hour,
        "today": daily[current_date.isoformat()],
        "total": total,
        "days": list(reversed(list(daily.values()))),
    }


def list_recent_private_user_messages(limit: int = 500) -> list[sqlite3.Row]:
    return _conversation_repository.list_recent_private_user_messages(limit)


def get_message_by_id(message_id: int) -> sqlite3.Row | None:
    return _conversation_repository.get_message_by_id(message_id)


def get_latest_message_id(role: str = "", conversation_id: str = "") -> int:
    return _conversation_repository.get_latest_message_id(role, conversation_id)


def get_messages_after_id(
    after_id: int,
    role: str = "",
    limit: int = 50,
    conversation_id: str = "",
) -> list[sqlite3.Row]:
    return _conversation_repository.get_messages_after_id(after_id, role, limit, conversation_id)


def create_agent_conversation(conversation_id: str, title: str = "新对话") -> sqlite3.Row:
    return _conversation_repository.create_agent_conversation(conversation_id, title)


def get_agent_conversation(conversation_id: str) -> sqlite3.Row | None:
    return _conversation_repository.get_agent_conversation(conversation_id)


def list_agent_conversations(limit: int = 40) -> list[sqlite3.Row]:
    return _conversation_repository.list_agent_conversations(limit)


def touch_agent_conversation(conversation_id: str, first_message: str = "") -> None:
    _conversation_repository.touch_agent_conversation(conversation_id, first_message)


def rename_agent_conversation(conversation_id: str, title: str) -> sqlite3.Row | None:
    return _conversation_repository.rename_agent_conversation(conversation_id, title)


def list_conversation_attachment_records(conversation_id: str) -> list[str]:
    return _conversation_repository.list_conversation_attachment_records(conversation_id)


def delete_agent_conversation(conversation_id: str) -> bool:
    return _conversation_repository.delete_agent_conversation(conversation_id)


def get_messages_since(
    start_date: str,
    conversation_id: str = "default",
    limit: int = 200,
) -> list[sqlite3.Row]:
    return _conversation_repository.get_messages_since(start_date, conversation_id, limit)


def get_last_message(conversation_id: str = "default", role: str | None = None) -> sqlite3.Row | None:
    return _conversation_repository.get_last_message(conversation_id, role)

def get_today_messages(date: str | None = None) -> list[sqlite3.Row]:
    target_date = date or today_string()
    start_at, end_at = logical_day_bounds(target_date)
    with get_conn() as conn:
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


def add_diary_material(content: str, date: str | None = None, source: str = "web") -> int:
    target_date = date or today_string()
    try:
        with get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO diary_materials (date, content, source, used_in_diary, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (target_date, content, source, now_iso()),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError:
        # 唯一索引 (date, content) 兜底：同一天相同内容已在库中，回查返回已有 id。
        with get_conn() as conn:
            existing = conn.execute(
                """
                SELECT id FROM diary_materials
                WHERE date = ? AND content = ?
                ORDER BY id DESC LIMIT 1
                """,
                (target_date, content),
            ).fetchone()
            if existing:
                return int(existing["id"])
            raise


def add_diary_material_once(content: str, date: str | None = None, source: str = "auto") -> int:
    target_date = date or today_string()
    normalized = " ".join(content.split()).strip()[:1000]
    if not normalized:
        raise ValueError("日记素材不能为空。")
    with get_conn() as conn:
        # 唯一索引 (date, content) 幂等：并发重复插入由唯一约束兜底，随后回查返回已有 id。
        conn.execute(
            """
            INSERT OR IGNORE INTO diary_materials (date, content, source, used_in_diary, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (target_date, normalized, source, now_iso()),
        )
        existing = conn.execute(
            """
            SELECT id
            FROM diary_materials
            WHERE date = ? AND content = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_date, normalized),
        ).fetchone()
        if existing:
            return int(existing["id"])
        return 0


def list_diary_materials(date: str | None = None) -> list[sqlite3.Row]:
    target_date = date or today_string()
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, content, source, used_in_diary, created_at
            FROM diary_materials
            WHERE date = ?
            ORDER BY id ASC
            """,
            (target_date,),
        ).fetchall()


def get_daily_state(date: str | None = None) -> sqlite3.Row | None:
    target_date = date or today_string()
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, daily_thirty_status, daily_thirty_reason, mood, mood_score,
                   key_events, avoidance_signals, next_min_action, created_at, updated_at
            FROM daily_states
            WHERE date = ?
            """,
            (target_date,),
        ).fetchone()


def ensure_daily_state_today() -> str:
    """跨天后首次对话时确保今天有状态记录（默认未确认），避免把昨天的状态当成今天的。"""
    target_date = today_string()
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO daily_states (
                date, daily_thirty_status, daily_thirty_reason, mood, mood_score,
                key_events, avoidance_signals, next_min_action, created_at, updated_at
            )
            VALUES (?, 'unknown', '', '', 0, '', '', '', ?, ?)
            """,
            (target_date, timestamp, timestamp),
        )
    return target_date


def list_daily_states_since(start_date: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, daily_thirty_status, daily_thirty_reason, mood, mood_score,
                   key_events, avoidance_signals, next_min_action, created_at, updated_at
            FROM daily_states
            WHERE date >= ?
            ORDER BY date ASC
            """,
            (start_date,),
        ).fetchall()


def upsert_daily_state(
    date: str,
    daily_thirty_status: str,
    mood: str,
    key_events: str,
    avoidance_signals: str,
    next_min_action: str,
    daily_thirty_reason: str = "",
    mood_score: int = 0,
) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_states (
                date, daily_thirty_status, daily_thirty_reason, mood, mood_score, key_events,
                avoidance_signals, next_min_action, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                daily_thirty_status = excluded.daily_thirty_status,
                daily_thirty_reason = CASE WHEN excluded.daily_thirty_reason <> '' THEN excluded.daily_thirty_reason ELSE daily_states.daily_thirty_reason END,
                mood = CASE WHEN excluded.mood <> '' THEN excluded.mood ELSE daily_states.mood END,
                mood_score = CASE WHEN excluded.mood_score > 0 THEN excluded.mood_score ELSE daily_states.mood_score END,
                key_events = CASE WHEN excluded.key_events <> '' THEN excluded.key_events ELSE daily_states.key_events END,
                avoidance_signals = CASE WHEN excluded.avoidance_signals <> '' THEN excluded.avoidance_signals ELSE daily_states.avoidance_signals END,
                next_min_action = CASE WHEN excluded.next_min_action <> '' THEN excluded.next_min_action ELSE daily_states.next_min_action END,
                updated_at = excluded.updated_at
            """,
            (
                date,
                daily_thirty_status,
                daily_thirty_reason,
                mood,
                max(0, min(5, int(mood_score or 0))),
                key_events,
                avoidance_signals,
                next_min_action,
                timestamp,
                timestamp,
            ),
        )


def update_daily_thirty(
    status: str,
    reason: str,
    date: str | None = None,
) -> None:
    if status not in {"done", "partial", "missed", "unknown"}:
        raise ValueError("每日三十状态无效。")
    target_date = date or today_string()
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_states (
                date, daily_thirty_status, daily_thirty_reason, mood, key_events,
                avoidance_signals, next_min_action, created_at, updated_at
            )
            VALUES (?, ?, ?, '', '', '', '', ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                daily_thirty_status = excluded.daily_thirty_status,
                daily_thirty_reason = excluded.daily_thirty_reason,
                updated_at = excluded.updated_at
            """,
            (target_date, status, reason.strip()[:500], timestamp, timestamp),
        )


def update_daily_mood(
    mood: str,
    date: str | None = None,
    mood_score: int = 0,
) -> None:
    target_date = date or today_string()
    timestamp = now_iso()
    normalized_score = max(0, min(5, int(mood_score or 0)))
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_states (
                date, daily_thirty_status, daily_thirty_reason, mood, mood_score, key_events,
                avoidance_signals, next_min_action, created_at, updated_at
            )
            VALUES (?, 'unknown', '', ?, ?, '', '', '', ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                mood = excluded.mood,
                mood_score = CASE WHEN excluded.mood_score > 0 THEN excluded.mood_score ELSE daily_states.mood_score END,
                updated_at = excluded.updated_at
            """,
            (target_date, mood.strip()[:300], normalized_score, timestamp, timestamp),
        )


def update_daily_state_summary(
    *,
    date: str | None = None,
    mood: str = "",
    mood_score: int = 0,
    key_events: str = "",
    avoidance_signals: str = "",
    next_min_action: str = "",
) -> bool:
    target_date = date or today_string()
    normalized_mood = mood.strip()[:300]
    normalized_score = max(0, min(5, int(mood_score or 0)))
    normalized_events = key_events.strip()[:500]
    normalized_avoidance = avoidance_signals.strip()[:500]
    normalized_action = next_min_action.strip()[:500]
    if not any(
        (
            normalized_mood,
            normalized_score,
            normalized_events,
            normalized_avoidance,
            normalized_action,
        )
    ):
        return False

    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_states (
                date, daily_thirty_status, daily_thirty_reason, mood, mood_score, key_events,
                avoidance_signals, next_min_action, created_at, updated_at
            )
            VALUES (?, 'unknown', '', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                mood = CASE WHEN excluded.mood <> '' THEN excluded.mood ELSE daily_states.mood END,
                mood_score = CASE WHEN excluded.mood_score > 0 THEN excluded.mood_score ELSE daily_states.mood_score END,
                key_events = CASE WHEN excluded.key_events <> '' THEN excluded.key_events ELSE daily_states.key_events END,
                avoidance_signals = CASE WHEN excluded.avoidance_signals <> '' THEN excluded.avoidance_signals ELSE daily_states.avoidance_signals END,
                next_min_action = CASE WHEN excluded.next_min_action <> '' THEN excluded.next_min_action ELSE daily_states.next_min_action END,
                updated_at = excluded.updated_at
            """,
            (
                target_date,
                normalized_mood,
                normalized_score,
                normalized_events,
                normalized_avoidance,
                normalized_action,
                timestamp,
                timestamp,
            ),
        )
    return True


def remember_pending_thread(
    conversation_id: str,
    content: str,
    follow_up_after: str = "",
    source_message_id: int = 0,
) -> int:
    normalized = " ".join(content.split()).strip()[:500]
    if not normalized:
        raise ValueError("待跟进话题不能为空。")
    timestamp = now_iso()
    with get_conn() as conn:
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


def list_open_pending_threads(conversation_id: str, limit: int = 8) -> list[sqlite3.Row]:
    with get_conn() as conn:
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


def list_due_pending_threads(conversation_id: str, current_iso: str, limit: int = 5) -> list[sqlite3.Row]:
    with get_conn() as conn:
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


def resolve_pending_thread(conversation_id: str, query: str) -> int | None:
    normalized = " ".join(query.split()).strip().casefold()
    if not normalized:
        return None
    timestamp = now_iso()
    with get_conn() as conn:
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


def mark_pending_thread_mentioned(thread_id: int) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE pending_threads
            SET last_mentioned_at = ?, follow_up_after = '', updated_at = ?
            WHERE id = ?
            """,
            (timestamp, timestamp, thread_id),
        )


def create_agent_run(
    run_id: str,
    request_id: str,
    *,
    trace_id: str = "",
    conversation_id: str = "",
    source: str = "",
    source_message_id: int = 0,
    model_id: str = "",
    reasoning_level: str = "",
    max_steps: int = 8,
    max_model_calls: int = 3,
    max_tool_calls: int = 6,
    deadline_at: str = "",
) -> sqlite3.Row:
    timestamp = now_iso()
    clean_run_id = str(run_id or "").strip()[:80]
    clean_request_id = str(request_id or "").strip()[:80]
    if not clean_run_id or not clean_request_id:
        raise ValueError("Agent run ID 和请求 ID 不能为空。")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO agent_runs (
                run_id, request_id, trace_id, conversation_id, source,
                source_message_id, status, model_id, reasoning_level,
                max_steps, max_model_calls, max_tool_calls, deadline_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'planning', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_run_id,
                clean_request_id,
                str(trace_id or "")[:80],
                str(conversation_id or "")[:160],
                str(source or "")[:40],
                int(source_message_id or 0),
                str(model_id or "")[:160],
                str(reasoning_level or "")[:40],
                max(1, min(32, int(max_steps))),
                max(1, min(8, int(max_model_calls))),
                max(0, min(24, int(max_tool_calls))),
                str(deadline_at or "")[:40],
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM agent_runs WHERE request_id = ?",
            (clean_request_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("Agent run 持久化失败。")
    return row


def get_agent_run(run_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM agent_runs WHERE run_id = ?",
            (str(run_id or "").strip()[:80],),
        ).fetchone()


def get_agent_run_by_request(request_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM agent_runs WHERE request_id = ?",
            (str(request_id or "").strip()[:80],),
        ).fetchone()


def get_agent_runs_by_requests(request_ids: list[str]) -> list[sqlite3.Row]:
    normalized = list(dict.fromkeys(
        str(request_id or "").strip()[:80]
        for request_id in request_ids
        if str(request_id or "").strip()
    ))[:500]
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    with get_conn() as conn:
        return conn.execute(
            f"SELECT * FROM agent_runs WHERE request_id IN ({placeholders})",
            tuple(normalized),
        ).fetchall()


def save_model_route_observation(
    *,
    request_id: str = "",
    source: str = "",
    mode: str = "manual",
    task_type: str = "conversation",
    difficulty: str = "",
    selected_model_id: str = "",
    actual_model_id: str = "",
    reasoning_level: str = "",
    success: bool,
    error_code: str = "",
    first_token_latency_ms: float | int | None = None,
    total_latency_ms: float | int | None = None,
    request_cost_yuan: float | int | None = None,
    request_cost_source: str = "",
    candidates_json: str = "[]",
    task_profile_json: str = "{}",
    escalated_from_model_id: str = "",
    reason: str = "",
) -> int:
    def optional_number(value: float | int | None) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, number)

    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO model_route_observations (
                request_id, source, mode, task_type, difficulty,
                selected_model_id, actual_model_id, reasoning_level,
                success, error_code, first_token_latency_ms, total_latency_ms,
                request_cost_yuan, request_cost_source, candidates_json,
                task_profile_json, escalated_from_model_id, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(request_id or "")[:80],
                str(source or "unknown")[:40],
                "automatic" if mode == "automatic" else "manual",
                str(task_type or "conversation")[:60],
                str(difficulty or "")[:40],
                str(selected_model_id or "")[:200],
                str(actual_model_id or selected_model_id or "")[:200],
                str(reasoning_level or "")[:50],
                int(bool(success)),
                str(error_code or "")[:120],
                optional_number(first_token_latency_ms),
                optional_number(total_latency_ms),
                optional_number(request_cost_yuan),
                str(request_cost_source or "")[:60],
                str(candidates_json or "[]")[:12000],
                str(task_profile_json or "{}")[:12000],
                str(escalated_from_model_id or "")[:200],
                str(reason or "")[:500],
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def list_model_route_observations(
    limit: int = 100,
    *,
    model_id: str = "",
    task_type: str = "",
) -> list[sqlite3.Row]:
    conditions: list[str] = []
    parameters: list[object] = []
    if model_id:
        conditions.append("actual_model_id = ?")
        parameters.append(str(model_id)[:200])
    if task_type:
        conditions.append("task_type = ?")
        parameters.append(str(task_type)[:60])
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    parameters.append(max(1, min(int(limit), 2000)))
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT * FROM model_route_observations
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()


def relink_agent_run_source_message(run_id: str, source_message_id: int) -> None:
    clean_run_id = str(run_id or "").strip()[:80]
    clean_message_id = max(0, int(source_message_id or 0))
    if not clean_run_id or clean_message_id <= 0:
        raise ValueError("Agent run ID 和来源消息 ID 不能为空。")
    with get_conn() as conn:
        conn.execute(
            "UPDATE agent_runs SET source_message_id = ?, updated_at = ? WHERE run_id = ?",
            (clean_message_id, now_iso(), clean_run_id),
        )
        conn.execute(
            "UPDATE companion_actions SET source_message_id = ? WHERE agent_run_id = ?",
            (clean_message_id, clean_run_id),
        )


def update_agent_run(
    run_id: str,
    status: str,
    *,
    plan_json: str | None = None,
    observation_json: str | None = None,
    summary_json: str | None = None,
    error: str | None = None,
    model_calls: int | None = None,
    tool_calls: int | None = None,
    replan_count: int | None = None,
) -> None:
    terminal = str(status) in {"completed", "failed", "cancelled", "timed_out", "interrupted"}
    assignments = ["status = ?", "updated_at = ?"]
    parameters: list[object] = [str(status or "running")[:40], now_iso()]
    optional_values = (
        ("plan_json", plan_json, 12000),
        ("observation_json", observation_json, 20000),
        ("summary_json", summary_json, 12000),
        ("error", error, 1000),
        ("model_calls", model_calls, None),
        ("tool_calls", tool_calls, None),
        ("replan_count", replan_count, None),
    )
    for column, value, max_chars in optional_values:
        if value is None:
            continue
        assignments.append(f"{column} = ?")
        parameters.append(str(value)[:max_chars] if max_chars is not None else max(0, int(value)))
    if terminal:
        assignments.append("finished_at = ?")
        parameters.append(now_iso())
    parameters.append(str(run_id or "").strip()[:80])
    with get_conn() as conn:
        conn.execute(
            f"UPDATE agent_runs SET {', '.join(assignments)} WHERE run_id = ?",
            parameters,
        )


def list_agent_runs(limit: int = 100, conversation_id: str = "") -> list[sqlite3.Row]:
    conditions = ""
    parameters: list[object] = []
    if conversation_id:
        conditions = "WHERE conversation_id = ?"
        parameters.append(str(conversation_id)[:160])
    parameters.append(max(1, min(int(limit), 500)))
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT * FROM agent_runs
            {conditions}
            ORDER BY created_at DESC, run_id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()


def claim_agent_run_step(
    run_id: str,
    step_index: int,
    step_kind: str,
    idempotency_key: str,
    *,
    tool_call_id: str = "",
    tool_name: str = "",
    permission: str = "",
    arguments_json: str = "{}",
) -> tuple[bool, sqlite3.Row]:
    timestamp = now_iso()
    clean_key = str(idempotency_key or "").strip()[:160]
    if not clean_key:
        raise ValueError("Agent 步骤幂等键不能为空。")
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO agent_run_steps (
                run_id, step_index, step_kind, tool_call_id, tool_name,
                permission, status, arguments_json, idempotency_key,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                str(run_id or "")[:80],
                max(0, int(step_index)),
                str(step_kind or "")[:40],
                str(tool_call_id or "")[:120],
                str(tool_name or "")[:80],
                str(permission or "")[:40],
                str(arguments_json or "{}")[:8000],
                clean_key,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM agent_run_steps WHERE idempotency_key = ?",
            (clean_key,),
        ).fetchone()
    if row is None:
        raise RuntimeError("Agent 步骤持久化失败。")
    return cursor.rowcount > 0, row


def update_agent_run_step(
    step_id: int,
    status: str,
    *,
    result_json: str | None = None,
    error: str | None = None,
    action_id: int | None = None,
    receipt_id: int | None = None,
) -> None:
    terminal = str(status) in {
        "completed", "failed", "cancelled", "timed_out", "needs_confirmation", "skipped"
    }
    assignments = ["status = ?", "updated_at = ?"]
    parameters: list[object] = [str(status or "running")[:40], now_iso()]
    if str(status) == "running":
        assignments.append("started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END")
        parameters.append(now_iso())
    if result_json is not None:
        assignments.append("result_json = ?")
        parameters.append(str(result_json or "{}")[:12000])
    if error is not None:
        assignments.append("error = ?")
        parameters.append(str(error or "")[:1000])
    if action_id is not None:
        assignments.append("action_id = ?")
        parameters.append(max(0, int(action_id)))
    if receipt_id is not None:
        assignments.append("receipt_id = ?")
        parameters.append(max(0, int(receipt_id)))
    if terminal:
        assignments.append("finished_at = ?")
        parameters.append(now_iso())
    parameters.append(int(step_id))
    with get_conn() as conn:
        conn.execute(
            f"UPDATE agent_run_steps SET {', '.join(assignments)} WHERE id = ?",
            parameters,
        )


def list_agent_run_steps(run_id: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT * FROM agent_run_steps
            WHERE run_id = ?
            ORDER BY step_index ASC, id ASC
            """,
            (str(run_id or "")[:80],),
        ).fetchall()


def list_agent_run_steps_many(run_ids: list[str]) -> list[sqlite3.Row]:
    normalized = list(dict.fromkeys(
        str(run_id or "").strip()[:80]
        for run_id in run_ids
        if str(run_id or "").strip()
    ))[:500]
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT * FROM agent_run_steps
            WHERE run_id IN ({placeholders})
            ORDER BY run_id ASC, step_index ASC, id ASC
            """,
            tuple(normalized),
        ).fetchall()


def get_agent_run_step(step_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM agent_run_steps WHERE id = ?",
            (int(step_id),),
        ).fetchone()


def start_tool_execution_receipt(
    conversation_id: str,
    tool_name: str,
    permission: str,
    request_json: str,
    status: str = "running",
    *,
    request_id: str = "",
    trace_id: str = "",
    agent_run_id: str = "",
    agent_step_id: int = 0,
    action_id: int = 0,
    idempotency_key: str = "",
) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO tool_execution_receipts (
                conversation_id, tool_name, permission, status, request_json,
                result, created_at, finished_at, request_id, trace_id,
                agent_run_id, agent_step_id, action_id, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, '', ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                str(conversation_id or "")[:120],
                str(tool_name or "")[:80],
                str(permission or "")[:40],
                str(status or "running")[:40],
                str(request_json or "{}")[:4000],
                now_iso(),
                str(request_id or "")[:80],
                str(trace_id or "")[:80],
                str(agent_run_id or "")[:80],
                int(agent_step_id or 0),
                int(action_id or 0),
                str(idempotency_key or "")[:160],
            ),
        )
        if cursor.rowcount > 0:
            return int(cursor.lastrowid)
        row = conn.execute(
            "SELECT id FROM tool_execution_receipts WHERE idempotency_key = ?",
            (str(idempotency_key or "")[:160],),
        ).fetchone()
        if row is None:
            raise RuntimeError("工具回执持久化失败。")
        return int(row["id"])


def finish_tool_execution_receipt(receipt_id: int, status: str, result: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE tool_execution_receipts
            SET status = ?, result = ?, finished_at = ?
            WHERE id = ?
            """,
            (str(status)[:40], str(result or "")[:1000], now_iso(), int(receipt_id)),
        )


def list_tool_execution_receipts(limit: int = 100) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, conversation_id, tool_name, permission, status,
                   request_json, result, created_at, finished_at,
                   request_id, trace_id, agent_run_id, agent_step_id,
                   action_id, idempotency_key
            FROM tool_execution_receipts
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()


def log_companion_action(
    conversation_id: str,
    action_type: str,
    payload_json: str,
    status: str,
    result: str = "",
    source_message_id: int = 0,
    requires_confirmation: bool = False,
    request_id: str = "",
    trace_id: str = "",
    agent_run_id: str = "",
    agent_step_id: int = 0,
    idempotency_key: str = "",
) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO companion_actions (
                date, conversation_id, action_type, payload_json, status, result,
                source_message_id, requires_confirmation, created_at,
                request_id, trace_id, agent_run_id, agent_step_id, idempotency_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                today_string(),
                conversation_id,
                action_type[:80],
                payload_json[:4000],
                status[:40],
                result[:1000],
                int(source_message_id or 0),
                1 if requires_confirmation else 0,
                now_iso(),
                str(request_id or "")[:80],
                str(trace_id or "")[:80],
                str(agent_run_id or "")[:80],
                int(agent_step_id or 0),
                str(idempotency_key or "")[:160],
            ),
        )
        if cursor.rowcount > 0:
            return int(cursor.lastrowid)
        row = conn.execute(
            "SELECT id FROM companion_actions WHERE idempotency_key = ?",
            (str(idempotency_key or "")[:160],),
        ).fetchone()
        if row is None:
            raise RuntimeError("Agent 动作持久化失败。")
        return int(row["id"])


def update_companion_action(
    action_id: int,
    status: str,
    result: str = "",
    *,
    approved: bool = False,
) -> None:
    timestamp = now_iso()
    terminal = status in {"executed", "failed", "skipped", "cancelled"}
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE companion_actions
            SET status = ?, result = ?, approved_at = CASE WHEN ? THEN ? ELSE approved_at END,
                finished_at = CASE WHEN ? THEN ? ELSE finished_at END
            WHERE id = ?
            """,
            (
                str(status)[:40],
                str(result or "")[:1000],
                1 if approved else 0,
                timestamp,
                1 if terminal else 0,
                timestamp,
                int(action_id),
            ),
        )
        if terminal:
            action = conn.execute(
                "SELECT conversation_id, action_type FROM companion_actions WHERE id = ?",
                (int(action_id),),
            ).fetchone()
            if action is not None:
                event_key = f"companion_action:{int(action_id)}:{str(status)[:40]}"
                relevance = 0.85 if status == "failed" else 0.65
                urgency = 0.8 if status == "failed" else 0.35
                conn.execute(
                    """
                    INSERT OR IGNORE INTO agent_events (
                        event_key, event_type, source, conversation_id, goal_id, capability,
                        risk_level, payload_json, relevance, confidence, urgency,
                        interruption_cost, occurred_at, available_at, status, created_at, updated_at
                    ) VALUES (?, 'task_result', 'agent_tool', ?, 0, 'task_result',
                              'read_only', ?, ?, 1.0, ?, 0.8, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        event_key,
                        str(action["conversation_id"] or "")[:120],
                        json.dumps(
                            {
                                "action_id": int(action_id),
                                "action_type": str(action["action_type"] or ""),
                                "status": str(status),
                                "result": str(result or "")[:1000],
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        relevance,
                        urgency,
                        timestamp,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )


def list_companion_actions(limit: int = 100, conversation_id: str = "") -> list[sqlite3.Row]:
    bounded_limit = max(1, min(500, int(limit)))
    conditions = ""
    parameters: list[object] = []
    if conversation_id:
        conditions = "WHERE conversation_id = ?"
        parameters.append(conversation_id)
    parameters.append(bounded_limit)
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT id, date, conversation_id, action_type, payload_json, status, result,
                   source_message_id, requires_confirmation, approved_at, created_at, finished_at,
                   request_id, trace_id, agent_run_id, agent_step_id, idempotency_key
            FROM companion_actions
            {conditions}
            ORDER BY id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()


def get_companion_action(action_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, conversation_id, action_type, payload_json, status, result,
                   source_message_id, requires_confirmation, approved_at, created_at, finished_at,
                   request_id, trace_id, agent_run_id, agent_step_id, idempotency_key
            FROM companion_actions
            WHERE id = ?
            """,
            (int(action_id),),
        ).fetchone()


def get_day_summary(date: str | None = None) -> dict[str, object]:
    target_date = date or today_string()
    start_at, end_at = logical_day_bounds(target_date)
    with get_conn() as conn:
        message_counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END) AS user_total,
                SUM(CASE WHEN role = 'assistant' THEN 1 ELSE 0 END) AS assistant_total
            FROM messages
            WHERE created_at >= ?
              AND created_at < ?
            """,
            (start_at, end_at),
        ).fetchone()
        diary = conn.execute(
            """
            SELECT date, title, daily_thirty_status, updated_at
            FROM diaries
            WHERE date = ?
            """,
            (target_date,),
        ).fetchone()
        state = conn.execute(
            """
            SELECT daily_thirty_status, daily_thirty_reason, mood, key_events, avoidance_signals,
                   next_min_action, updated_at
            FROM daily_states
            WHERE date = ?
            """,
            (target_date,),
        ).fetchone()
        material_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM diary_materials
            WHERE date = ?
            """,
            (target_date,),
        ).fetchone()[0]

    return {
        "date": target_date,
        "message_count": int(message_counts["total"] or 0),
        "user_message_count": int(message_counts["user_total"] or 0),
        "assistant_message_count": int(message_counts["assistant_total"] or 0),
        "diary_exists": diary is not None,
        "diary_title": diary["title"] if diary else "",
        "daily_thirty_status": state["daily_thirty_status"] if state else (diary["daily_thirty_status"] if diary else "unknown"),
        "daily_thirty_reason": state["daily_thirty_reason"] if state else "",
        "mood": state["mood"] if state else "未判定",
        "key_events": state["key_events"] if state else "未判定",
        "avoidance_signals": state["avoidance_signals"] if state else "未判定",
        "next_min_action": state["next_min_action"] if state else "未判定",
        "state_updated_at": state["updated_at"] if state else "",
        "material_count": int(material_count or 0),
        "diary_updated_at": diary["updated_at"] if diary else "",
    }


def upsert_diary(
    date: str,
    title: str,
    markdown_content: str,
    mood_tags: str = "",
    daily_thirty_status: str = "unknown",
    confirmed_at: str | None = None,
) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
        current = conn.execute(
            "SELECT confirmed_at FROM diaries WHERE date = ?",
            (date,),
        ).fetchone()
        if confirmed_at is None:
            effective_confirmed_at = current["confirmed_at"] if current else ""
        else:
            effective_confirmed_at = confirmed_at
        conn.execute(
            """
            INSERT INTO diaries (
                date, title, markdown_content, mood_tags, daily_thirty_status, confirmed_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                title = excluded.title,
                markdown_content = excluded.markdown_content,
                mood_tags = excluded.mood_tags,
                daily_thirty_status = excluded.daily_thirty_status,
                confirmed_at = excluded.confirmed_at,
                updated_at = excluded.updated_at
            """,
            (
                date,
                title,
                markdown_content,
                mood_tags,
                daily_thirty_status,
                effective_confirmed_at,
                timestamp,
                timestamp,
            ),
        )


def list_diaries() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, title, markdown_content, mood_tags, daily_thirty_status,
                   confirmed_at, created_at, updated_at
            FROM diaries
            ORDER BY date DESC
            """
        ).fetchall()


def search_diaries(query: str = "") -> list[sqlite3.Row]:
    keyword = query.strip()
    if not keyword:
        return list_diaries()

    pattern = f"%{keyword}%"
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, title, markdown_content, mood_tags, daily_thirty_status,
                   confirmed_at, created_at, updated_at
            FROM diaries
            WHERE title LIKE ?
               OR markdown_content LIKE ?
               OR mood_tags LIKE ?
               OR daily_thirty_status LIKE ?
            ORDER BY date DESC
            """,
            (pattern, pattern, pattern, pattern),
        ).fetchall()


def get_diary(date: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, title, markdown_content, mood_tags, daily_thirty_status,
                   confirmed_at, created_at, updated_at
            FROM diaries
            WHERE date = ?
            """,
            (date,),
        ).fetchone()


def list_diaries_since(start_date: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, title, markdown_content, mood_tags,
                   daily_thirty_status, confirmed_at, created_at, updated_at
            FROM diaries
            WHERE date >= ?
            ORDER BY date ASC
            """,
            (start_date,),
        ).fetchall()


def list_diary_exports() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT date, title, markdown_content, updated_at
            FROM diaries
            ORDER BY date ASC
            """
        ).fetchall()


def get_daily_review(date: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, markdown_content, created_at, updated_at
            FROM daily_reviews
            WHERE date = ?
            """,
            (date,),
        ).fetchone()


def list_daily_reviews_since(start_date: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, date, markdown_content, created_at, updated_at
            FROM daily_reviews
            WHERE date >= ?
            ORDER BY date ASC
            """,
            (start_date,),
        ).fetchall()


def upsert_daily_review(date: str, markdown_content: str) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_reviews (date, markdown_content, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                markdown_content = excluded.markdown_content,
                updated_at = excluded.updated_at
            """,
            (date, markdown_content, timestamp, timestamp),
        )


def set_diary_confirmed(date: str, confirmed: bool = True) -> str | None:
    timestamp = now_iso()
    confirmed_at = timestamp if confirmed else ""
    with get_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE diaries
            SET confirmed_at = ?, updated_at = ?
            WHERE date = ?
            """,
            (confirmed_at, timestamp, date),
        )
    if cursor.rowcount == 0:
        return None
    return confirmed_at


def get_qq_proactive_state(user_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT user_id, last_user_message_at, next_prompt_at, last_prompt_at, updated_at
            FROM qq_proactive_states
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()


def upsert_qq_proactive_state(
    user_id: str,
    last_user_message_at: str,
    next_prompt_at: str,
    last_prompt_at: str = "",
) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO qq_proactive_states (
                user_id, last_user_message_at, next_prompt_at, last_prompt_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_user_message_at = excluded.last_user_message_at,
                next_prompt_at = excluded.next_prompt_at,
                last_prompt_at = excluded.last_prompt_at,
                updated_at = excluded.updated_at
            """,
            (user_id, last_user_message_at, next_prompt_at, last_prompt_at, timestamp),
        )


def record_proactive_topic(
    conversation_id: str,
    topic_key: str,
    topic_kind: str,
    topic_text: str,
    score: float,
) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO proactive_topic_history (
                conversation_id, topic_key, topic_kind, topic_text, score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(conversation_id)[:120],
                str(topic_key)[:120],
                str(topic_kind)[:40],
                str(topic_text)[:500],
                float(score),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def list_recent_proactive_topics(conversation_id: str, limit: int = 12) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, conversation_id, topic_key, topic_kind, topic_text, score, created_at
            FROM proactive_topic_history
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (str(conversation_id), max(1, min(int(limit), 100))),
        ).fetchall()


def get_autonomy_policy() -> sqlite3.Row:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM autonomy_policies WHERE id = 1").fetchone()
        if row is None:
            timestamp = now_iso()
            conn.execute(
                """
                INSERT INTO autonomy_policies (
                    id, paused, autonomy_level, quiet_start_hour, quiet_end_hour,
                    minimum_interval_minutes, daily_behavior_limit, daily_budget_yuan,
                    capability_overrides_json, updated_at
                ) VALUES (1, 0, 'suggest', 22, 8, 120, 3, 0.05, '{}', ?)
                """,
                (timestamp,),
            )
            row = conn.execute("SELECT * FROM autonomy_policies WHERE id = 1").fetchone()
        assert row is not None
        return row


def update_autonomy_policy(values: dict[str, object]) -> sqlite3.Row:
    allowed = {
        "paused",
        "autonomy_level",
        "quiet_start_hour",
        "quiet_end_hour",
        "minimum_interval_minutes",
        "daily_behavior_limit",
        "daily_budget_yuan",
        "capability_overrides_json",
    }
    changes = {key: value for key, value in values.items() if key in allowed}
    if changes:
        changes["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in changes)
        with get_conn() as conn:
            conn.execute(
                f"UPDATE autonomy_policies SET {assignments} WHERE id = 1",
                tuple(changes.values()),
            )
    return get_autonomy_policy()


def create_agent_goal(
    title: str,
    *,
    description: str = "",
    conversation_id: str = "",
    source_kind: str = "manual",
    source_ref: str = "",
    autonomy_level: str = "",
    capabilities: list[str] | tuple[str, ...] = (),
    due_at: str = "",
) -> sqlite3.Row:
    timestamp = now_iso()
    normalized_ref = str(source_ref or "")[:160]
    with get_conn() as conn:
        if normalized_ref:
            existing = conn.execute(
                "SELECT * FROM agent_goals WHERE source_kind = ? AND source_ref = ?",
                (str(source_kind)[:40], normalized_ref),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    """
                    UPDATE agent_goals
                    SET title = ?, description = ?, conversation_id = ?, due_at = ?,
                        capabilities_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        str(title)[:200],
                        str(description)[:2000],
                        str(conversation_id)[:120],
                        str(due_at)[:40],
                        json.dumps(list(capabilities), ensure_ascii=False),
                        timestamp,
                        int(existing["id"]),
                    ),
                )
                result = conn.execute(
                    "SELECT * FROM agent_goals WHERE id = ?", (int(existing["id"]),)
                ).fetchone()
                assert result is not None
                return result
        cursor = conn.execute(
            """
            INSERT INTO agent_goals (
                conversation_id, title, description, status, source_kind, source_ref,
                autonomy_level, capabilities_json, due_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(conversation_id)[:120],
                str(title)[:200],
                str(description)[:2000],
                str(source_kind)[:40],
                normalized_ref,
                str(autonomy_level)[:40],
                json.dumps(list(capabilities), ensure_ascii=False),
                str(due_at)[:40],
                timestamp,
                timestamp,
            ),
        )
        result = conn.execute(
            "SELECT * FROM agent_goals WHERE id = ?", (int(cursor.lastrowid),)
        ).fetchone()
        assert result is not None
        return result


def get_agent_goal(goal_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM agent_goals WHERE id = ?", (int(goal_id),)).fetchone()


def list_agent_goals(limit: int = 100, status: str = "") -> list[sqlite3.Row]:
    where = "WHERE status = ?" if status else ""
    params: tuple[object, ...] = (str(status),) if status else ()
    with get_conn() as conn:
        return conn.execute(
            f"SELECT * FROM agent_goals {where} ORDER BY updated_at DESC, id DESC LIMIT ?",
            (*params, max(1, min(int(limit), 500))),
        ).fetchall()


def update_agent_goal_status(goal_id: int, status: str) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE agent_goals SET status = ?, updated_at = ? WHERE id = ?",
            (str(status)[:40], now_iso(), int(goal_id)),
        )
        return cursor.rowcount > 0


def record_agent_event(
    event_key: str,
    event_type: str,
    *,
    source: str = "",
    conversation_id: str = "",
    goal_id: int = 0,
    capability: str = "",
    risk_level: str = "read_only",
    payload: dict[str, object] | None = None,
    relevance: float = 0.0,
    confidence: float = 0.0,
    urgency: float = 0.0,
    interruption_cost: float = 0.0,
    occurred_at: str = "",
    available_at: str = "",
) -> sqlite3.Row:
    timestamp = now_iso()
    occurred = str(occurred_at or timestamp)
    available = str(available_at or occurred)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO agent_events (
                event_key, event_type, source, conversation_id, goal_id, capability,
                risk_level, payload_json, relevance, confidence, urgency,
                interruption_cost, occurred_at, available_at, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                str(event_key)[:240],
                str(event_type)[:80],
                str(source)[:80],
                str(conversation_id)[:120],
                max(0, int(goal_id or 0)),
                str(capability)[:80],
                str(risk_level)[:40],
                json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))[:12000],
                min(1.0, max(0.0, float(relevance))),
                min(1.0, max(0.0, float(confidence))),
                min(1.0, max(0.0, float(urgency))),
                min(1.0, max(0.0, float(interruption_cost))),
                occurred,
                available,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM agent_events WHERE event_key = ?", (str(event_key)[:240],)
        ).fetchone()
        assert row is not None
        return row


def claim_next_agent_event(
    current_iso: str,
    stale_before_iso: str,
    claim_token: str,
) -> sqlite3.Row | None:
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM agent_events
            WHERE (status = 'pending' AND available_at <= ?)
               OR (status = 'claimed' AND claimed_at != '' AND claimed_at <= ?)
            ORDER BY urgency DESC, available_at, id
            LIMIT 1
            """,
            (str(current_iso), str(stale_before_iso)),
        ).fetchone()
        if row is None:
            return None
        timestamp = now_iso()
        cursor = conn.execute(
            """
            UPDATE agent_events
            SET status = 'claimed', claim_token = ?, claimed_at = ?, attempts = attempts + 1,
                error = '', updated_at = ?
            WHERE id = ? AND (
                (status = 'pending' AND available_at <= ?)
                OR (status = 'claimed' AND claimed_at != '' AND claimed_at <= ?)
            )
            """,
            (
                str(claim_token)[:120],
                timestamp,
                timestamp,
                int(row["id"]),
                str(current_iso),
                str(stale_before_iso),
            ),
        )
        if cursor.rowcount != 1:
            return None
        return conn.execute("SELECT * FROM agent_events WHERE id = ?", (int(row["id"]),)).fetchone()


def finish_agent_event(
    event_id: int,
    status: str,
    *,
    reason: str = "",
    error: str = "",
) -> bool:
    timestamp = now_iso()
    with get_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE agent_events
            SET status = ?, decision_reason = ?, error = ?, processed_at = ?,
                claim_token = '', updated_at = ?
            WHERE id = ?
            """,
            (str(status)[:40], str(reason)[:2000], str(error)[:1000], timestamp, timestamp, int(event_id)),
        )
        return cursor.rowcount > 0


def reschedule_agent_event(event_id: int, available_at: str, reason: str) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE agent_events
            SET status = 'pending', available_at = ?, decision_reason = ?,
                claim_token = '', claimed_at = '', updated_at = ?
            WHERE id = ?
            """,
            (str(available_at), str(reason)[:2000], now_iso(), int(event_id)),
        )
        return cursor.rowcount > 0


def list_agent_events(limit: int = 100, status: str = "") -> list[sqlite3.Row]:
    where = "WHERE status = ?" if status else ""
    params: tuple[object, ...] = (str(status),) if status else ()
    with get_conn() as conn:
        return conn.execute(
            f"SELECT * FROM agent_events {where} ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(int(limit), 500))),
        ).fetchall()


def create_autonomy_behavior(
    behavior_key: str,
    *,
    event_id: int,
    goal_id: int,
    conversation_id: str,
    behavior_type: str,
    capability: str,
    risk_level: str,
    permission_mode: str,
    status: str,
    reason: str,
    evidence: dict[str, object],
    content: str,
    destination: str = "app",
    request_id: str = "",
    model_id: str = "",
    provider_id: str = "",
    provider_name: str = "",
    provider_model: str = "",
    provider_request_id: str = "",
    reasoning_level: str = "",
    prompt_tokens: int = 0,
    cached_prompt_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
    first_token_latency_ms: float | None = None,
    total_latency_ms: float | None = None,
    cost_yuan: float = 0.0,
    cost_source: str = "",
) -> sqlite3.Row:
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO autonomy_behaviors (
                behavior_key, event_id, goal_id, conversation_id, behavior_type,
                capability, risk_level, permission_mode, status, reason, evidence_json,
                content, destination, delivery_status, request_id, model_id, provider_id,
                provider_name, provider_model, provider_request_id, reasoning_level,
                prompt_tokens, cached_prompt_tokens, completion_tokens, reasoning_tokens,
                first_token_latency_ms, total_latency_ms, cost_yuan, cost_source,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'not_attempted', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(behavior_key)[:240],
                max(0, int(event_id or 0)),
                max(0, int(goal_id or 0)),
                str(conversation_id)[:120],
                str(behavior_type)[:80],
                str(capability)[:80],
                str(risk_level)[:40],
                str(permission_mode)[:40],
                str(status)[:40],
                str(reason)[:2000],
                json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))[:12000],
                str(content)[:4000],
                str(destination)[:80],
                str(request_id)[:160],
                str(model_id)[:160],
                str(provider_id)[:160],
                str(provider_name)[:200],
                str(provider_model)[:200],
                str(provider_request_id)[:200],
                str(reasoning_level)[:40],
                max(0, int(prompt_tokens or 0)),
                max(0, int(cached_prompt_tokens or 0)),
                max(0, int(completion_tokens or 0)),
                max(0, int(reasoning_tokens or 0)),
                first_token_latency_ms,
                total_latency_ms,
                max(0.0, float(cost_yuan or 0.0)),
                str(cost_source)[:80],
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM autonomy_behaviors WHERE behavior_key = ?",
            (str(behavior_key)[:240],),
        ).fetchone()
        assert row is not None
        return row


def get_autonomy_behavior(behavior_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM autonomy_behaviors WHERE id = ?", (int(behavior_id),)
        ).fetchone()


def get_autonomy_behavior_by_key(behavior_key: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM autonomy_behaviors WHERE behavior_key = ?",
            (str(behavior_key)[:240],),
        ).fetchone()


def update_autonomy_behavior(behavior_id: int, values: dict[str, object]) -> bool:
    allowed = {
        "status",
        "reason",
        "delivery_status",
        "app_message_id",
        "qq_delivery_status",
        "cost_yuan",
        "cost_source",
        "completed_at",
    }
    changes = {key: value for key, value in values.items() if key in allowed}
    if not changes:
        return False
    changes["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in changes)
    with get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE autonomy_behaviors SET {assignments} WHERE id = ?",
            (*changes.values(), int(behavior_id)),
        )
        return cursor.rowcount > 0


def list_autonomy_behaviors(limit: int = 100, status: str = "") -> list[sqlite3.Row]:
    where = "WHERE status = ?" if status else ""
    params: tuple[object, ...] = (str(status),) if status else ()
    with get_conn() as conn:
        return conn.execute(
            f"SELECT * FROM autonomy_behaviors {where} ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(int(limit), 500))),
        ).fetchall()


def autonomy_usage_between(started_at: str, ended_at: str) -> dict[str, object]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS behavior_count, COALESCE(SUM(cost_yuan), 0) AS cost_yuan,
                   MAX(completed_at) AS last_completed_at
            FROM autonomy_behaviors
            WHERE created_at >= ? AND created_at < ?
              AND status IN ('delivered', 'delivery_unknown', 'completed')
            """,
            (str(started_at), str(ended_at)),
        ).fetchone()
    return {
        "behavior_count": int(row["behavior_count"] or 0) if row else 0,
        "cost_yuan": float(row["cost_yuan"] or 0.0) if row else 0.0,
        "last_completed_at": str(row["last_completed_at"] or "") if row else "",
    }


def get_weekly_review(week_start: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, week_start, markdown_content, created_at, updated_at
            FROM weekly_reviews
            WHERE week_start = ?
            """,
            (week_start,),
        ).fetchone()


def list_weekly_reviews() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, week_start, markdown_content, created_at, updated_at
            FROM weekly_reviews
            ORDER BY week_start DESC
            """
        ).fetchall()


def upsert_weekly_review(week_start: str, markdown_content: str) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO weekly_reviews (week_start, markdown_content, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                markdown_content = excluded.markdown_content,
                updated_at = excluded.updated_at
            """,
            (week_start, markdown_content, timestamp, timestamp),
        )


def get_monthly_review(month: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, month, markdown_content, created_at, updated_at
            FROM monthly_reviews
            WHERE month = ?
            """,
            (month,),
        ).fetchone()


def list_monthly_reviews() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, month, markdown_content, created_at, updated_at
            FROM monthly_reviews
            ORDER BY month DESC
            """
        ).fetchall()


def upsert_monthly_review(month: str, markdown_content: str) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO monthly_reviews (month, markdown_content, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(month) DO UPDATE SET
                markdown_content = excluded.markdown_content,
                updated_at = excluded.updated_at
            """,
            (month, markdown_content, timestamp, timestamp),
        )


def get_night_close_prompted_date(user_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT prompted_date FROM night_close_states WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return str(row["prompted_date"]) if row else ""


def set_night_close_prompted_date(user_id: str, date: str) -> None:
    timestamp = now_iso()
    with get_conn() as conn:
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


def list_all_open_pending_threads(limit: int = 50) -> list[sqlite3.Row]:
    with get_conn() as conn:
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


def get_pending_thread(thread_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, conversation_id, content, status, follow_up_after,
                   source_message_id, last_mentioned_at, created_at, updated_at
            FROM pending_threads
            WHERE id = ?
            """,
            (int(thread_id),),
        ).fetchone()


def find_open_pending_thread(conversation_id: str, query: str) -> sqlite3.Row | None:
    normalized = " ".join(str(query or "").split()).strip().casefold()
    if not normalized:
        return None
    with get_conn() as conn:
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


def record_follow_up_result(
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
    timestamp = now_iso()
    with get_conn() as conn:
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


def list_follow_up_results(
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
    with get_conn() as conn:
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


def resolve_pending_thread_by_id(thread_id: int) -> bool:
    timestamp = now_iso()
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE pending_threads SET status = 'resolved', updated_at = ? WHERE id = ? AND status = 'open'",
            (timestamp, thread_id),
        )
        return cursor.rowcount > 0


def update_pending_thread_by_id(thread_id: int, content: str, follow_up_after: str = "") -> bool:
    normalized = " ".join(content.split()).strip()[:500]
    if not normalized:
        raise ValueError("待跟进话题不能为空。")
    with get_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE pending_threads
            SET content = ?, follow_up_after = ?, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (normalized, follow_up_after.strip(), now_iso(), thread_id),
        )
        return cursor.rowcount > 0


def delete_pending_thread_by_id(thread_id: int) -> bool:
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM pending_threads WHERE id = ?", (thread_id,))
        return cursor.rowcount > 0


def list_memories_by_type(memory_type: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, type, content, importance, tags, created_at, updated_at
            FROM memories
            WHERE type = ?
            ORDER BY updated_at DESC
            """,
            (memory_type,),
        ).fetchall()


def list_reviews() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT date, markdown_content, created_at, updated_at
            FROM daily_reviews
            ORDER BY date DESC
            """
        ).fetchall()


def get_diary_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM diaries").fetchone()[0]

        status_rows = conn.execute(
            """
            SELECT daily_thirty_status, COUNT(*) AS cnt
            FROM diaries
            GROUP BY daily_thirty_status
            """
        ).fetchall()
        by_status: dict[str, int] = {"done": 0, "partial": 0, "missed": 0, "unknown": 0}
        for row in status_rows:
            key = row[0] if row[0] in by_status else "unknown"
            by_status[key] += int(row[1])

        done_dates_rows = conn.execute(
            """
            SELECT date FROM diaries
            WHERE daily_thirty_status = 'done'
            ORDER BY date DESC
            """
        ).fetchall()

    done_dates = {row[0] for row in done_dates_rows}

    # current_streak: count consecutive done days going back from today.
    # 今天还没结束、尚未判定完成时，不应把连击清零，从昨天开始往回数。
    current_streak = 0
    cursor_date = datetime.fromisoformat(today_string()).date()
    if cursor_date.isoformat() not in done_dates:
        cursor_date -= timedelta(days=1)
    while True:
        if cursor_date.isoformat() in done_dates:
            current_streak += 1
            cursor_date -= timedelta(days=1)
        else:
            break

    # longest_streak: scan all done dates sorted ascending
    longest_streak = 0
    if done_dates:
        sorted_dates = sorted(datetime.fromisoformat(d).date() for d in done_dates)
        run = 1
        longest_streak = 1
        for i in range(1, len(sorted_dates)):
            if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
                run += 1
                if run > longest_streak:
                    longest_streak = run
            else:
                run = 1

    completion_rate = round(by_status["done"] / total, 4) if total > 0 else 0.0

    return {
        "total": int(total),
        "by_status": by_status,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "completion_rate": completion_rate,
    }


def get_calendar_data(year: int, month: int) -> list[dict]:
    prefix = f"{year:04d}-{month:02d}-"
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT date, daily_thirty_status, title
            FROM diaries
            WHERE date LIKE ?
            ORDER BY date ASC
            """,
            (f"{prefix}%",),
        ).fetchall()
    return [
        {
            "date": row["date"],
            "daily_thirty_status": row["daily_thirty_status"],
            "title": row["title"],
        }
        for row in rows
    ]


def delete_diary(date: str) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM diaries WHERE date = ?",
            (date,),
        )
    return cursor.rowcount > 0


def search_all(query: str) -> dict:
    keyword = query.strip()
    pattern = f"%{keyword}%"
    with get_conn() as conn:
        diaries = conn.execute(
            """
            SELECT id, date, title, mood_tags, daily_thirty_status, confirmed_at, created_at, updated_at
            FROM diaries
            WHERE title LIKE ?
               OR markdown_content LIKE ?
               OR mood_tags LIKE ?
            ORDER BY date DESC
            """,
            (pattern, pattern, pattern),
        ).fetchall()

        reviews = conn.execute(
            """
            SELECT id, date, created_at, updated_at
            FROM daily_reviews
            WHERE markdown_content LIKE ?
            ORDER BY date DESC
            """,
            (pattern,),
        ).fetchall()

        states = conn.execute(
            """
            SELECT id, date, daily_thirty_status, daily_thirty_reason, mood, key_events,
                   avoidance_signals, next_min_action, created_at, updated_at
            FROM daily_states
            WHERE key_events LIKE ?
            ORDER BY date DESC
            """,
            (pattern,),
        ).fetchall()

    return {
        "diaries": [dict(row) for row in diaries],
        "reviews": [dict(row) for row in reviews],
        "states": [dict(row) for row in states],
    }


def mark_materials_used(date: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE diary_materials
            SET used_in_diary = 1
            WHERE date = ?
            """,
            (date,),
        )
