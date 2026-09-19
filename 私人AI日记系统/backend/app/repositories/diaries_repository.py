"""Diary storage, export metadata and statistics."""
from __future__ import annotations

from typing import Any, Callable
from datetime import datetime
import sqlite3
from datetime import timedelta


class DiariesRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_get_conn: Callable[..., Any],
                 dep_logical_day_bounds: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 dep_today_string: Callable[..., Any],
                 ) -> None:
        self._dep_get_conn = dep_get_conn
        self._dep_logical_day_bounds = dep_logical_day_bounds
        self._dep_now_iso = dep_now_iso
        self._dep_today_string = dep_today_string

    def get_day_summary(self, date: str | None = None) -> dict[str, object]:
        target_date = date or self._dep_today_string()
        start_at, end_at = self._dep_logical_day_bounds(target_date)
        with self._dep_get_conn() as conn:
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


    def upsert_diary(self,
        date: str,
        title: str,
        markdown_content: str,
        mood_tags: str = "",
        daily_thirty_status: str = "unknown",
        confirmed_at: str | None = None,
    ) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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


    def list_diaries(self) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, date, title, markdown_content, mood_tags, daily_thirty_status,
                       confirmed_at, created_at, updated_at
                FROM diaries
                ORDER BY date DESC
                """
            ).fetchall()


    def search_diaries(self, query: str = "") -> list[sqlite3.Row]:
        keyword = query.strip()
        if not keyword:
            return self.list_diaries()

        pattern = f"%{keyword}%"
        with self._dep_get_conn() as conn:
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


    def get_diary(self, date: str) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, date, title, markdown_content, mood_tags, daily_thirty_status,
                       confirmed_at, created_at, updated_at
                FROM diaries
                WHERE date = ?
                """,
                (date,),
            ).fetchone()


    def list_diaries_since(self, start_date: str) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
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


    def list_diary_exports(self) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT date, title, markdown_content, updated_at
                FROM diaries
                ORDER BY date ASC
                """
            ).fetchall()


    def get_daily_review(self, date: str) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, date, markdown_content, created_at, updated_at
                FROM daily_reviews
                WHERE date = ?
                """,
                (date,),
            ).fetchone()


    def list_daily_reviews_since(self, start_date: str) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, date, markdown_content, created_at, updated_at
                FROM daily_reviews
                WHERE date >= ?
                ORDER BY date ASC
                """,
                (start_date,),
            ).fetchall()


    def upsert_daily_review(self, date: str, markdown_content: str) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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


    def set_diary_confirmed(self, date: str, confirmed: bool = True) -> str | None:
        timestamp = self._dep_now_iso()
        confirmed_at = timestamp if confirmed else ""
        with self._dep_get_conn() as conn:
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


    def list_reviews(self) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT date, markdown_content, created_at, updated_at
                FROM daily_reviews
                ORDER BY date DESC
                """
            ).fetchall()


    def get_diary_stats(self) -> dict:
        with self._dep_get_conn() as conn:
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
        cursor_date = datetime.fromisoformat(self._dep_today_string()).date()
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


    def get_calendar_data(self, year: int, month: int) -> list[dict]:
        prefix = f"{year:04d}-{month:02d}-"
        with self._dep_get_conn() as conn:
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


    def delete_diary(self, date: str) -> bool:
        with self._dep_get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM diaries WHERE date = ?",
                (date,),
            )
        return cursor.rowcount > 0


    def search_all(self, query: str) -> dict:
        keyword = query.strip()
        pattern = f"%{keyword}%"
        with self._dep_get_conn() as conn:
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


    def mark_materials_used(self, date: str) -> None:
        with self._dep_get_conn() as conn:
            conn.execute(
                """
                UPDATE diary_materials
                SET used_in_diary = 1
                WHERE date = ?
                """,
                (date,),
            )
