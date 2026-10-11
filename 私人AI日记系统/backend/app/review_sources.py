"""Versioned source snapshots for daily, weekly, and monthly reviews."""
from __future__ import annotations

import hashlib
import json
from datetime import date as date_cls, timedelta
from typing import Any

from . import db


def _ensure_table() -> None:
    with db.get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_source_snapshots (
                review_kind TEXT NOT NULL,
                review_key TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                source_json TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                PRIMARY KEY (review_kind, review_key)
            )
            """
        )


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _content_hash(value: Any) -> str:
    return _hash(str(value or ""))


def _row(value: Any, fields: tuple[str, ...], *, content_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if value is None:
        return None
    result = {field: value[field] for field in fields if field in value.keys()}
    for field in content_fields:
        if field in value.keys():
            result[f"{field}_hash"] = _content_hash(value[field])
            result.pop(field, None)
    return result


def _day_payload(day: str, *, include_daily_review: bool = False) -> dict[str, Any]:
    diary = db.get_diary(day)
    state = db.get_daily_state(day)
    materials = db.list_diary_materials(day)
    messages = db.get_today_messages(day)
    payload: dict[str, Any] = {
        "date": day,
        "diary": _row(
            diary,
            ("id", "date", "updated_at", "confirmed_at", "title", "markdown_content", "daily_thirty_status"),
            content_fields=("title", "markdown_content"),
        ),
        "state": _row(
            state,
            ("id", "date", "updated_at", "daily_thirty_status", "daily_thirty_reason", "mood", "mood_score", "key_events", "avoidance_signals", "next_min_action"),
            content_fields=("daily_thirty_reason", "mood", "key_events", "avoidance_signals", "next_min_action"),
        ),
        "materials": [
            _row(material, ("id", "date", "source", "created_at", "used_in_diary"), content_fields=("content",))
            for material in materials
        ],
        "messages": [
            _row(message, ("id", "role", "source", "conversation_id", "created_at"), content_fields=("content",))
            for message in messages
        ],
    }
    if include_daily_review:
        review = db.get_daily_review(day)
        payload["daily_review"] = _row(review, ("id", "date", "updated_at"), content_fields=("markdown_content",))
    return payload


def daily_payload(day: str) -> dict[str, Any]:
    return _day_payload(day)


def period_payload(start: str, end: str) -> dict[str, Any]:
    current = date_cls.fromisoformat(start)
    last = date_cls.fromisoformat(end)
    days: list[dict[str, Any]] = []
    while current <= last:
        days.append(_day_payload(current.isoformat(), include_daily_review=True))
        current += timedelta(days=1)
    return {"start": start, "end": end, "days": days}


def save_snapshot(kind: str, key: str, payload: dict[str, Any], generated_at: str | None = None) -> dict[str, Any]:
    _ensure_table()
    generated = generated_at or db.now_iso()
    source_hash = _hash(payload)
    source_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO review_source_snapshots(review_kind, review_key, source_hash, source_json, generated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(review_kind, review_key) DO UPDATE SET
                source_hash=excluded.source_hash, source_json=excluded.source_json, generated_at=excluded.generated_at
            """,
            (kind, key, source_hash, source_json, generated),
        )
    return {"source_hash": source_hash, "source_generated_at": generated, "source_status": "current", "stale": False}


def status(kind: str, key: str, current_payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_table()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT source_hash, source_json, generated_at FROM review_source_snapshots WHERE review_kind=? AND review_key=?",
            (kind, key),
        ).fetchone()
    if row is None:
        return {
            "source_hash": "",
            "source_generated_at": "",
            "source_status": "unknown",
            "stale": True,
            "stale_reason": "此回顾生成时未保存来源快照，无法确认输入是否变化。",
        }
    current_hash = _hash(current_payload)
    stale = current_hash != row["source_hash"]
    return {
        "source_hash": row["source_hash"],
        "current_source_hash": current_hash,
        "source_generated_at": row["generated_at"],
        "source_status": "stale" if stale else "current",
        "stale": stale,
        "stale_reason": "来源日记、状态、素材或聊天记录已变化。" if stale else "",
    }


def metadata(kind: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = status(kind, key, payload)
    result["source_dates"] = [day["date"] for day in payload.get("days", [payload]) if day.get("date")]
    return result
