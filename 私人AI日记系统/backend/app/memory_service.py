from __future__ import annotations

import re
from datetime import datetime, timedelta
from sqlite3 import Row

from . import db


MEMORY_LAYERS = {"L0", "L1", "L2"}
MEMORY_CATEGORIES = {
    "identity",
    "preference",
    "relationship",
    "current_state",
    "plan",
    "project",
    "experience",
    "person",
    "other",
}
MEMORY_TEMPORAL_STATUSES = {"current", "historical", "planned", "enduring", "time_unknown"}
DURABLE_CORE_CATEGORIES = {"identity", "preference", "relationship", "person"}
PRIVATE_MEMORY_PREFIXES = ("qq_private_", "desktop_", "default")


def source_window_for_conversation(conversation_id: str) -> str:
    """Map an origin conversation to a shared-memory source window."""
    value = str(conversation_id or "").strip().lower()
    if value.startswith("desktop_agent_"):
        return "agent"
    if value.startswith("qq_group_"):
        return "qq_group"
    if value.startswith("qq_"):
        return "companion"
    if value == "desktop_pet" or value.startswith("desktop_"):
        return "companion"
    return "companion"


def is_group_conversation(conversation_id: str) -> bool:
    return str(conversation_id or "").startswith("qq_group_")


def _clean(value: object, max_chars: int) -> str:
    return " ".join(str(value or "").split()).strip()[:max_chars]


def _memory_key(value: object, category: str, content: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff_-]+", "_", _clean(value, 80).casefold()).strip("_")
    if normalized:
        return normalized
    compact = re.sub(r"\s+", "", content.casefold())[:32]
    return f"{category}:{compact}"


def _iso_time(value: object) -> str:
    clean = _clean(value, 40)
    if not clean:
        return ""
    try:
        return datetime.fromisoformat(clean.replace("Z", "+00:00")).isoformat(timespec="seconds")
    except ValueError:
        try:
            return datetime.fromisoformat(clean + "T00:00:00").date().isoformat()
        except ValueError as exc:
            raise ValueError("记忆时间必须是明确的 ISO 日期或时间，不能保存“明天”等相对时间。") from exc


def _temporal_fields(
    *,
    category: str,
    source_message_id: int,
    occurred_at: str,
    learned_at: str,
    valid_from: str,
    valid_until: str,
    last_confirmed_at: str,
    time_confidence: float,
    temporal_status: str,
) -> dict[str, object]:
    source = db.get_message_by_id(int(source_message_id or 0)) if source_message_id else None
    learned = _iso_time(learned_at or (str(source["created_at"] or "") if source is not None else db.now_iso()))
    occurred = _iso_time(occurred_at)
    valid_start = _iso_time(valid_from)
    valid_end = _iso_time(valid_until)
    confirmed = _iso_time(last_confirmed_at)
    confidence = max(0.0, min(1.0, float(time_confidence or 0.0)))
    status = str(temporal_status or "").strip().lower()
    if status not in MEMORY_TEMPORAL_STATUSES:
        status = ""
    if not status:
        if category in DURABLE_CORE_CATEGORIES:
            status = "enduring"
        elif category == "plan" and (occurred or valid_start):
            status = "planned"
        elif category in {"current_state", "project"}:
            status = "current"
        elif category == "experience" and occurred:
            status = "historical"
        else:
            status = "time_unknown"
    if status == "current" and not valid_start:
        valid_start = learned
    if status == "historical" and occurred and not valid_start:
        valid_start = occurred
        valid_end = valid_end or occurred
    if status in {"current", "planned"} and not confirmed:
        confirmed = learned
    return {
        "occurred_at": occurred,
        "learned_at": learned,
        "valid_from": valid_start,
        "valid_until": valid_end,
        "last_confirmed_at": confirmed,
        "time_confidence": confidence,
        "temporal_status": status,
    }


