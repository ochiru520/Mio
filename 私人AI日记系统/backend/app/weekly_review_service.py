from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .model_runtime import operation, OperationStopped
from .config import settings
from .llm import call_chat_completion
from .prompts import build_weekly_review_messages
from .review_sources import period_payload, save_snapshot
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


def _split_review_context(text: str, *, chunk_chars: int = 6000) -> list[str]:
    blocks = re.split(r"(?=^### \d{4}-\d{2}-\d{2})", text, flags=re.MULTILINE)
    chunks: list[str] = []
    for block in filter(None, blocks):
        heading = block.splitlines()[0] if block.startswith("### ") else "### 日期未标注"
        body = block[len(heading):].strip()
        while body:
            piece, body = body[:chunk_chars], body[chunk_chars:]
            chunks.append(f"{heading}（原文分段）\n{piece}")
    return chunks


async def _bounded_review_context(period_label: str, day_sections: str) -> str:
    from .review_context_service import bounded
    return await bounded(period_label, day_sections, call_chat_completion)


def _source_coverage(week_start: str) -> str:
    start = date_cls.fromisoformat(week_start)
    diary_days: list[str] = []
    state_days: list[str] = []
    review_days: list[str] = []
    unconfirmed_days: list[str] = []
    missing_days: list[str] = []
    for offset in range(7):
        day = (start + timedelta(days=offset)).isoformat()
        diary, state, review = db.get_diary(day), db.get_daily_state(day), db.get_daily_review(day)
        if diary is not None:
            diary_days.append(day)
            if not str(diary["confirmed_at"] or "").strip():
                unconfirmed_days.append(f"{day} 日记未确认")
        if state is not None:
            state_days.append(day)
            if state["daily_thirty_status"] == "unknown" or not state["key_events"]:
                unconfirmed_days.append(f"{day} 状态含未确认项")
        if review is not None:
            review_days.append(day)
        if diary is None and state is None and review is None:
            missing_days.append(day)
    lines = [
        "", "", "---", "", "## 来源与覆盖",
        f"- 周期：{week_start} ~ {week_end_for(week_start)}（7 天）",
        f"- 日记来源日期：{', '.join(diary_days) or '无'}",
        f"- 状态来源日期：{', '.join(state_days) or '无'}",
        f"- 每日回顾来源日期：{', '.join(review_days) or '无'}",
        f"- 完全无记录日期：{', '.join(missing_days) or '无'}",
        f"- 未确认输入：{'; '.join(unconfirmed_days) or '无'}",
    ]
    return "\n".join(lines)


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
                f"事件={state['key_events'] or '未确认'}"
            )
        if diary is not None:
            diary_count += 1
            confirmation = "已确认" if str(diary["confirmed_at"] or "").strip() else "未确认"
            lines.append(f"- 日记（{confirmation}）：{diary['markdown_content']}")
        if review is not None:
            lines.append(f"- AI 回顾：{review['markdown_content']}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections), diary_count


@operation("record", automatic=True)
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

    source_payload = period_payload(week_start, week_end)
    day_sections, diary_count = _build_day_sections(week_start)
    if diary_count == 0:
        raise ValueError("这一周没有任何日记，先积累几天再复盘。")

    if source_payload != period_payload(week_start, week_end):
        raise ValueError('读取周期材料时来源发生变化，旧回顾已保留，请重试。')
    coverage = _source_coverage(week_start)
    review_context = await _bounded_review_context(f"{week_start} ~ {week_end}", day_sections)
    messages = build_weekly_review_messages(week_start, week_end, review_context)
    markdown_content = await call_chat_completion(messages, temperature=0.35)
    markdown_content = markdown_content.replace("**", "").strip()
    markdown_content += coverage
    existing = db.get_weekly_review(week_start)
    if not overwrite and existing is not None:
        return WeeklyReviewResult(week_start, week_end, str(existing["markdown_content"]), False)
    db.upsert_weekly_review(week_start, markdown_content)
    save_snapshot("weekly", week_start, source_payload)
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
        except OperationStopped:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("自动每周回顾检查失败")
        await asyncio.sleep(max(600, settings.weekly_review_check_seconds))
