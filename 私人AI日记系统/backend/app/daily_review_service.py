from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .review_service import generate_review_for_date
from .routes.onebot import send_private_message


logger = logging.getLogger(__name__)


def _now() -> datetime:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(tz)


def _target_date(now: datetime) -> str:
    logical_today = datetime.fromisoformat(f"{db.today_string(now)}T00:00:00").date()
    return (logical_today - timedelta(days=1)).isoformat()


def _is_after_review_time(now: datetime) -> bool:
    target_minutes = settings.daily_review_auto_hour * 60 + settings.daily_review_auto_minute
    current_minutes = now.hour * 60 + now.minute
    return current_minutes >= target_minutes


async def _notify_review_ready(date: str) -> None:
    if not settings.daily_review_auto_notify_qq:
        return
    if not settings.qq_bot_enabled:
        return
    for user_id in settings.qq_allowed_user_ids:
        message = f"昨天的日记回顾写好了。\n{date} 的。"
        if await send_private_message(user_id, message):
            db.save_message(
                "assistant",
                message,
                source="qq",
                conversation_id=f"qq_private_{user_id}",
                request_cost_yuan=0.0,
                request_cost_source="local_fallback",
            )
            if settings.qq_reply_delay_seconds > 0:
                await asyncio.sleep(settings.qq_reply_delay_seconds)


async def run_daily_review_once(now: datetime | None = None) -> int:
    current = now or _now()
    if not settings.daily_review_auto_enabled:
        return 0
    if not _is_after_review_time(current):
        return 0

    date = _target_date(current)
    diary = db.get_diary(date)
    if diary is None:
        return 0
    if not str(diary["confirmed_at"] or "").strip():
        return 0
    if db.get_daily_review(date) is not None:
        return 0

    result = await generate_review_for_date(date, overwrite=False)
    if result.created:
        await _notify_review_ready(date)
        return 1
    return 0


async def daily_review_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await run_daily_review_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("自动每日回顾检查失败")
        await asyncio.sleep(max(60, settings.daily_review_check_seconds))
