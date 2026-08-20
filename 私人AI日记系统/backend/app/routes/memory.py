from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import db
from ..config import settings
from ..context_service import SUMMARY_TYPE, _strip_summary_marker, _summary_last_message_id, _summary_with_marker
from ..documents import read_text_with_fallback
from ..life_loop_service import public_follow_up_result, record_follow_up_result
from ..mio_profile import load_mio_profile, save_mio_profile
from ..memory_service import public_memory_item, save_memory_item
from ..web import templates


router = APIRouter()


class MemoryTextRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class PendingThreadRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    conversation_id: str = Field(default="default", max_length=120)
    follow_up_after: str = Field(default="", max_length=40)


class FollowUpResultRequest(BaseModel):
    outcome: str = Field(pattern=r"^(completed|partial|not_completed)$")
    summary: str = Field(default="", max_length=800)
    adjustment: str = Field(default="", max_length=500)
    next_follow_up_after: str = Field(default="", max_length=40)


class ConversationSummaryRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=12000)


class StructuredMemoryRequest(BaseModel):
    layer: str = Field(default="L0", max_length=2)
    category: str = Field(default="preference", max_length=40)
    memory_key: str = Field(default="", max_length=80)
    content: str = Field(min_length=1, max_length=800)
    confidence: float = Field(default=1.0, ge=0, le=1)
    conversation_id: str = Field(default="default", max_length=120)


def _normalize_follow_up(value: str) -> str:
    clean = value.strip()
    if not clean:
        return ""
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="跟进时间格式不正确。") from exc
    local_tz = ZoneInfo(settings.timezone)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(local_tz).isoformat(timespec="seconds")


def _conversation_label(conversation_id: str) -> str:
    if conversation_id.startswith("qq_private_"):
        return "QQ 私聊"
    if conversation_id.startswith("qq_group_"):
        return "QQ 群聊"
    if conversation_id == "default":
        return "网页"
    return conversation_id


def _memory_data() -> dict[str, object]:
    threads = [
        {**dict(row), "conversation_label": _conversation_label(str(row["conversation_id"]))}
        for row in db.list_all_open_pending_threads(limit=50)
    ]
    summaries = [
        {
            "conversation_id": str(row["tags"] or ""),
            "conversation_label": _conversation_label(str(row["tags"] or "")),
            "content": _strip_summary_marker(str(row["content"] or "")),
            "updated_at": str(row["updated_at"]),
        }
        for row in db.list_memories_by_type(SUMMARY_TYPE)
    ]
    runtime_content = ""
    runtime_error = ""
    if settings.runtime_summary_path.exists():
        try:
            runtime_content = read_text_with_fallback(settings.runtime_summary_path)
        except OSError as exc:
            runtime_error = str(exc)
    else:
        runtime_error = "文件不存在"
    return {
        "threads": threads,
        "summaries": summaries,
        "structured": [public_memory_item(row) for row in db.list_structured_memories(status="active", limit=200)],
        "structured_candidates": [
            public_memory_item(row)
            for row in db.list_structured_memories(status="candidate", limit=100)
        ],
        "structured_sleeping": [
            public_memory_item(row)
            for row in db.list_structured_memories(status="sleeping", limit=100)
        ],
        "structured_history": [
            public_memory_item(row)
            for row in db.list_structured_memories(status="", limit=100)
            if str(row["status"]) != "active"
        ],
        "follow_up_results": [
            public_follow_up_result(row)
            for row in db.list_follow_up_results(limit=50)
        ],
        "profile": load_mio_profile(),
        "runtime_summary": {
            "path": str(settings.runtime_summary_path),
            "content": runtime_content,
            "error": runtime_error,
            "updated_at": (
                datetime.fromtimestamp(settings.runtime_summary_path.stat().st_mtime).astimezone().isoformat()
                if settings.runtime_summary_path.exists()
                else ""
            ),
        },
    }


