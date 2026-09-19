"""Agent runs, steps, observations and execution receipts."""
from __future__ import annotations

from typing import Any, Callable
import json
import sqlite3


class AgentRunsRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_get_conn: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 dep_today_string: Callable[..., Any],
                 ) -> None:
        self._dep_get_conn = dep_get_conn
        self._dep_now_iso = dep_now_iso
        self._dep_today_string = dep_today_string

    def create_agent_run(self,
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
        timestamp = self._dep_now_iso()
        clean_run_id = str(run_id or "").strip()[:80]
        clean_request_id = str(request_id or "").strip()[:80]
        if not clean_run_id or not clean_request_id:
            raise ValueError("Agent run ID 和请求 ID 不能为空。")
        with self._dep_get_conn() as conn:
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
                    max(1, min(200, int(max_steps))),
                    max(1, min(24, int(max_model_calls))),
                    max(0, min(100, int(max_tool_calls))),
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


    def get_agent_run(self, run_id: str) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (str(run_id or "").strip()[:80],),
            ).fetchone()


    def get_agent_run_by_request(self, request_id: str) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                "SELECT * FROM agent_runs WHERE request_id = ?",
                (str(request_id or "").strip()[:80],),
            ).fetchone()


    def get_agent_runs_by_requests(self, request_ids: list[str]) -> list[sqlite3.Row]:
        normalized = list(dict.fromkeys(
            str(request_id or "").strip()[:80]
            for request_id in request_ids
            if str(request_id or "").strip()
        ))[:500]
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        with self._dep_get_conn() as conn:
            return conn.execute(
                f"SELECT * FROM agent_runs WHERE request_id IN ({placeholders})",
                tuple(normalized),
            ).fetchall()


    def save_model_route_observation(self,
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

        with self._dep_get_conn() as conn:
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
                    self._dep_now_iso(),
                ),
            )
            return int(cursor.lastrowid)


    def list_model_route_observations(self,
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
        with self._dep_get_conn() as conn:
            return conn.execute(
                f"""
                SELECT * FROM model_route_observations
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()


    def relink_agent_run_source_message(self, run_id: str, source_message_id: int) -> None:
        clean_run_id = str(run_id or "").strip()[:80]
        clean_message_id = max(0, int(source_message_id or 0))
        if not clean_run_id or clean_message_id <= 0:
            raise ValueError("Agent run ID 和来源消息 ID 不能为空。")
        with self._dep_get_conn() as conn:
            conn.execute(
                "UPDATE agent_runs SET source_message_id = ?, updated_at = ? WHERE run_id = ?",
                (clean_message_id, self._dep_now_iso(), clean_run_id),
            )
            conn.execute(
                "UPDATE companion_actions SET source_message_id = ? WHERE agent_run_id = ?",
                (clean_message_id, clean_run_id),
            )


    def update_agent_run(self,
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
        parameters: list[object] = [str(status or "running")[:40], self._dep_now_iso()]
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
            parameters.append(str(value) if column.endswith("_json") else str(value)[:max_chars] if max_chars is not None else max(0, int(value)))
        if terminal:
            assignments.append("finished_at = ?")
            parameters.append(self._dep_now_iso())
        parameters.append(str(run_id or "").strip()[:80])
        with self._dep_get_conn() as conn:
            conn.execute(
                f"UPDATE agent_runs SET {', '.join(assignments)} WHERE run_id = ?",
                parameters,
            )


    def list_agent_runs(self, limit: int = 100, conversation_id: str = "") -> list[sqlite3.Row]:
        conditions = ""
        parameters: list[object] = []
        if conversation_id:
            conditions = "WHERE conversation_id = ?"
            parameters.append(str(conversation_id)[:160])
        parameters.append(max(1, min(int(limit), 500)))
        with self._dep_get_conn() as conn:
            return conn.execute(
                f"""
                SELECT * FROM agent_runs
                {conditions}
                ORDER BY created_at DESC, run_id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()


    def claim_agent_run_step(self,
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
        timestamp = self._dep_now_iso()
        clean_key = str(idempotency_key or "").strip()[:160]
        if not clean_key:
            raise ValueError("Agent 步骤幂等键不能为空。")
        with self._dep_get_conn() as conn:
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
                    str(arguments_json or "{}"),
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


    def update_agent_run_step(self,
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
        parameters: list[object] = [str(status or "running")[:40], self._dep_now_iso()]
        if str(status) == "running":
            assignments.append("started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END")
            parameters.append(self._dep_now_iso())
        if result_json is not None:
            assignments.append("result_json = ?")
            parameters.append(str(result_json or "{}"))
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
            parameters.append(self._dep_now_iso())
        parameters.append(int(step_id))
        with self._dep_get_conn() as conn:
            conn.execute(
                f"UPDATE agent_run_steps SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )


    def list_agent_run_steps(self, run_id: str) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT * FROM agent_run_steps
                WHERE run_id = ?
                ORDER BY step_index ASC, id ASC
                """,
                (str(run_id or "")[:80],),
            ).fetchall()


    def list_agent_run_steps_many(self, run_ids: list[str]) -> list[sqlite3.Row]:
        normalized = list(dict.fromkeys(
            str(run_id or "").strip()[:80]
            for run_id in run_ids
            if str(run_id or "").strip()
        ))[:500]
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        with self._dep_get_conn() as conn:
            return conn.execute(
                f"""
                SELECT * FROM agent_run_steps
                WHERE run_id IN ({placeholders})
                ORDER BY run_id ASC, step_index ASC, id ASC
                """,
                tuple(normalized),
            ).fetchall()


    def get_agent_run_step(self, step_id: int) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                "SELECT * FROM agent_run_steps WHERE id = ?",
                (int(step_id),),
            ).fetchone()


    def start_tool_execution_receipt(self,
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
        with self._dep_get_conn() as conn:
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
                    self._dep_now_iso(),
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


    def finish_tool_execution_receipt(self, receipt_id: int, status: str, result: str = "") -> None:
        with self._dep_get_conn() as conn:
            conn.execute(
                """
                UPDATE tool_execution_receipts
                SET status = ?, result = ?, finished_at = ?
                WHERE id = ?
                """,
                (str(status)[:40], str(result or "")[:1000], self._dep_now_iso(), int(receipt_id)),
            )


    def list_tool_execution_receipts(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
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


    def log_companion_action(self,
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
        with self._dep_get_conn() as conn:
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
                    self._dep_today_string(),
                    conversation_id,
                    action_type[:80],
                    payload_json,
                    status[:40],
                    result[:1000],
                    int(source_message_id or 0),
                    1 if requires_confirmation else 0,
                    self._dep_now_iso(),
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


    def update_companion_action(self,
        action_id: int,
        status: str,
        result: str = "",
        *,
        approved: bool = False,
    ) -> None:
        timestamp = self._dep_now_iso()
        terminal = status in {"executed", "failed", "skipped", "cancelled"}
        with self._dep_get_conn() as conn:
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


    def list_companion_actions(self, limit: int = 100, conversation_id: str = "") -> list[sqlite3.Row]:
        bounded_limit = max(1, min(500, int(limit)))
        conditions = ""
        parameters: list[object] = []
        if conversation_id:
            conditions = "WHERE conversation_id = ?"
            parameters.append(conversation_id)
        parameters.append(bounded_limit)
        with self._dep_get_conn() as conn:
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


    def get_companion_action(self, action_id: int) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
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
