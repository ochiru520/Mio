from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .llm import call_chat_completion
from .prompts import build_weekly_review_messages
from .routes.onebot import send_private_message


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeeklyReviewResult:
    week_start: str
    week_end: str
    markdown_content: str
    created: bool


def _now() -> datetime:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(tz)


def week_start_for(day: date_cls) -> date_cls:
    return day - timedelta(days=day.weekday())


def last_completed_week_start(current_logical_day: date_cls) -> str:
    return (week_start_for(current_logical_day) - timedelta(days=7)).isoformat()


def week_end_for(week_start: str) -> str:
    return (date_cls.fromisoformat(week_start) + timedelta(days=6)).isoformat()


def _compact(text: object, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _build_day_sections(week_start: str) -> tuple[str, int]:
    start = date_cls.fromisoformat(week_start)
    sections: list[str] = []
    diary_count = 0
    weekday_labels = "一二三四五六日"
    for offset in range(7):
        day = (start + timedelta(days=offset)).isoformat()
        diary = db.get_diary(day)
        state = db.get_daily_state(day)
        review = db.get_daily_review(day)
        if diary is None and state is None and review is None:
            continue

        lines = [f"### {day}（周{weekday_labels[offset]}）"]
        if state is not None:
            lines.append(
                f"- 状态板：每日三十={state['daily_thirty_status']}；"
                f"情绪={state['mood'] or '未确认'}（{state['mood_score'] or '无'}分）；"
                f"事件={_compact(state['key_events'], 120) or '未确认'}"
            )
        if diary is not None:
            diary_count += 1
            lines.append(f"- 日记：{_compact(diary['markdown_content'], 600)}")
        if review is not None:
            lines.append(f"- AI 回顾：{_compact(review['markdown_content'], 260)}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections), diary_count


async def generate_weekly_review(week_start: str, overwrite: bool = True) -> WeeklyReviewResult:
    week_end = week_end_for(week_start)
    existing = db.get_weekly_review(week_start)
    if existing is not None and not overwrite:
        return WeeklyReviewResult(
            week_start=week_start,
            week_end=week_end,
            markdown_content=str(existing["markdown_content"]),
            created=False,
        )

    day_sections, diary_count = _build_day_sections(week_start)
    if diary_count == 0:
        raise ValueError("这一周没有任何日记，先积累几天再复盘。")

    messages = build_weekly_review_messages(week_start, week_end, day_sections)
    markdown_content = await call_chat_completion(messages, temperature=0.35)
    markdown_content = markdown_content.replace("**", "").strip()
    db.upsert_weekly_review(week_start, markdown_content)
    return WeeklyReviewResult(
        week_start=week_start,
        week_end=week_end,
        markdown_content=markdown_content,
        created=True,
    )


async def _notify_weekly_review_ready(week_start: str) -> None:
    if not settings.weekly_review_notify_qq or not settings.qq_bot_enabled:
        return
    for user_id in settings.qq_allowed_user_ids:
        message = f"上周（{week_start} 起）的周复盘写好了，网站上可以看。"
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


async def run_weekly_review_once(now: datetime | None = None) -> int:
    if not settings.weekly_review_enabled:
        return 0
    current = now or _now()
    if current.hour < settings.weekly_review_hour:
        return 0

    logical_today = date_cls.fromisoformat(db.today_string(current))
    week_start = last_completed_week_start(logical_today)
    if db.get_weekly_review(week_start) is not None:
        return 0

    _, diary_count = _build_day_sections(week_start)
    if diary_count == 0:
        return 0

    result = await generate_weekly_review(week_start, overwrite=False)
    if result.created:
        await _notify_weekly_review_ready(week_start)
        return 1
    return 0


async def weekly_review_loop() -> None:
    await asyncio.sleep(40)
    while True:
        try:
            await run_weekly_review_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("自动每周回顾检查失败")
        await asyncio.sleep(max(600, settings.weekly_review_check_seconds))
