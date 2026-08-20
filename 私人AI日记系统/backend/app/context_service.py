from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil
from sqlite3 import Row

from . import db
from .config import settings
from .llm import call_chat_completion
from .memory_service import build_structured_memory_context
from .life_loop_service import build_follow_up_result_context


SUMMARY_TYPE = "conversation_summary"
SUMMARY_MARKER_RE = re.compile(r"<!--\s*last_message_id:(\d+)\s*-->")


@dataclass(frozen=True)
class ChatContext:
    system_context: str
    raw_messages: list[Row]
    used_chars: int
    max_chars: int
    used_tokens: int = 0
    max_tokens: int = 0
    warning: bool = False
    compression_triggered: bool = False


def _row_content(row: Row) -> str:
    return str(row["content"] or "").strip()


def _row_char_count(row: Row) -> int:
    return len(_row_content(row)) + 24


def estimate_tokens(text: object) -> int:
    """Estimate mixed Chinese/English prompt tokens without adding a tokenizer dependency."""
    value = str(text or "")
    if not value:
        return 0
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", value))
    latin_runs = re.findall(r"[A-Za-z0-9_]+", value)
    latin_tokens = sum(max(1, ceil(len(run) / 4)) for run in latin_runs)
    punctuation = len(re.findall(r"[^\sA-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", value))
    whitespace = len(re.findall(r"\s+", value))
    return max(1, int(ceil(cjk * 1.1 + latin_tokens + punctuation * 0.35 + whitespace * 0.1)))


def _row_token_count(row: Row) -> int:
    return estimate_tokens(f"{row['created_at']} {'用户' if row['role'] == 'user' else 'Mio'}：") + estimate_tokens(
        _row_content(row)
    )


def _latest_user_query(history_rows: list[Row]) -> str:
    for row in reversed(history_rows):
        if row["role"] == "user":
            return _row_content(row)
    return ""