def save_memory_item(
    *,
    layer: str,
    category: str,
    memory_key: str,
    content: str,
    source_conversation_id: str,
    source_message_id: int = 0,
    confidence: float = 0.0,
    occurred_at: str = "",
    learned_at: str = "",
    valid_from: str = "",
    valid_until: str = "",
    last_confirmed_at: str = "",
    time_confidence: float = 0.0,
    temporal_status: str = "",
    source_window: str = "",
) -> dict[str, object]:
    if is_group_conversation(source_conversation_id):
        raise ValueError("群聊内容不能写入私人记忆。")

    normalized_layer = str(layer or "").upper()
    normalized_category = str(category or "other").lower()
    normalized_content = _clean(content, 800)
    normalized_confidence = max(0.0, min(1.0, float(confidence or 0.0)))
    if normalized_layer not in MEMORY_LAYERS:
        raise ValueError("记忆层级必须是 L0、L1 或 L2。")
    if normalized_category not in MEMORY_CATEGORIES:
        normalized_category = "other"
    if not normalized_content:
        raise ValueError("记忆内容不能为空。")
    if normalized_layer == "L0" and normalized_confidence < 0.90:
        raise ValueError("核心记忆需要至少 0.90 的置信度。")
    if normalized_layer in {"L1", "L2"} and normalized_confidence < 0.75:
        raise ValueError("记忆证据不足。")

    key = _memory_key(memory_key, normalized_category, normalized_content)
    temporal = _temporal_fields(
        category=normalized_category,
        source_message_id=source_message_id,
        occurred_at=occurred_at,
        learned_at=learned_at,
        valid_from=valid_from,
        valid_until=valid_until,
        last_confirmed_at=last_confirmed_at,
        time_confidence=time_confidence,
        temporal_status=temporal_status,
    )
    memory_id, outcome = db.save_structured_memory(
        normalized_layer,
        normalized_category,
        key,
        normalized_content,
        source_conversation_id,
        source_message_id,
        normalized_confidence,
        source_window=source_window or source_window_for_conversation(source_conversation_id),
        **temporal,
    )
    return {"id": memory_id, "outcome": outcome, "layer": normalized_layer, "memory_key": key}


def save_memory_candidate(
    *,
    layer: str,
    category: str,
    memory_key: str,
    content: str,
    source_conversation_id: str,
    source_message_id: int = 0,
    confidence: float = 0.0,
    occurred_at: str = "",
    learned_at: str = "",
    valid_from: str = "",
    valid_until: str = "",
    last_confirmed_at: str = "",
    time_confidence: float = 0.0,
    temporal_status: str = "",
    source_window: str = "",
) -> dict[str, object]:
    """Store an uncertain fact for later confirmation; candidates never enter context."""
    if is_group_conversation(source_conversation_id):
        raise ValueError("群聊内容不能写入私人记忆。")
    normalized_layer = str(layer or "").upper()
    normalized_category = str(category or "other").lower()
    normalized_content = _clean(content, 800)
    normalized_confidence = max(0.0, min(1.0, float(confidence or 0.0)))
    if normalized_layer not in MEMORY_LAYERS:
        raise ValueError("记忆层级必须是 L0、L1 或 L2。")
    if normalized_category not in MEMORY_CATEGORIES:
        normalized_category = "other"
    if not normalized_content:
        raise ValueError("记忆内容不能为空。")
    if normalized_confidence < 0.55:
        raise ValueError("记忆候选置信度过低。")
    key = _memory_key(memory_key, normalized_category, normalized_content)
    temporal = _temporal_fields(
        category=normalized_category,
        source_message_id=source_message_id,
        occurred_at=occurred_at,
        learned_at=learned_at,
        valid_from=valid_from,
        valid_until=valid_until,
        last_confirmed_at=last_confirmed_at,
        time_confidence=time_confidence,
        temporal_status=temporal_status,
    )
    memory_id = db.save_structured_memory_candidate(
        normalized_layer,
        normalized_category,
        key,
        normalized_content,
        source_conversation_id,
        source_message_id,
        normalized_confidence,
        source_window=source_window or source_window_for_conversation(source_conversation_id),
        **temporal,
    )
    return {"id": memory_id, "outcome": "candidate", "layer": normalized_layer, "memory_key": key}


