from __future__ import annotations

from sqlite3 import Row

from . import db


FOLLOW_UP_OUTCOMES = {"completed", "partial", "not_completed"}
FOLLOW_UP_OUTCOME_LABELS = {
    "completed": "已完成",
    "partial": "部分完成",
    "not_completed": "未完成",
}


def _meaningful_daily_state(row: Row | None) -> bool:
    if row is None:
        return False
    if str(row["daily_thirty_status"] or "") not in {"", "unknown"}:
        return True
    return any(
        str(row[field] or "").strip()
        for field in ("daily_thirty_reason", "mood", "key_events", "avoidance_signals", "next_min_action")
    )


def diary_lifecycle(date: str) -> dict[str, object]:
    state = db.get_daily_state(date)
    materials = db.list_diary_materials(date)
    diary = db.get_diary(date)
    review = db.get_daily_review(date)
    state_ready = _meaningful_daily_state(state)
    material_count = len(materials)
    used_material_count = sum(1 for row in materials if int(row["used_in_diary"] or 0) > 0)
    diary_ready = diary is not None
    confirmed = bool(diary and str(diary["confirmed_at"] or "").strip())
    review_ready = review is not None

    steps = [
        {
            "id": "state",
            "label": "今日状态",
            "status": "complete" if state_ready else "pending",
            "detail": "已形成状态依据" if state_ready else "还没有明确状态",
            "timestamp": str(state["updated_at"] or "") if state else "",
        },
        {
            "id": "materials",
            "label": "日记素材",
            "status": "complete" if material_count else "pending",
            "detail": f"{material_count} 条素材，{used_material_count} 条已用于日记",
            "timestamp": str(materials[-1]["created_at"] or "") if materials else "",
        },
        {
            "id": "diary",
            "label": "日记",
            "status": "complete" if diary_ready else "pending",
            "detail": "日记已生成" if diary_ready else "等待生成日记",
            "timestamp": str(diary["updated_at"] or "") if diary else "",
        },
        {
            "id": "confirmed",
            "label": "已确认",
            "status": "complete" if confirmed else ("ready" if diary_ready else "blocked"),
            "detail": "内容已由用户确认" if confirmed else ("等待用户确认" if diary_ready else "需要先生成日记"),
            "timestamp": str(diary["confirmed_at"] or "") if diary else "",
        },
        {
            "id": "review",
            "label": "次日回顾",
            "status": "complete" if review_ready else ("ready" if confirmed else "blocked"),
            "detail": (
                "次日回顾已生成"
                if review_ready
                else ("已具备生成条件" if confirmed else "确认日记后才会自动回顾")
            ),
            "timestamp": str(review["updated_at"] or "") if review else "",
        },
    ]
    return {
        "date": date,
        "state_ready": state_ready,
        "material_count": material_count,
        "used_material_count": used_material_count,
        "diary_ready": diary_ready,
        "confirmed": confirmed,
        "review_ready": review_ready,
        "review_eligible": confirmed and not review_ready,
        "steps": steps,
    }


def public_follow_up_result(row: Row | dict[str, object]) -> dict[str, object]:
    item = dict(row)
    outcome = str(item.get("outcome") or "")
    return {
        "id": int(item.get("id") or 0),
        "thread_id": int(item.get("thread_id") or 0),
        "conversation_id": str(item.get("conversation_id") or ""),
        "thread_content": str(item.get("thread_content") or ""),
        "outcome": outcome,
        "outcome_label": FOLLOW_UP_OUTCOME_LABELS.get(outcome, outcome),
        "summary": str(item.get("summary") or ""),
        "adjustment": str(item.get("adjustment") or ""),
        "next_follow_up_after": str(item.get("next_follow_up_after") or ""),
        "source_message_id": int(item.get("source_message_id") or 0),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


def record_follow_up_result(
    thread_id: int,
    *,
    outcome: str,
    summary: str = "",
    adjustment: str = "",
    next_follow_up_after: str = "",
    source_message_id: int = 0,
) -> dict[str, object]:
    normalized = str(outcome or "").strip().lower()
    if normalized not in FOLLOW_UP_OUTCOMES:
        raise ValueError("跟进结果无效。")
    result_id = db.record_follow_up_result(
        thread_id,
        normalized,
        summary,
        adjustment,
        next_follow_up_after,
        source_message_id,
    )
    row = next((item for item in db.list_follow_up_results(thread_id=thread_id, limit=20) if int(item["id"]) == result_id), None)
    if row is None:
        raise RuntimeError("跟进结果写入后无法读取。")
    return public_follow_up_result(row)


def record_matching_follow_up_result(
    conversation_id: str,
    content: str,
    *,
    outcome: str,
    summary: str = "",
    adjustment: str = "",
    next_follow_up_after: str = "",
    source_message_id: int = 0,
) -> dict[str, object] | None:
    thread = db.find_open_pending_thread(conversation_id, content)
    if thread is None:
        return None
    return record_follow_up_result(
        int(thread["id"]),
        outcome=outcome,
        summary=summary,
        adjustment=adjustment,
        next_follow_up_after=next_follow_up_after,
        source_message_id=source_message_id,
    )


def build_follow_up_result_context(conversation_id: str, limit: int = 6) -> str:
    rows = db.list_follow_up_results(conversation_id=conversation_id, limit=limit)
    if not rows:
        return ""
    lines: list[str] = []
    for row in reversed(rows):
        item = public_follow_up_result(row)
        detail = item["summary"] or "用户没有补充说明"
        adjustment = f"；后续调整={item['adjustment']}" if item["adjustment"] else ""
        next_time = f"；下次跟进={item['next_follow_up_after']}" if item["next_follow_up_after"] else ""
        lines.append(
            f"- {item['thread_content']}：结果={item['outcome_label']}；反馈={detail}{adjustment}{next_time}"
        )
    return (
        "最近的现实行动回访：\n"
        + "\n".join(lines)
        + "\n后续建议要尊重这些真实结果和调整，不要把未完成说成完成，也不要机械重复原建议。"
    )


__all__ = [
    "FOLLOW_UP_OUTCOMES",
    "build_follow_up_result_context",
    "diary_lifecycle",
    "public_follow_up_result",
    "record_follow_up_result",
    "record_matching_follow_up_result",
]
