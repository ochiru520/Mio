from __future__ import annotations

import asyncio
import logging
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
    }


def _record_check(target_date: str, result: str, error: str = "") -> None:
    _last_check.update(
        checked_at=_now().isoformat(timespec="seconds"),
        target_date=target_date,
        result=result,
        error=error,
    )


async def run_daily_diary_once(now: datetime | None = None) -> int:
    current = now or _now()
    date = _target_date(current)
    if not settings.daily_diary_auto_enabled:
        _record_check(date, "disabled")
        return 0

    if db.get_diary(date) is not None:
        _record_check(date, "already_exists")
        return 0
    if not db.get_today_messages(date) and not db.list_diary_materials(date):
        _record_check(date, "no_content")
        return 0

    try:
        result = await generate_diary_for_date_payload(date, overwrite=False)
    except Exception as exc:
        _record_check(date, "error", str(exc))
        raise

    if result.get("skipped"):
        _record_check(date, "skipped")
        return 0
    _record_check(date, "generated")
    logger.info("自动生成日记完成：%s", date)
    return 1


async def daily_diary_loop() -> None:
    while True:
        try:
            await run_daily_diary_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("自动日记检查失败")
        await asyncio.sleep(max(30, settings.daily_diary_check_seconds))
