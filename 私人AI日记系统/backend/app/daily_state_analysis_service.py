"""Shared daily-state analysis for manual requests and background chat work."""
from __future__ import annotations
import json
import re
from . import db
from .llm import LLMConfigError, call_chat_completion, require_configured
from .prompts import build_state_messages
from .service_errors import ServiceOperationError

def _chat_log_for_rows(rows) -> str:
    parts: list[str] = []
    for row in rows:
        if row["role"] != "user":
            continue
        parts.append(f"[{row['created_at']}] 用户：{row['content']}")
    return "\n\n".join(parts)

def _material_log_for_rows(rows) -> str:
    return "\n".join(f"- {row['content']}" for row in rows)

def _parse_state_json(raw: str) -> dict[str, str]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    data = json.loads(text)
    try:
        mood_score = max(0, min(5, int(data.get("mood_score") or 0)))
    except (TypeError, ValueError):
        mood_score = 0
    return {
        "daily_thirty_status": str(data.get("daily_thirty_status") or "unknown"),
        "daily_thirty_reason": str(data.get("daily_thirty_reason") or "未确认"),
        "mood": str(data.get("mood") or "未确认"),
        "mood_score": mood_score,
        "key_events": str(data.get("key_events") or "未确认"),
        "avoidance_signals": str(data.get("avoidance_signals") or "未确认"),
        "next_min_action": str(data.get("next_min_action") or "未确认"),
    }

async def analyze_today_state() -> dict[str, str]:
    try:
        require_configured()
    except LLMConfigError as exc:
        raise ServiceOperationError(status_code=400, detail=str(exc)) from exc

    date = db.today_string()
    messages = db.get_today_messages(date)
    materials = db.list_diary_materials(date)
    if not messages and not materials:
        raise ServiceOperationError(status_code=400, detail="今天还没有聊天记录或日记素材。")

    state_messages = build_state_messages(
        date,
        _chat_log_for_rows(messages),
        _material_log_for_rows(materials),
    )
    try:
        raw = await call_chat_completion(state_messages, temperature=0.2)
        state = _parse_state_json(raw)
    except LLMConfigError as exc:
        raise ServiceOperationError(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise ServiceOperationError(status_code=502, detail=f"今日状态判定失败：{exc}") from exc

    db.upsert_daily_state(
        date,
        state["daily_thirty_status"],
        state["mood"],
        state["key_events"],
        state["avoidance_signals"],
        state["next_min_action"],
        state["daily_thirty_reason"],
        mood_score=int(state.get("mood_score") or 0),
    )
    return {"date": date, **state}
