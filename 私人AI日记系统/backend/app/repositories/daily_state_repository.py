"""Daily state and diary source-material persistence."""
from __future__ import annotations

from typing import Any, Callable
import sqlite3


class DailyStateRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_get_conn: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 dep_today_string: Callable[..., Any],
                 ) -> None:
        self._dep_get_conn = dep_get_conn
        self._dep_now_iso = dep_now_iso
        self._dep_today_string = dep_today_string

    def add_diary_material(self, content: str, date: str | None = None, source: str = "web") -> int:
        target_date = date or self._dep_today_string()
        try:
            with self._dep_get_conn() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO diary_materials (date, content, source, used_in_diary, created_at)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (target_date, content, source, self._dep_now_iso()),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            # 唯一索引 (date, content) 兜底：同一天相同内容已在库中，回查返回已有 id。
            with self._dep_get_conn() as conn:
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


    def add_diary_material_once(self, content: str, date: str | None = None, source: str = "auto") -> int:
        target_date = date or self._dep_today_string()
        normalized = " ".join(content.split()).strip()[:1000]
        if not normalized:
            raise ValueError("日记素材不能为空。")
        with self._dep_get_conn() as conn:
            # 唯一索引 (date, content) 幂等：并发重复插入由唯一约束兜底，随后回查返回已有 id。
            conn.execute(
                """
                INSERT OR IGNORE INTO diary_materials (date, content, source, used_in_diary, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (target_date, normalized, source, self._dep_now_iso()),
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


    def list_diary_materials(self, date: str | None = None) -> list[sqlite3.Row]:
        target_date = date or self._dep_today_string()
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, date, content, source, used_in_diary, created_at
                FROM diary_materials
                WHERE date = ?
                ORDER BY id ASC
                """,
                (target_date,),
            ).fetchall()


    def get_daily_state(self, date: str | None = None) -> sqlite3.Row | None:
        target_date = date or self._dep_today_string()
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, date, daily_thirty_status, daily_thirty_reason, mood, mood_score,
                       key_events, avoidance_signals, next_min_action, created_at, updated_at
                FROM daily_states
                WHERE date = ?
                """,
                (target_date,),
            ).fetchone()


    def ensure_daily_state_today(self) -> str:
        """跨天后首次对话时确保今天有状态记录（默认未确认），避免把昨天的状态当成今天的。"""
        target_date = self._dep_today_string()
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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


    def list_daily_states_since(self, start_date: str) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
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


    def upsert_daily_state(self,
        date: str,
        daily_thirty_status: str,
        mood: str,
        key_events: str,
        avoidance_signals: str,
        next_min_action: str,
        daily_thirty_reason: str = "",
        mood_score: int = 0,
    ) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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


    def update_daily_thirty(self,
        status: str,
        reason: str,
        date: str | None = None,
    ) -> None:
        if status not in {"done", "partial", "missed", "unknown"}:
            raise ValueError("每日三十状态无效。")
        target_date = date or self._dep_today_string()
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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


    def update_daily_mood(self,
        mood: str,
        date: str | None = None,
        mood_score: int = 0,
    ) -> None:
        target_date = date or self._dep_today_string()
        timestamp = self._dep_now_iso()
        normalized_score = max(0, min(5, int(mood_score or 0)))
        with self._dep_get_conn() as conn:
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


    def update_daily_state_summary(self,
        *,
        date: str | None = None,
        mood: str = "",
        mood_score: int = 0,
        key_events: str = "",
        avoidance_signals: str = "",
        next_min_action: str = "",
    ) -> bool:
        target_date = date or self._dep_today_string()
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

        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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
