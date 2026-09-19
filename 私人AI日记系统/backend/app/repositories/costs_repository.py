"""Usage accounting and reconciliation persistence."""
from __future__ import annotations

from typing import Any, Callable
from datetime import datetime
import sqlite3
from datetime import timedelta


class CostsRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_get_conn: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 dep_today_string: Callable[..., Any],
                 ) -> None:
        self._dep_get_conn = dep_get_conn
        self._dep_now_iso = dep_now_iso
        self._dep_today_string = dep_today_string

    def enqueue_cost_reconciliation(self,
        *,
        local_request_id: str,
        conversation_id: str,
        provider_request_id: str,
        profile_id: str,
        base_url: str,
        estimated_cost_yuan: float | None,
        estimated_cost_source: str,
    ) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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
        self.refresh_reconciled_message_cost(local_request_id)


    def list_due_cost_reconciliation_jobs(self, limit: int = 1) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT * FROM cost_reconciliation_jobs
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at ASC, id ASC
                LIMIT ?
                """,
                (self._dep_now_iso(), max(1, min(int(limit), 20))),
            ).fetchall()


    def resolve_cost_reconciliation_job(self, job_id: int, cost_yuan: float) -> str:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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
        self.refresh_reconciled_message_cost(local_request_id)
        return local_request_id


    def retry_cost_reconciliation_job(self,
        job_id: int,
        *,
        delay_seconds: int,
        last_error: str,
        max_attempts: int = 6,
    ) -> str:
        timestamp = datetime.fromisoformat(self._dep_now_iso())
        with self._dep_get_conn() as conn:
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
                (attempts, status, next_attempt, str(last_error or "")[:300], self._dep_now_iso(), int(job_id)),
            )
            local_request_id = str(row["local_request_id"])
        self.refresh_reconciled_message_cost(local_request_id)
        return local_request_id


    def refresh_reconciled_message_cost(self, local_request_id: str) -> None:
        if not local_request_id:
            return
        with self._dep_get_conn() as conn:
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
                        self._dep_now_iso(),
                        local_request_id,
                    ),
                )


    def record_screen_analysis_usage(self,
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
        target_date = date or self._dep_today_string()
        timestamp = self._dep_now_iso()
        priced = cost_yuan is not None
        normalized_cost = max(0.0, float(cost_yuan or 0.0))
        with self._dep_get_conn() as conn:
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


    def get_screen_analysis_usage(self, date: str | None = None) -> dict[str, int | float | str]:
        target_date = date or self._dep_today_string()
        with self._dep_get_conn() as conn:
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


    def get_screen_analysis_costs_since(self, started_at: str) -> dict[str, int | float]:
        with self._dep_get_conn() as conn:
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
