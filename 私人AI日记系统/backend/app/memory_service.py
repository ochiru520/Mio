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
PRIVATE_MEMORY_PREFIXES = ("qq_private_", "desktop_", "default")


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


def save_memory_item(
    *,
    layer: str,
    category: str,
    memory_key: str,
    content: str,
    source_conversation_id: str,
    source_message_id: int = 0,
    confidence: float = 0.0,
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
    memory_id, outcome = db.save_structured_memory(
        normalized_layer,
        normalized_category,
        key,
        normalized_content,
        source_conversation_id,
        source_message_id,
        normalized_confidence,
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
    memory_id = db.save_structured_memory_candidate(
        normalized_layer,
        normalized_category,
        key,
        normalized_content,
        source_conversation_id,
        source_message_id,
        normalized_confidence,
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
    core_rows = db.list_structured_memories(status="active", layer="L0", limit=100)
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
    for layer in ("L0", "L1", "L2"):
        layer_rows = [row for row in rows if row["layer"] == layer]
        if not layer_rows:
            continue
        lines = [
            f"- {row['content']}（置信度 {float(row['confidence'] or 0):.2f}，更新于 {str(row['updated_at'])[:10]}）"
            for row in layer_rows
        ]
        sections.append(f"{labels[layer]}：\n" + "\n".join(lines))
    return (
        "以下是有来源证据的分层私人记忆。只在当前话题相关时自然使用，"
        "不要逐条复述，不要透露数据库、层级或置信度。若与用户本轮原话冲突，以本轮原话为准。\n\n"
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
        "source_message_id": int(row["source_message_id"] or 0),
        "confidence": float(row["confidence"] or 0.0),
        "status": str(row["status"]),
        "superseded_by": int(row["superseded_by"] or 0),
        "last_seen_at": str(row["last_seen_at"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


__all__ = [
    "MEMORY_CATEGORIES",
    "MEMORY_LAYERS",
    "build_structured_memory_context",
    "is_group_conversation",
    "public_memory_item",
    "retrieve_memory_items",
    "save_memory_candidate",
    "save_memory_item",
]