def _query_terms(query: str) -> set[str]:
    compact = re.sub(r"\s+", "", str(query or "").casefold())
    terms = {part for part in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", str(query or "").casefold()) if len(part) >= 2}
    terms.update(compact[index : index + 2] for index in range(max(0, len(compact) - 1)))
    return terms


def _relevance(row: Row, query_terms: set[str], now: datetime) -> float:
    haystack = f"{row['memory_key']} {row['content']}".casefold()
    overlap = sum(1 for term in query_terms if term in haystack)
    confidence = float(row["confidence"] or 0.0)
    layer_bonus = {"L0": 8.0, "L1": 5.0, "L2": 2.0}.get(str(row["layer"]), 0.0)
    try:
        updated = datetime.fromisoformat(str(row["updated_at"]))
        if updated.tzinfo is not None and now.tzinfo is None:
            now = now.astimezone(updated.tzinfo)
        age_days = max(0.0, (now - updated).total_seconds() / 86400)
    except (TypeError, ValueError):
        age_days = 365.0
    recency = max(0.0, 4.0 - age_days / 7.0) if row["layer"] == "L1" else max(0.0, 1.0 - age_days / 180.0)
    evidence_bonus = 1.5 if int(row["source_message_id"] or 0) > 0 else 0.0
    return overlap * 3.0 + confidence * 4.0 + layer_bonus + recency + evidence_bonus


def retrieve_memory_items(query: str = "", limit: int = 16) -> list[Row]:
    now = datetime.now().astimezone()
    recent_cutoff = now - timedelta(days=21)
    db.sleep_stale_structured_memories(recent_cutoff.isoformat(timespec="seconds"))
    matched_rows = (
        db.search_structured_memories(query, status="active", limit=300)
        if str(query or "").strip()
        else db.list_structured_memories(status="active", limit=300)
    )
    # L0 is the durable identity/preference layer and must survive topic filtering.
    core_rows = [
        row for row in db.list_structured_memories(status="active", layer="L0", limit=100)
        if str(row["category"] or "") in DURABLE_CORE_CATEGORIES
    ]
    rows_by_id = {int(row["id"]): row for row in [*matched_rows, *core_rows]}
    rows = list(rows_by_id.values())
    query_terms = _query_terms(query)
    candidates: list[Row] = []
    for row in rows:
        if row["layer"] == "L1":
            try:
                if datetime.fromisoformat(str(row["updated_at"])) < recent_cutoff:
                    continue
            except (TypeError, ValueError):
                continue
        candidates.append(row)
    candidates.sort(key=lambda row: (_relevance(row, query_terms, now), int(row["id"])), reverse=True)

    selected: list[Row] = []
    for row in candidates:
        if row["layer"] == "L0" and row not in selected:
            selected.append(row)
    for row in candidates:
        if row not in selected:
            selected.append(row)
        if len(selected) >= max(1, min(limit, 40)):
            break
    selected = selected[: max(1, min(limit, 40))]
    db.mark_structured_memories_seen([int(row["id"]) for row in selected])
    return selected


def build_structured_memory_context(conversation_id: str, query: str = "") -> str:
    if is_group_conversation(conversation_id):
        return ""
    rows = retrieve_memory_items(query, limit=16)
    if not rows:
        return ""
    labels = {"L0": "核心事实与稳定偏好", "L1": "近期状态", "L2": "长期经历"}
    sections: list[str] = []
    source_dates: dict[int, str] = {}
    for row in rows:
        source_message_id = int(row["source_message_id"] or 0)
        if source_message_id <= 0 or source_message_id in source_dates:
            continue
        source = db.get_message_by_id(source_message_id)
        if source is None:
            continue
        try:
            source_dates[source_message_id] = db.logical_date_for_datetime(
                datetime.fromisoformat(str(source["created_at"]))
            )
        except (TypeError, ValueError):
            continue
    for layer in ("L0", "L1", "L2"):
        layer_rows = [row for row in rows if row["layer"] == layer]
        if not layer_rows:
            continue
        lines: list[str] = []
        for row in layer_rows:
            source_message_id = int(row["source_message_id"] or 0)
            learned_at = str(row["learned_at"] or "")
            occurred_at = str(row["occurred_at"] or "")
            valid_from = str(row["valid_from"] or "")
            valid_until = str(row["valid_until"] or "")
            temporal_status = str(row["temporal_status"] or "time_unknown")
            source_window = str(row["source_window"] or "")
            source_date = source_dates.get(source_message_id, "")
            if occurred_at:
                date_note = f"发生时间 {occurred_at[:16]}"
            elif learned_at:
                source_note = f"来源记录日 {source_date}，" if source_date else ""
                date_note = f"{source_note}用户在 {learned_at[:16]} 提到，事情发生时间未确认"
            elif source_date:
                date_note = f"来源记录日 {source_date}，事情发生时间未确认"
            else:
                date_note = f"记录维护于 {str(row['updated_at'])[:10]}（不是事件日期）"
            validity = ""
            if valid_from or valid_until:
                validity = f"，有效期 {valid_from[:16] or '未知'} 至 {valid_until[:16] or '未结束'}"
            origin = f"，来源窗口 {source_window}" if source_window else ""
            lines.append(
                f"- {row['content']}（时间状态 {temporal_status}，{date_note}{origin}{validity}，"
                f"置信度 {float(row['confidence'] or 0):.2f}）"
            )
        sections.append(f"{labels[layer]}：\n" + "\n".join(lines))
    return (
        "以下是有来源证据的分层私人记忆。只在当前话题相关时自然使用，"
        "不要逐条复述，不要透露数据库、层级或置信度。若与用户本轮原话冲突，以本轮原话为准。"
        "historical 只能当作过去事件；current 也要服从有效期与最后确认时间；time_unknown 不得说成最近。"
        "只有“来源记录日”可以帮助解释当时的相对时间；“记录维护于”只是数据库维护时间，"
        "绝不能据此推断事情发生日期。\n\n"
        + "\n\n".join(sections)
    )


def public_memory_item(row: Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "layer": str(row["layer"]),
        "category": str(row["category"]),
        "memory_key": str(row["memory_key"]),
        "content": str(row["content"]),
        "source_conversation_id": str(row["source_conversation_id"]),
        "source_window": str(row["source_window"] or ""),
        "source_message_id": int(row["source_message_id"] or 0),
        "confidence": float(row["confidence"] or 0.0),
        "occurred_at": str(row["occurred_at"] or ""),
        "learned_at": str(row["learned_at"] or ""),
        "valid_from": str(row["valid_from"] or ""),
        "valid_until": str(row["valid_until"] or ""),
        "last_confirmed_at": str(row["last_confirmed_at"] or ""),
        "time_confidence": float(row["time_confidence"] or 0.0),
        "temporal_status": str(row["temporal_status"] or "time_unknown"),
        "status": str(row["status"]),
        "superseded_by": int(row["superseded_by"] or 0),
        "last_seen_at": str(row["last_seen_at"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


__all__ = [
    "MEMORY_CATEGORIES",
    "MEMORY_LAYERS",
    "MEMORY_TEMPORAL_STATUSES",
    "build_structured_memory_context",
    "is_group_conversation",
    "public_memory_item",
    "retrieve_memory_items",
    "save_memory_candidate",
    "save_memory_item",
    "source_window_for_conversation",
]
