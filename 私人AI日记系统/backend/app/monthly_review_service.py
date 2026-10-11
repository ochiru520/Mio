from __future__ import annotations

import asyncio
import calendar
import logging
import re
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .model_runtime import operation
from .config import settings
from .llm import call_chat_completion
from .prompts import build_monthly_review_messages
from .review_sources import period_payload, save_snapshot
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


def _source_coverage(month: str) -> str:
    month_start, month_end = month_bounds(month)
    start, end = date_cls.fromisoformat(month_start), date_cls.fromisoformat(month_end)
    diary_days: list[str] = []
    state_days: list[str] = []
    review_days: list[str] = []
    unconfirmed_days: list[str] = []
    missing_days: list[str] = []
    current = start
    while current <= end:
        day = current.isoformat()
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
        current += timedelta(days=1)
    return "\n".join(
        [
            "", "", "---", "", "## 来源与覆盖",
            f"- 周期：{month_start} ~ {month_end}（自然月）",
            f"- 日记来源日期：{', '.join(diary_days) or '无'}",
            f"- 状态来源日期：{', '.join(state_days) or '无'}",
            f"- 每日回顾来源日期：{', '.join(review_days) or '无'}",
            f"- 完全无记录日期：{', '.join(missing_days) or '无'}",
            f"- 未确认输入：{'; '.join(unconfirmed_days) or '无'}",
        ]
    )


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
                    f"事件={state['key_events'] or '未确认'}"
                )
            if diary is not None:
                diary_count += 1
                confirmation = "已确认" if str(diary["confirmed_at"] or "").strip() else "未确认"
                lines.append(f"- 日记（{confirmation}）：{diary['markdown_content']}")
            if review is not None:
                lines.append(f"- AI 回顾：{review['markdown_content']}")
            sections.append("\n".join(lines))
        current += timedelta(days=1)
    return "\n\n".join(sections), diary_count


_active_generations: set[asyncio.Task] = set()


async def cancel_active() -> None:
    pending = [task for task in _active_generations if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@operation("record", automatic=True)
async def generate_monthly_review(month: str, overwrite: bool = True) -> MonthlyReviewResult:
    from .agent_task_service import automatic_work_paused
    if automatic_work_paused():
        raise ValueError("敏感能力已暂停，月记生成已停止。")
    task = asyncio.create_task(_generate_monthly_review(month, overwrite))
    _active_generations.add(task)
    try:
        return await task
    finally:
        _active_generations.discard(task)


async def _generate_monthly_review(month: str, overwrite: bool = True) -> MonthlyReviewResult:
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

    source_payload = period_payload(month_start, month_end)
    day_sections, diary_count = _build_day_sections(normalized)
    if diary_count == 0:
        raise ValueError("这个月没有任何日记，暂时无法形成月记。")

    if source_payload != period_payload(month_start, month_end):
        raise ValueError('读取周期材料时来源发生变化，旧回顾已保留，请重试。')
    coverage = _source_coverage(normalized)
    review_context = await _bounded_review_context(normalized, day_sections)
    messages = build_monthly_review_messages(normalized, month_start, month_end, review_context)
    markdown_content = await call_chat_completion(messages, temperature=0.3)
    markdown_content = markdown_content.replace("**", "").strip()
    markdown_content += coverage
    existing = db.get_monthly_review(normalized)
    if not overwrite and existing is not None:
        return MonthlyReviewResult(normalized, month_start, month_end, str(existing["markdown_content"]), False)
    db.upsert_monthly_review(normalized, markdown_content)
    save_snapshot("monthly", normalized, source_payload)
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
    from .agent_task_service import automatic_work_paused
    if automatic_work_paused() or not settings.monthly_review_enabled:
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

    try:
        result = await generate_monthly_review(month, overwrite=False)
    except asyncio.CancelledError:
        if automatic_work_paused() and not getattr(asyncio.current_task(), 'cancelling', lambda: 0)():
            return 0
        raise
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
