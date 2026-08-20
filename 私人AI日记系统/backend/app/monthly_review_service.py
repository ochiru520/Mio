from __future__ import annotations

import asyncio
import calendar
import logging
import re
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .llm import call_chat_completion
from .prompts import build_monthly_review_messages
from .routes.onebot import send_private_message


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonthlyReviewResult:
    month: str
    month_start: str
    month_end: str
    markdown_content: str
    created: bool


def _now() -> datetime:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(tz)


def normalize_month(month: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", str(month or "")):
        raise ValueError("月份格式应为 YYYY-MM。")
    year, month_number = (int(part) for part in month.split("-", 1))
    if not 1 <= year <= 9999 or not 1 <= month_number <= 12:
        raise ValueError("月份无效。")
    return f"{year:04d}-{month_number:02d}"


def month_bounds(month: str) -> tuple[str, str]:
    normalized = normalize_month(month)
    year, month_number = (int(part) for part in normalized.split("-", 1))
    last_day = calendar.monthrange(year, month_number)[1]
    return f"{normalized}-01", f"{normalized}-{last_day:02d}"


def last_completed_month(current_logical_day: date_cls) -> str:
    previous_day = current_logical_day.replace(day=1) - timedelta(days=1)
    return previous_day.strftime("%Y-%m")


def _compact(text: object, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _build_day_sections(month: str) -> tuple[str, int]:
    month_start, month_end = month_bounds(month)
    start = date_cls.fromisoformat(month_start)
    end = date_cls.fromisoformat(month_end)
    sections: list[str] = []
    diary_count = 0
    current = start
    while current <= end:
        day = current.isoformat()
        diary = db.get_diary(day)
        state = db.get_daily_state(day)
        review = db.get_daily_review(day)
        if diary is not None or state is not None or review is not None:
            lines = [f"### {day}"]
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
                lines.append(f"- AI 回顾：{_compact(review['markdown_content'], 220)}")
            sections.append("\n".join(lines))
        current += timedelta(days=1)
    return "\n\n".join(sections), diary_count


async def generate_monthly_review(month: str, overwrite: bool = True) -> MonthlyReviewResult:
    normalized = normalize_month(month)
    month_start, month_end = month_bounds(normalized)
    existing = db.get_monthly_review(normalized)
    if existing is not None and not overwrite:
        return MonthlyReviewResult(
            month=normalized,
            month_start=month_start,
            month_end=month_end,
            markdown_content=str(existing["markdown_content"]),
            created=False,
        )

    day_sections, diary_count = _build_day_sections(normalized)
    if diary_count == 0:
        raise ValueError("这个月没有任何日记，暂时无法形成月记。")

    messages = build_monthly_review_messages(normalized, month_start, month_end, day_sections)
    markdown_content = await call_chat_completion(messages, temperature=0.3)
    markdown_content = markdown_content.replace("**", "").strip()
    db.upsert_monthly_review(normalized, markdown_content)
    return MonthlyReviewResult(
        month=normalized,
        month_start=month_start,
        month_end=month_end,
        markdown_content=markdown_content,
        created=True,
    )


async def _notify_monthly_review_ready(month: str) -> None:
    if not settings.monthly_review_notify_qq or not settings.qq_bot_enabled:
        return
    for user_id in settings.qq_allowed_user_ids:
        message = f"{month} 的月记写好了，Mio 里可以看。"
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


async def run_monthly_review_once(now: datetime | None = None) -> int:
    if not settings.monthly_review_enabled:
        return 0
    current = now or _now()
    if current.hour < settings.monthly_review_hour:
        return 0

    # 月记按自然月结算，不受每日 4 点等逻辑日界线影响。
    month = last_completed_month(current.date())
    if db.get_monthly_review(month) is not None:
        return 0

    _, diary_count = _build_day_sections(month)
    if diary_count == 0:
        return 0

    result = await generate_monthly_review(month, overwrite=False)
    if result.created:
        await _notify_monthly_review_ready(month)
        return 1
    return 0


async def monthly_review_loop() -> None:
    await asyncio.sleep(55)
    while True:
        try:
            await run_monthly_review_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("自动月记检查失败")
        await asyncio.sleep(max(600, settings.monthly_review_check_seconds))