@router.get("/memory", response_class=HTMLResponse)
async def memory_page(request: Request):
    data = _memory_data()
    return templates.TemplateResponse(
        "记忆手账.html",
        {
            "request": request,
            **data,
        },
    )


@router.get("/api/memory")
async def api_memory():
    return _memory_data()


@router.put("/api/memory/runtime-summary")
async def api_update_runtime_summary(payload: MemoryTextRequest):
    content = payload.content.strip()
    settings.runtime_summary_path.parent.mkdir(parents=True, exist_ok=True)
    settings.runtime_summary_path.write_text(content + "\n", encoding="utf-8")
    return {"saved": True, "path": str(settings.runtime_summary_path), "content": content}


@router.post("/api/memory/items")
async def api_create_memory_item(payload: StructuredMemoryRequest):
    try:
        saved = save_memory_item(
            layer=payload.layer,
            category=payload.category,
            memory_key=payload.memory_key,
            content=payload.content,
            source_conversation_id=payload.conversation_id.strip() or "default",
            confidence=payload.confidence,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = db.get_structured_memory(int(saved["id"]))
    return {"saved": True, "outcome": saved["outcome"], "memory": public_memory_item(row)}


@router.delete("/api/memory/items/{memory_id}")
async def api_archive_memory_item(memory_id: int):
    if not db.archive_structured_memory(memory_id):
        raise HTTPException(status_code=404, detail="没有找到这条有效记忆。")
    return {"archived": True, "id": memory_id}


@router.post("/api/memory/items/{memory_id}/confirm")
async def api_confirm_memory_candidate(memory_id: int):
    if not db.confirm_structured_memory_candidate(memory_id):
        raise HTTPException(status_code=404, detail="没有找到这条待确认记忆。")
    row = db.get_structured_memory(memory_id)
    return {"confirmed": True, "memory": public_memory_item(row)}


@router.post("/api/memory/items/{memory_id}/reject")
async def api_reject_memory_candidate(memory_id: int):
    row = db.get_structured_memory(memory_id)
    if row is None or str(row["status"]) != "candidate":
        raise HTTPException(status_code=404, detail="没有找到这条待确认记忆。")
    db.set_structured_memory_status(memory_id, "archived")
    return {"rejected": True, "id": memory_id}


@router.post("/api/memory/items/{memory_id}/sleep")
async def api_sleep_memory_item(memory_id: int):
    row = db.get_structured_memory(memory_id)
    if row is None or str(row["status"]) != "active":
        raise HTTPException(status_code=404, detail="没有找到这条有效记忆。")
    db.set_structured_memory_status(memory_id, "sleeping")
    return {"sleeping": True, "id": memory_id}


@router.post("/api/memory/items/{memory_id}/wake")
async def api_wake_memory_item(memory_id: int):
    row = db.get_structured_memory(memory_id)
    if row is None or str(row["status"]) != "sleeping":
        raise HTTPException(status_code=404, detail="没有找到这条沉睡记忆。")
    db.set_structured_memory_status(memory_id, "active")
    return {"active": True, "memory": public_memory_item(db.get_structured_memory(memory_id))}


@router.post("/api/memory/items/{memory_id}/restore")
async def api_restore_memory_item(memory_id: int):
    if not db.restore_structured_memory(memory_id):
        raise HTTPException(status_code=404, detail="没有找到可恢复的旧记忆版本。")
    return {"restored": True, "memory": public_memory_item(db.get_structured_memory(memory_id))}


@router.post("/api/threads")
async def api_create_thread(payload: PendingThreadRequest):
    thread_id = db.remember_pending_thread(
        payload.conversation_id.strip() or "default",
        payload.content,
        _normalize_follow_up(payload.follow_up_after),
    )
    return {"id": thread_id, "saved": True}


@router.put("/api/threads/{thread_id}")
async def api_update_thread(thread_id: int, payload: PendingThreadRequest):
    if not db.update_pending_thread_by_id(thread_id, payload.content, _normalize_follow_up(payload.follow_up_after)):
        raise HTTPException(status_code=404, detail="没有找到这个待跟进话题。")
    return {"id": thread_id, "saved": True}


@router.post("/api/threads/{thread_id}/resolve")
async def api_resolve_thread(thread_id: int):
    try:
        result = record_follow_up_result(
            thread_id,
            outcome="completed",
            summary="在记忆页标记为已完成。",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": thread_id, "resolved": True, "result": result}


@router.post("/api/threads/{thread_id}/result")
async def api_record_follow_up_result(thread_id: int, payload: FollowUpResultRequest):
    next_follow_up_after = (
        _normalize_follow_up(payload.next_follow_up_after)
        if payload.outcome != "completed"
        else ""
    )
    try:
        result = record_follow_up_result(
            thread_id,
            outcome=payload.outcome,
            summary=payload.summary,
            adjustment=payload.adjustment,
            next_follow_up_after=next_follow_up_after,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"saved": True, "result": result}


@router.delete("/api/threads/{thread_id}")
async def api_delete_thread(thread_id: int):
    if not db.delete_pending_thread_by_id(thread_id):
        raise HTTPException(status_code=404, detail="没有找到这个待跟进话题。")
    return {"id": thread_id, "deleted": True}


@router.put("/api/memory/conversation-summary")
async def api_update_conversation_summary(payload: ConversationSummaryRequest):
    conversation_id = payload.conversation_id.strip()
    existing = db.get_latest_memory(SUMMARY_TYPE, tags=conversation_id)
    last_message_id = _summary_last_message_id(str(existing["content"] or "")) if existing else 0
    content = payload.content.strip()
    stored = _summary_with_marker(content, last_message_id) if last_message_id else content
    db.replace_memory(SUMMARY_TYPE, stored, importance=4, tags=conversation_id)
    return {"saved": True, "conversation_id": conversation_id, "content": content}


@router.delete("/api/memory/conversation-summary/{conversation_id}")
async def api_delete_conversation_summary(conversation_id: str):
    if not db.delete_memory(SUMMARY_TYPE, tags=conversation_id):
        raise HTTPException(status_code=404, detail="没有找到这份长期印象。")
    return {"deleted": True, "conversation_id": conversation_id}


def _profile_notes() -> tuple[dict[str, object], list[str]]:
    profile = load_mio_profile()
    preferences = profile.setdefault("preferences", {})
    raw_notes = preferences.setdefault("custom_notes", []) if isinstance(preferences, dict) else []
    notes = [str(note) for note in raw_notes if str(note).strip()]
    return profile, notes


@router.post("/api/memory/profile-notes")
async def api_add_profile_note(payload: MemoryTextRequest):
    profile, notes = _profile_notes()
    content = payload.content.strip()
    if content not in notes:
        notes.append(content)
    profile["preferences"]["custom_notes"] = notes
    saved = save_mio_profile(profile)
    return {"saved": True, "profile": saved}


@router.put("/api/memory/profile-notes/{note_index}")
async def api_update_profile_note(note_index: int, payload: MemoryTextRequest):
    profile, notes = _profile_notes()
    if note_index < 0 or note_index >= len(notes):
        raise HTTPException(status_code=404, detail="没有找到这条记忆。")
    notes[note_index] = payload.content.strip()
    profile["preferences"]["custom_notes"] = notes
    saved = save_mio_profile(profile)
    return {"saved": True, "profile": saved}


@router.delete("/api/memory/profile-notes/{note_index}")
async def api_delete_profile_note(note_index: int):
    profile, notes = _profile_notes()
    if note_index < 0 or note_index >= len(notes):
        raise HTTPException(status_code=404, detail="没有找到这条记忆。")
    notes.pop(note_index)
    profile["preferences"]["custom_notes"] = notes
    saved = save_mio_profile(profile)
    return {"deleted": True, "profile": saved}
