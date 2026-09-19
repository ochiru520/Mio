"""SQLite schema/bootstrap, unchanged SQL and migration behavior."""
from __future__ import annotations

from typing import Any, Callable


class SchemaRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep__ensure_structured_memory_fts: Callable[..., Any],
                 dep_get_conn: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 ) -> None:
        self._dep__ensure_structured_memory_fts = dep__ensure_structured_memory_fts
        self._dep_get_conn = dep_get_conn
        self._dep_now_iso = dep_now_iso

    def init_db(self) -> None:
        with self._dep_get_conn() as conn:
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

                CREATE TABLE IF NOT EXISTS deleted_conversations (id TEXT PRIMARY KEY);

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
                    source_window TEXT NOT NULL DEFAULT '',
                    source_message_id INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    occurred_at TEXT NOT NULL DEFAULT '',
                    learned_at TEXT NOT NULL DEFAULT '',
                    valid_from TEXT NOT NULL DEFAULT '',
                    valid_until TEXT NOT NULL DEFAULT '',
                    last_confirmed_at TEXT NOT NULL DEFAULT '',
                    time_confidence REAL NOT NULL DEFAULT 0,
                    temporal_status TEXT NOT NULL DEFAULT 'time_unknown',
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

                CREATE TABLE IF NOT EXISTS creation_assets (
                    id TEXT PRIMARY KEY,
                    original_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creation_presets (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    negative_prompt TEXT NOT NULL DEFAULT '',
                    params_json TEXT NOT NULL DEFAULT '{}',
                    reference_asset_ids_json TEXT NOT NULL DEFAULT '[]',
                    approved INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_creation_presets_kind_updated
                    ON creation_presets(kind, updated_at DESC);

                CREATE TABLE IF NOT EXISTS creation_jobs (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'creation_page',
                    media_type TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    provider_id TEXT NOT NULL DEFAULT '',
                    model_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'created',
                    stage TEXT NOT NULL DEFAULT 'created',
                    progress REAL NOT NULL DEFAULT 0,
                    prompt_id TEXT NOT NULL DEFAULT '',
                    client_id TEXT NOT NULL DEFAULT '',
                    spec_json TEXT NOT NULL DEFAULT '{}',
                    preset_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    outputs_json TEXT NOT NULL DEFAULT '[]',
                    confirmation_reason TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    parent_job_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_creation_jobs_created
                    ON creation_jobs(created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_creation_jobs_prompt
                    ON creation_jobs(prompt_id);

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
            self._ensure_column(conn, "diaries", "confirmed_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "daily_states", "daily_thirty_reason", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "daily_states", "mood_score", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "messages", "request_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "messages", "model_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "messages", "provider_model", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "messages", "reasoning_level", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "messages", "prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "messages", "cached_prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "messages", "completion_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "messages", "reasoning_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "messages", "request_cost_yuan", "REAL")
            self._ensure_column(conn, "messages", "request_cost_source", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "messages", "attachments_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "messages", "emotion", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "messages", "first_token_latency_ms", "REAL")
            self._ensure_column(conn, "messages", "total_latency_ms", "REAL")
            self._ensure_column(conn, "messages", "delivery_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "structured_memories", "occurred_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "structured_memories", "learned_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "structured_memories", "source_window", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "structured_memories", "valid_from", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "structured_memories", "valid_until", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "structured_memories", "last_confirmed_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "structured_memories", "time_confidence", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "structured_memories", "temporal_status", "TEXT NOT NULL DEFAULT 'time_unknown'")
            self._ensure_column(conn, "screen_events", "observation_id", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "companion_reactions", "model_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "companion_reactions", "request_cost_yuan", "REAL")
            self._ensure_column(conn, "companion_actions", "source_message_id", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "companion_actions", "requires_confirmation", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "companion_actions", "approved_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "companion_actions", "finished_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "companion_actions", "request_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "companion_actions", "trace_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "companion_actions", "agent_run_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "companion_actions", "agent_step_id", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "companion_actions", "idempotency_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "tool_execution_receipts", "request_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "tool_execution_receipts", "trace_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "tool_execution_receipts", "agent_run_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "tool_execution_receipts", "agent_step_id", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "tool_execution_receipts", "action_id", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "tool_execution_receipts", "idempotency_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "autonomy_behaviors", "model_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "autonomy_behaviors", "provider_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "autonomy_behaviors", "provider_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "autonomy_behaviors", "provider_model", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "autonomy_behaviors", "provider_request_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "autonomy_behaviors", "reasoning_level", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "autonomy_behaviors", "prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "autonomy_behaviors", "cached_prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "autonomy_behaviors", "completion_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "autonomy_behaviors", "reasoning_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "autonomy_behaviors", "first_token_latency_ms", "REAL")
            self._ensure_column(conn, "autonomy_behaviors", "total_latency_ms", "REAL")
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
                (self._dep_now_iso(),),
            )
            self._dep__ensure_structured_memory_fts(conn)
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


    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
