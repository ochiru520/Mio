"""Screen sessions, observations and reaction persistence."""
from __future__ import annotations

from typing import Any, Callable
from datetime import datetime
import json
import sqlite3
from datetime import timedelta


class ObservationsRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep__local_timezone: Callable[..., Any],
                 dep_get_conn: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 ) -> None:
        self._dep__local_timezone = dep__local_timezone
        self._dep_get_conn = dep_get_conn
        self._dep_now_iso = dep_now_iso

    def _screen_session_is_recent(self, last_activity_at: str, current_at: str, *, minutes: int = 30) -> bool:
        try:
            last_activity = datetime.fromisoformat(last_activity_at)
            current = datetime.fromisoformat(current_at)
        except (TypeError, ValueError):
            return False
        local_timezone = self._dep__local_timezone()
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=local_timezone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=local_timezone)
        last_activity = last_activity.astimezone(local_timezone)
        current = current.astimezone(local_timezone)
        return timedelta(0) <= current - last_activity <= timedelta(minutes=max(1, minutes))


    def start_screen_session(self, mode: str, title: str, vision_model: str = "") -> int:
        timestamp = self._dep_now_iso()
        normalized_mode = "window" if mode == "window" else "screen"
        normalized_title = str(title or "")[:300]
        with self._dep_get_conn() as conn:
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
                and self._screen_session_is_recent(str(current["last_activity_at"] or ""), timestamp)
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
                and self._screen_session_is_recent(
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


    def end_screen_sessions(self) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
            conn.execute(
                "UPDATE game_sessions SET status = 'ended', ended_at = ? WHERE status = 'active'",
                (timestamp,),
            )


    def cleanup_screen_observation_history(self,
        *,
        retention_days: int = 30,
        max_rows_per_table: int = 20000,
        current_at: str | None = None,
    ) -> dict[str, int]:
        """Trim disposable screen-observation telemetry without touching chats or diaries."""
        current = datetime.fromisoformat(current_at or self._dep_now_iso())
        if current.tzinfo is None:
            current = current.replace(tzinfo=self._dep__local_timezone())
        cutoff = (current - timedelta(days=max(1, int(retention_days)))).isoformat(timespec="seconds")
        row_limit = max(1000, int(max_rows_per_table))
        deleted: dict[str, int] = {}

        with self._dep_get_conn() as conn:
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


    def save_screen_event(self,
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
        with self._dep_get_conn() as conn:
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
                    str(occurred_at or self._dep_now_iso()),
                    self._dep_now_iso(),
                ),
            )
            event_id = int(cursor.lastrowid)
            normalized_importance = max(0.0, min(1.0, float(importance or 0)))
            if should_speak or normalized_importance >= 0.7:
                timestamp = self._dep_now_iso()
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


    def save_observation(self,
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
        with self._dep_get_conn() as conn:
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
                    str(occurred_at or self._dep_now_iso()),
                    self._dep_now_iso(),
                ),
            )
            return int(cursor.lastrowid)


    def recent_observations(self, session_id: int, limit: int = 8) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
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


    def get_game_session_state(self, session_id: int) -> dict[str, object]:
        with self._dep_get_conn() as conn:
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


    def upsert_game_session_state(self, session_id: int, game_name: str, state: dict[str, object]) -> None:
        timestamp = self._dep_now_iso()
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))[:12000]
        with self._dep_get_conn() as conn:
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


    def recent_screen_event_summaries(self, session_id: int, limit: int = 8) -> list[str]:
        with self._dep_get_conn() as conn:
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


    def save_companion_reaction(self,
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
        with self._dep_get_conn() as conn:
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
                    self._dep_now_iso(),
                ),
            )
            return int(cursor.lastrowid)
