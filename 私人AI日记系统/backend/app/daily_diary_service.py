from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .routes.diary import generate_diary_for_date_payload


logger = logging.getLogger(__name__)

_last_check: dict[str, object] = {
    "checked_at": "",
    "target_date": "",
    "result": "not_checked",
    "error": "",
}
_active: asyncio.Task | None = None
_retry_at = 0.0
_failures = 0
_retry_signature: tuple = ()


def _configuration_signature() -> tuple:
    # A saved model/key change should release backoff immediately. Never expose keys.
    path = settings.model_profiles_path
    stamp = path.stat().st_mtime_ns if path.exists() else 0
    from .model_runtime import model_policy
    return (str(settings.db_path), settings.daily_diary_model_id, stamp,
            json.dumps(model_policy("record"), sort_keys=True))


def _missing_dates(current: datetime) -> list[str]:
    """Discover source days, preserving timezone and the configured day boundary."""
    cutoff = db.today_string(current)
    with db.get_conn() as conn:
        existing = {row[0] for row in conn.execute("SELECT date FROM diaries")}
        dates = {row[0] for row in conn.execute("SELECT DISTINCT date FROM diary_materials WHERE date < ?", (cutoff,))}
        # Only timestamps are read. Process the cursor incrementally, not chat contents.
        for row in conn.execute("SELECT created_at FROM messages WHERE conversation_id NOT LIKE 'qq_group_%'"):
            try:
                date = db.logical_date_for_datetime(datetime.fromisoformat(row[0]))
            except (ValueError, TypeError):
                continue
            if date < cutoff:
                dates.add(date)
    return sorted(dates - existing)


def _date_attempts(signature: tuple) -> tuple[str, dict[str, dict]]:
    key = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
    with closing(db.get_conn()) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_diary_attempts (
            date TEXT PRIMARY KEY, configuration_key TEXT NOT NULL,
            failures INTEGER NOT NULL, next_retry_at TEXT NOT NULL, error TEXT NOT NULL
        )""")
        rows = conn.execute("SELECT * FROM daily_diary_attempts WHERE configuration_key=?", (key,)).fetchall()
    return key, {row["date"]: dict(row) for row in rows}


async def cancel_active() -> None:
    if _active is not None and not _active.done():
        _active.cancel()
        await asyncio.gather(_active, return_exceptions=True)


def _now() -> datetime:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(tz)


def _target_date(now: datetime) -> str:
    logical_today = datetime.fromisoformat(f"{db.today_string(now)}T00:00:00").date()
    return (logical_today - timedelta(days=1)).isoformat()


def get_daily_diary_status() -> dict[str, object]:
    return {
        **_last_check,
        "enabled": settings.daily_diary_auto_enabled,
        "check_seconds": settings.daily_diary_check_seconds,
        "model_id": settings.daily_diary_model_id,
        "busy": _active is not None and not _active.done(),
        "retry_after_seconds": max(0, int(_retry_at - time.monotonic())),
    }


def _record_check(target_date: str, result: str, error: str = "") -> None:
    _last_check.update(
        checked_at=_now().isoformat(timespec="seconds"),
        target_date=target_date,
        result=result,
        error=error,
    )


async def run_daily_diary_once(now: datetime | None = None, *, force: bool = False) -> int:
    global _active, _retry_at, _failures, _retry_signature
    from .agent_task_service import automatic_work_paused
    current = now or _now()
    date = _target_date(current)
    if _active is not None:
        return 0
    if automatic_work_paused():
        _record_check(date, "paused")
        return 0
    if not settings.daily_diary_auto_enabled:
        _record_check(date, "disabled")
        return 0

    signature = _configuration_signature()
    if signature != _retry_signature:
        _retry_at, _failures, _retry_signature = 0.0, 0, signature
    dates = _missing_dates(current)
    configuration_key, attempts = _date_attempts(signature)
    _last_check["pending_dates"] = dates
    _last_check["deferred_dates"] = [attempts[day] for day in dates if day in attempts]
    if not dates:
        _record_check(date, "already_exists" if db.get_diary(date) is not None else "no_content")
        return 0
    eligible = [day for day in dates if force or day not in attempts
                or datetime.fromisoformat(attempts[day]["next_retry_at"]) <= current]
    if not eligible:
        attempt = min((attempts[day] for day in dates), key=lambda item: item["next_retry_at"])
        _retry_at = time.monotonic() + max(0, (datetime.fromisoformat(attempt["next_retry_at"]) - current).total_seconds())
        _record_check(attempt["date"], "error", attempt["error"])
        return 0
    # Fresh dates make progress before a date that has already failed repeatedly.
    date = min(eligible, key=lambda day: (day in attempts, day))

    _record_check(date, "generating")
    _active = asyncio.create_task(generate_diary_for_date_payload(date, overwrite=False))
    try:
        result = await _active
    except asyncio.CancelledError:
        _record_check(date, "paused" if automatic_work_paused() else "cancelled")
        if automatic_work_paused():
            return 0
        raise
    except Exception as exc:
        _failures = int(attempts.get(date, {}).get("failures", 0)) + 1
        delay = min(3600, 300 * 2 ** min(_failures - 1, 4))
        error = str(getattr(exc, "detail", str(exc)))[:1000]
        retry_at = (current + timedelta(seconds=delay)).isoformat(timespec="seconds")
        with closing(db.get_conn()) as conn, conn:
            conn.execute("INSERT INTO daily_diary_attempts VALUES(?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET configuration_key=excluded.configuration_key,failures=excluded.failures,next_retry_at=excluded.next_retry_at,error=excluded.error",
                         (date, configuration_key, _failures, retry_at, error))
        _retry_at = time.monotonic() + delay
        _record_check(date, "error", error)
        raise
    finally:
        _active = None

    _retry_at, _failures = 0.0, 0
    with closing(db.get_conn()) as conn, conn:
        conn.execute("DELETE FROM daily_diary_attempts WHERE date=?", (date,))
    _last_check["pending_dates"] = [day for day in dates if day != date]
    if result.get("skipped"):
        _record_check(date, "skipped")
        return 0
    _record_check(date, "generated")
    logger.info("自动生成日记完成：%s", date)
    return 1


_manual_check: asyncio.Task | None = None


def request_check() -> dict[str, object]:
    """Queue a bounded check without holding an HTTP connection during generation."""
    global _manual_check
    if _manual_check is None or _manual_check.done():
        async def check() -> None:
            try:
                await run_daily_diary_once(force=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("手动重试自动日记失败")
        _manual_check = asyncio.create_task(check())
    return {"accepted": True, **get_daily_diary_status()}


async def daily_diary_loop() -> None:
    while True:
        try:
            await run_daily_diary_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("自动日记检查失败")
        await asyncio.sleep(max(30, settings.daily_diary_check_seconds))
