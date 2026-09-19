"""Autonomous goals, policies and scheduled events."""
from __future__ import annotations

from typing import Any, Callable
import json
import sqlite3


class AutonomyRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_get_conn: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 ) -> None:
        self._dep_get_conn = dep_get_conn
        self._dep_now_iso = dep_now_iso

    def get_qq_proactive_state(self, user_id: str) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT user_id, last_user_message_at, next_prompt_at, last_prompt_at, updated_at
                FROM qq_proactive_states
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()


    def upsert_qq_proactive_state(self,
        user_id: str,
        last_user_message_at: str,
        next_prompt_at: str,
        last_prompt_at: str = "",
    ) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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


    def record_proactive_topic(self,
        conversation_id: str,
        topic_key: str,
        topic_kind: str,
        topic_text: str,
        score: float,
    ) -> int:
        with self._dep_get_conn() as conn:
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
                    self._dep_now_iso(),
                ),
            )
            return int(cursor.lastrowid)


    def list_recent_proactive_topics(self, conversation_id: str, limit: int = 12) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
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


    def get_autonomy_policy(self) -> sqlite3.Row:
        with self._dep_get_conn() as conn:
            row = conn.execute("SELECT * FROM autonomy_policies WHERE id = 1").fetchone()
            if row is None:
                timestamp = self._dep_now_iso()
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


    def update_autonomy_policy(self, values: dict[str, object]) -> sqlite3.Row:
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
            changes["updated_at"] = self._dep_now_iso()
            assignments = ", ".join(f"{key} = ?" for key in changes)
            with self._dep_get_conn() as conn:
                conn.execute(
                    f"UPDATE autonomy_policies SET {assignments} WHERE id = 1",
                    tuple(changes.values()),
                )
        return self.get_autonomy_policy()


    def create_agent_goal(self,
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
        timestamp = self._dep_now_iso()
        normalized_ref = str(source_ref or "")[:160]
        with self._dep_get_conn() as conn:
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


    def get_agent_goal(self, goal_id: int) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute("SELECT * FROM agent_goals WHERE id = ?", (int(goal_id),)).fetchone()


    def list_agent_goals(self, limit: int = 100, status: str = "") -> list[sqlite3.Row]:
        where = "WHERE status = ?" if status else ""
        params: tuple[object, ...] = (str(status),) if status else ()
        with self._dep_get_conn() as conn:
            return conn.execute(
                f"SELECT * FROM agent_goals {where} ORDER BY updated_at DESC, id DESC LIMIT ?",
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()


    def update_agent_goal_status(self, goal_id: int, status: str) -> bool:
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                "UPDATE agent_goals SET status = ?, updated_at = ? WHERE id = ?",
                (str(status)[:40], self._dep_now_iso(), int(goal_id)),
            )
            return cursor.rowcount > 0


    def record_agent_event(self,
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
        timestamp = self._dep_now_iso()
        occurred = str(occurred_at or timestamp)
        available = str(available_at or occurred)
        with self._dep_get_conn() as conn:
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
                    json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")),
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


    def claim_next_agent_event(self,
        current_iso: str,
        stale_before_iso: str,
        claim_token: str,
    ) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
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
            timestamp = self._dep_now_iso()
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


    def finish_agent_event(self,
        event_id: int,
        status: str,
        *,
        reason: str = "",
        error: str = "",
    ) -> bool:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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


    def reschedule_agent_event(self, event_id: int, available_at: str, reason: str) -> bool:
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_events
                SET status = 'pending', available_at = ?, decision_reason = ?,
                    claim_token = '', claimed_at = '', updated_at = ?
                WHERE id = ?
                """,
                (str(available_at), str(reason)[:2000], self._dep_now_iso(), int(event_id)),
            )
            return cursor.rowcount > 0


    def list_agent_events(self, limit: int = 100, status: str = "") -> list[sqlite3.Row]:
        where = "WHERE status = ?" if status else ""
        params: tuple[object, ...] = (str(status),) if status else ()
        with self._dep_get_conn() as conn:
            return conn.execute(
                f"SELECT * FROM agent_events {where} ORDER BY id DESC LIMIT ?",
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()


    def create_autonomy_behavior(self,
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
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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
                    json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
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


    def get_autonomy_behavior(self, behavior_id: int) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                "SELECT * FROM autonomy_behaviors WHERE id = ?", (int(behavior_id),)
            ).fetchone()


    def get_autonomy_behavior_by_key(self, behavior_key: str) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                "SELECT * FROM autonomy_behaviors WHERE behavior_key = ?",
                (str(behavior_key)[:240],),
            ).fetchone()


    def update_autonomy_behavior(self, behavior_id: int, values: dict[str, object]) -> bool:
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
        changes["updated_at"] = self._dep_now_iso()
        assignments = ", ".join(f"{key} = ?" for key in changes)
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE autonomy_behaviors SET {assignments} WHERE id = ?",
                (*changes.values(), int(behavior_id)),
            )
            return cursor.rowcount > 0


    def list_autonomy_behaviors(self, limit: int = 100, status: str = "") -> list[sqlite3.Row]:
        where = "WHERE status = ?" if status else ""
        params: tuple[object, ...] = (str(status),) if status else ()
        with self._dep_get_conn() as conn:
            return conn.execute(
                f"SELECT * FROM autonomy_behaviors {where} ORDER BY id DESC LIMIT ?",
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()


    def autonomy_usage_between(self, started_at: str, ended_at: str) -> dict[str, object]:
        with self._dep_get_conn() as conn:
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
