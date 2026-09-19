"""Weekly and monthly review persistence."""
from __future__ import annotations

from typing import Any, Callable
import sqlite3


class ReviewsRepository:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_get_conn: Callable[..., Any],
                 dep_now_iso: Callable[..., Any],
                 ) -> None:
        self._dep_get_conn = dep_get_conn
        self._dep_now_iso = dep_now_iso

    def get_weekly_review(self, week_start: str) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, week_start, markdown_content, created_at, updated_at
                FROM weekly_reviews
                WHERE week_start = ?
                """,
                (week_start,),
            ).fetchone()


    def list_weekly_reviews(self) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, week_start, markdown_content, created_at, updated_at
                FROM weekly_reviews
                ORDER BY week_start DESC
                """
            ).fetchall()


    def upsert_weekly_review(self, week_start: str, markdown_content: str) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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


    def get_monthly_review(self, month: str) -> sqlite3.Row | None:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, month, markdown_content, created_at, updated_at
                FROM monthly_reviews
                WHERE month = ?
                """,
                (month,),
            ).fetchone()


    def list_monthly_reviews(self) -> list[sqlite3.Row]:
        with self._dep_get_conn() as conn:
            return conn.execute(
                """
                SELECT id, month, markdown_content, created_at, updated_at
                FROM monthly_reviews
                ORDER BY month DESC
                """
            ).fetchall()


    def upsert_monthly_review(self, month: str, markdown_content: str) -> None:
        timestamp = self._dep_now_iso()
        with self._dep_get_conn() as conn:
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