def _compact_text(text: str, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _strip_summary_marker(content: str) -> str:
    return SUMMARY_MARKER_RE.sub("", content).strip()


def _summary_last_message_id(content: str) -> int:
    match = SUMMARY_MARKER_RE.search(content)
    return int(match.group(1)) if match else 0


def _summary_with_marker(summary: str, last_message_id: int) -> str:
    return f"<!-- last_message_id:{last_message_id} -->\n{summary.strip()}"


def _format_message_rows(rows: list[Row], max_chars: int = 9000) -> str:
    lines: list[str] = []
    total = 0
    for row in rows:
        role = "用户" if row["role"] == "user" else "Mio"
        line = f"{row['created_at']} {role}：{_compact_text(_row_content(row), 500)}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


def _trim_rows_to_budget(rows: list[Row], max_chars: int) -> list[Row]:
    selected: list[Row] = []
    total = 0
    for row in reversed(rows):
        total += _row_char_count(row)
        if selected and total > max_chars:
            break
        selected.append(row)
    return list(reversed(selected))


def _trim_rows_to_token_budget(rows: list[Row], max_tokens: int) -> list[Row]:
    selected: list[Row] = []
    total = 0
    for row in reversed(rows):
        total += _row_token_count(row)
        if selected and total > max_tokens:
            break
        selected.append(row)
    return list(reversed(selected))


def _select_raw_messages(
    history_rows: list[Row],
    periodic_context: str,
    summary_text: str,
    summary_last_id: int,
    over_budget: bool,
) -> list[Row]:
    if summary_last_id:
        raw_messages = [row for row in history_rows if int(row["id"]) > summary_last_id]
        if len(raw_messages) < settings.chat_recent_keep_messages:
            raw_messages = history_rows[-settings.chat_recent_keep_messages :]
    else:
        raw_messages = history_rows[-settings.chat_history_limit :]
        if not over_budget:
            raw_messages = history_rows

    remaining_chars = max(
        2000,
        settings.chat_context_max_chars - len(periodic_context) - len(summary_text),
    )
    selected = _trim_rows_to_budget(raw_messages, remaining_chars)
    token_limit = max(1000, int(settings.chat_context_max_tokens))
    remaining_tokens = max(
        512,
        token_limit - estimate_tokens(periodic_context) - estimate_tokens(summary_text),
    )
    return _trim_rows_to_token_budget(selected, remaining_tokens)


def _usage_values(periodic_context: str, summary_text: str, raw_messages: list[Row]) -> tuple[int, int]:
    used_chars = len(periodic_context) + len(summary_text)
    used_chars += sum(_row_char_count(row) for row in raw_messages)
    return used_chars, settings.chat_context_max_chars


def _token_usage_values(
    periodic_context: str,
    summary_text: str,
    raw_messages: list[Row],
) -> tuple[int, int]:
    used_tokens = estimate_tokens(periodic_context) + estimate_tokens(summary_text)
    used_tokens += sum(_row_token_count(row) for row in raw_messages)
    return used_tokens, max(1000, int(settings.chat_context_max_tokens))


def _context_thresholds(used_tokens: int) -> tuple[bool, bool]:
    max_tokens = max(1000, int(settings.chat_context_max_tokens))
    warning_ratio = min(0.99, max(0.1, float(settings.chat_context_warning_ratio)))
    compress_ratio = min(1.0, max(warning_ratio, float(settings.chat_context_compress_ratio)))
    return used_tokens >= int(max_tokens * warning_ratio), used_tokens >= int(max_tokens * compress_ratio)


def preview_chat_context_usage(conversation_id: str, history_rows: list[Row]) -> dict[str, object]:
    """Return the next-turn context budget without triggering a model compression call."""
    periodic_context = build_periodic_memory_context(conversation_id, _latest_user_query(history_rows))
    summary_row = db.get_latest_memory(SUMMARY_TYPE, tags=conversation_id)
    summary_content = str(summary_row["content"] or "") if summary_row else ""
    summary_text = _strip_summary_marker(summary_content)
    summary_last_id = _summary_last_message_id(summary_content)
    raw_total_tokens = (
        estimate_tokens(periodic_context)
        + estimate_tokens(summary_text)
        + sum(_row_token_count(row) for row in history_rows)
    )
    warning, compression_triggered = _context_thresholds(raw_total_tokens)
    over_budget = (
        len(periodic_context)
        + len(summary_text)
        + sum(_row_char_count(row) for row in history_rows)
        > settings.chat_context_max_chars
        or compression_triggered
    )
    raw_messages = _select_raw_messages(
        history_rows,
        periodic_context,
        summary_text,
        summary_last_id,
        over_budget,
    )
    used_chars, max_chars = _usage_values(periodic_context, summary_text, raw_messages)
    used_tokens, max_tokens = _token_usage_values(periodic_context, summary_text, raw_messages)
    return {
        "used_chars": used_chars,
        "max_chars": max_chars,
        "percent": round(min(100.0, used_chars / max_chars * 100), 1) if max_chars else 0.0,
        "used_tokens": used_tokens,
        "max_tokens": max_tokens,
        "token_percent": round(min(100.0, used_tokens / max_tokens * 100), 1) if max_tokens else 0.0,
        "warning": warning,
        "compression_triggered": compression_triggered,
        "has_summary": bool(summary_text),
    }


def build_chat_context_snapshot(conversation_id: str, history_rows: list[Row]) -> ChatContext:
    """Build context from stored memory without triggering an LLM compression call."""
    periodic_context = build_periodic_memory_context(conversation_id, _latest_user_query(history_rows))
    summary_row = db.get_latest_memory(SUMMARY_TYPE, tags=conversation_id)
    summary_content = str(summary_row["content"] or "") if summary_row else ""
    summary_text = _strip_summary_marker(summary_content)
    summary_last_id = _summary_last_message_id(summary_content)
    raw_total_tokens = (
        estimate_tokens(periodic_context)
        + estimate_tokens(summary_text)
        + sum(_row_token_count(row) for row in history_rows)
    )
    warning, compression_triggered = _context_thresholds(raw_total_tokens)
    over_budget = (
        len(periodic_context)
        + len(summary_text)
        + sum(_row_char_count(row) for row in history_rows)
        > settings.chat_context_max_chars
        or compression_triggered
    )
    raw_messages = _select_raw_messages(
        history_rows,
        periodic_context,
        summary_text,
        summary_last_id,
        over_budget,
    )

    context_parts: list[str] = []
    if summary_text:
        context_parts.append(
            "以下是较早聊天的压缩记忆。它用于延续关系和事实，不代表用户本轮刚说过。"
            "不要向用户解释这段摘要的存在。\n\n"
            + summary_text
        )
    if periodic_context:
        context_parts.append(periodic_context)

    used_chars, max_chars = _usage_values(periodic_context, summary_text, raw_messages)
    used_tokens, max_tokens = _token_usage_values(periodic_context, summary_text, raw_messages)
    return ChatContext(
        system_context="\n\n---\n\n".join(context_parts),
        raw_messages=raw_messages,
        used_chars=used_chars,
        max_chars=max_chars,
        used_tokens=used_tokens,
        max_tokens=max_tokens,
        warning=warning,
        compression_triggered=compression_triggered,
    )


def build_fast_chat_context_snapshot(
    conversation_id: str,
    history_rows: list[Row],
    *,
    recent_messages: int = 6,
    max_tokens: int = 1800,
) -> ChatContext:
    """Build a bounded context for ordinary chat without model compression or broad diary scans."""
    query = _latest_user_query(history_rows)
    memory_context = _compact_text(
        build_structured_memory_context(conversation_id, query),
        800,
    )
    follow_up_context = _compact_text(build_follow_up_result_context(conversation_id), 360)
    summary_row = db.get_latest_memory(SUMMARY_TYPE, tags=conversation_id)
    summary_content = str(summary_row["content"] or "") if summary_row else ""
    summary_text = _compact_text(_strip_summary_marker(summary_content), 520)

    raw_messages = list(history_rows[-max(2, int(recent_messages)):])
    reserved_tokens = (
        estimate_tokens(memory_context)
        + estimate_tokens(follow_up_context)
        + estimate_tokens(summary_text)
    )
    raw_messages = _trim_rows_to_token_budget(
        raw_messages,
        max(512, int(max_tokens) - reserved_tokens),
    )
    context_parts: list[str] = []
    if summary_text:
        context_parts.append("较早聊天摘要：\n" + summary_text)
    if memory_context:
        context_parts.append(memory_context)
    if follow_up_context:
        context_parts.append(follow_up_context)
    system_context = "\n\n---\n\n".join(context_parts)
    used_tokens = estimate_tokens(system_context) + sum(_row_token_count(row) for row in raw_messages)
    used_chars = len(system_context) + sum(_row_char_count(row) for row in raw_messages)
    return ChatContext(
        system_context=system_context,
        raw_messages=raw_messages,
        used_chars=used_chars,
        max_chars=max(4000, used_chars),
        used_tokens=used_tokens,
        max_tokens=max(1000, int(max_tokens)),
        warning=used_tokens >= int(max_tokens * 0.8),
        compression_triggered=False,
    )


def _start_date_for_memory() -> str:
    today = date.fromisoformat(db.today_string())
    days = max(1, settings.memory_context_days)
    return (today - timedelta(days=days - 1)).isoformat()


def build_periodic_memory_context(conversation_id: str, query: str = "") -> str:
    start_date = _start_date_for_memory()
    sections: list[str] = []

    structured_memory = build_structured_memory_context(conversation_id, query)
    if structured_memory:
        sections.append(structured_memory)

    follow_up_results = build_follow_up_result_context(conversation_id)
    if follow_up_results:
        sections.append(follow_up_results)

    states = db.list_daily_states_since(start_date)
    if states:
        lines = []
        for row in states:
            lines.append(
                f"- {row['date']}：状态={row['daily_thirty_status']}；"
                f"依据={row['daily_thirty_reason'] or '未确认'}；"
                f"情绪={row['mood'] or '未确认'}；"
                f"事件={row['key_events'] or '未确认'}；"
                f"耗电={row['avoidance_signals'] or '未确认'}；"
                f"下一步={row['next_min_action'] or '未确认'}"
            )
        sections.append("近几天状态板：\n" + "\n".join(lines))

    pending_threads = db.list_due_pending_threads(conversation_id, db.now_iso(), limit=4)
    if pending_threads:
        lines = []
        for row in pending_threads:
            follow_up = str(row["follow_up_after"] or "")
            timing = f"；适合跟进时间={follow_up}" if follow_up else ""
            lines.append(f"- {row['content']}{timing}")
        sections.append(
            "已经到达明确跟进时间的话题与约定：\n"
            + "\n".join(lines)
            + "\n这些不是待办清单。只有当前话题不冲突、用户也没有在表达明显情绪时，才自然碰一下；不要连续追问。"
        )

    diaries = db.list_diaries_since(start_date)
    if diaries:
        lines = []
        for row in diaries:
            body = _compact_text(str(row["markdown_content"] or ""), 420)
            lines.append(f"- {row['date']}《{row['title']}》：{body}")
        sections.append("近几天日记摘要：\n" + "\n".join(lines))

    reviews = db.list_daily_reviews_since(start_date)
    if reviews:
        lines = []
        for row in reviews:
            lines.append(f"- {row['date']}：{_compact_text(str(row['markdown_content'] or ''), 300)}")
        sections.append("近几天 AI 回顾：\n" + "\n".join(lines))

    messages = db.get_messages_since(start_date, conversation_id=conversation_id, limit=500)
    samples_by_day: dict[str, list[str]] = {}
    for row in messages:
        if row["role"] != "user":
            continue
        day = str(row["created_at"])[:10]
        samples_by_day.setdefault(day, [])
        samples_by_day[day].append(_compact_text(_row_content(row), 90))

    if samples_by_day:
        lines = []
        per_day = max(1, settings.memory_context_messages_per_day)
        for day, samples in sorted(samples_by_day.items()):
            picked = samples[-per_day:]
            lines.append(f"- {day}：" + " / ".join(picked))
        sections.append("近几天用户原话线索：\n" + "\n".join(lines))

    context = "\n\n".join(sections).strip()
    if not context:
        return ""

    prefix = (
        "以下是跨天周期记忆。它用于维持连续感，不代表用户本轮刚说过。"
        "引用时要自然，不要暴露你在读取数据库或摘要。\n\n"
    )
    return _compact_text(prefix + context, settings.memory_context_max_chars)


async def _compress_old_messages(
    conversation_id: str,
    previous_summary: str,
    old_rows: list[Row],
) -> str:
    old_text = _format_message_rows(old_rows)
    system = """你是 Mio 的对话上下文压缩器。

你的任务是把旧聊天压缩成后续对话可用的记忆。
不要编造事实。
保留用户偏好、最近困扰、未完成事项、关系语气、重要原话和 Mio 已经答应过的事。
输出中文，短而密，最多 900 字。
不要写成日记，不要写寒暄。"""
    user = f"""会话：{conversation_id}

已有压缩记忆：
{previous_summary or "无"}

这次新增的旧聊天：
{old_text}

请输出更新后的压缩记忆。"""
    return await call_chat_completion(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )


async def build_chat_context(
    conversation_id: str,
    history_rows: list[Row],
) -> ChatContext:
    periodic_context = build_periodic_memory_context(conversation_id, _latest_user_query(history_rows))
    summary_row = db.get_latest_memory(SUMMARY_TYPE, tags=conversation_id)
    summary_content = str(summary_row["content"] or "") if summary_row else ""
    summary_text = _strip_summary_marker(summary_content)
    summary_last_id = _summary_last_message_id(summary_content)

    base_chars = len(periodic_context) + len(summary_text)
    raw_chars = sum(_row_char_count(row) for row in history_rows)
    base_tokens = estimate_tokens(periodic_context) + estimate_tokens(summary_text)
    raw_tokens = sum(_row_token_count(row) for row in history_rows)
    warning, compression_triggered = _context_thresholds(base_tokens + raw_tokens)
    over_budget = (
        base_chars + raw_chars > settings.chat_context_max_chars
        or compression_triggered
    )

    if over_budget and history_rows:
        keep = max(4, settings.chat_recent_keep_messages)
        old_rows = [row for row in history_rows[:-keep] if int(row["id"]) > summary_last_id]
        if old_rows:
            try:
                new_summary = await _compress_old_messages(conversation_id, summary_text, old_rows)
                summary_last_id = int(old_rows[-1]["id"])
                summary_text = _compact_text(new_summary, 1400)
                db.replace_memory(
                    SUMMARY_TYPE,
                    _summary_with_marker(summary_text, summary_last_id),
                    importance=4,
                    tags=conversation_id,
                )
            except Exception:
                # 压缩失败时不影响正常聊天，只少带一些旧原文。
                pass

    raw_messages = _select_raw_messages(
        history_rows,
        periodic_context,
        summary_text,
        summary_last_id,
        over_budget,
    )

    context_parts: list[str] = []
    if summary_text:
        context_parts.append(
            "以下是较早聊天的压缩记忆。它用于延续关系和事实，不代表用户本轮刚说过。"
            "不要向用户解释这段摘要的存在。\n\n"
            + summary_text
        )
    if periodic_context:
        context_parts.append(periodic_context)

    used_chars, max_chars = _usage_values(periodic_context, summary_text, raw_messages)
    used_tokens, max_tokens = _token_usage_values(periodic_context, summary_text, raw_messages)
    return ChatContext(
        system_context="\n\n---\n\n".join(context_parts),
        raw_messages=raw_messages,
        used_chars=used_chars,
        max_chars=max_chars,
        used_tokens=used_tokens,
        max_tokens=max_tokens,
        warning=warning,
        compression_triggered=compression_triggered,
    )
