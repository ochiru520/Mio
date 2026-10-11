from __future__ import annotations

from dataclasses import dataclass

from . import db
from .model_runtime import operation
from .llm import call_chat_completion
from .prompts import build_review_messages
from .review_sources import daily_payload, save_snapshot


@dataclass(frozen=True)
class ReviewResult:
    date: str
    markdown_content: str
    created: bool


def _chat_log_for_rows(rows) -> str:
    parts: list[str] = []
    for row in rows:
        speaker = "用户" if row["role"] == "user" else "Mio"
        parts.append(f"[{row['created_at']}] {speaker}：{row['content']}")
    return "\n\n".join(parts)


def _material_log_for_rows(rows) -> str:
    return "\n".join(
        f"- [{row['date']}；来源={row['source'] or '未标注'}；记录于={row['created_at']}] {row['content']}"
        for row in rows
    )


@operation("record", automatic=True)
async def generate_review_for_date(date: str, overwrite: bool = True) -> ReviewResult:
    existing = db.get_daily_review(date)
    if existing is not None and not overwrite:
        return ReviewResult(date=date, markdown_content=str(existing["markdown_content"]), created=False)

    source_payload = daily_payload(date)
    diary = db.get_diary(date)
    if diary is None:
        raise ValueError("没有找到这一天的日记。")

    day_summary = db.get_day_summary(date)
    messages = build_review_messages(
        date,
        diary["markdown_content"],
        day_summary,
        _chat_log_for_rows(db.get_today_messages(date)),
        _material_log_for_rows(db.list_diary_materials(date)),
    )
    if source_payload != daily_payload(date):
        raise ValueError('读取当天材料时来源发生变化，旧回顾已保留，请重试。')
    markdown_content = await call_chat_completion(messages, temperature=0.35)
    markdown_content = markdown_content.replace("**", "").strip()
    source_status = "已确认" if str(diary["confirmed_at"] or "").strip() else "未确认"
    state_status = "有记录" if day_summary.get("daily_thirty_status") else "未记录"
    markdown_content += (
        f"\n\n---\n\n## 来源与覆盖\n"
        f"- 日期：{date}\n"
        f"- 日记：{source_status}（{date}）\n"
        f"- 每日状态：{state_status}"
        f"（每日三十={day_summary.get('daily_thirty_status') or '未确认'}；"
        f"情绪={day_summary.get('mood') or '未确认'}；"
        f"关键事件={day_summary.get('key_events') or '未确认'}）\n"
        f"- 聊天：{day_summary.get('message_count', 0)} 条"
        f"（{date}；无记录表示该日未保存聊天）\n"
        f"- 日记素材：{day_summary.get('material_count', 0)} 条"
        f"（{date}；无记录表示该日未保存素材）"
    )
    existing = db.get_daily_review(date)
    if not overwrite and existing is not None:
        return ReviewResult(date, str(existing["markdown_content"]), False)
    db.upsert_daily_review(date, markdown_content)
    save_snapshot("daily", date, source_payload)
    return ReviewResult(date=date, markdown_content=markdown_content, created=True)
