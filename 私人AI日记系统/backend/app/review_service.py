from __future__ import annotations

from dataclasses import dataclass

from . import db
from .llm import call_chat_completion
from .prompts import build_review_messages


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
    return "\n".join(f"- {row['content']}" for row in rows)


async def generate_review_for_date(date: str, overwrite: bool = True) -> ReviewResult:
    existing = db.get_daily_review(date)
    if existing is not None and not overwrite:
        return ReviewResult(date=date, markdown_content=str(existing["markdown_content"]), created=False)

    diary = db.get_diary(date)
    if diary is None:
        raise ValueError("没有找到这一天的日记。")

    messages = build_review_messages(
        date,
        diary["markdown_content"],
        db.get_day_summary(date),
        _chat_log_for_rows(db.get_today_messages(date)),
        _material_log_for_rows(db.list_diary_materials(date)),
    )
    markdown_content = await call_chat_completion(messages, temperature=0.35)
    markdown_content = markdown_content.replace("**", "").strip()
    db.upsert_daily_review(date, markdown_content)
    return ReviewResult(date=date, markdown_content=markdown_content, created=True)
