from __future__ import annotations
from ..repositories import conversation_lifecycle_repository as repository

import asyncio
import uuid
from types import SimpleNamespace
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import conversation_service, db
from ..chat_service import cancel_scheduled_companion_actions
from ..conversation_runtime import chat_run_coordinator


router = APIRouter()


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=60)
    workspace: Literal["main", "agent"] = "main"


class ConversationUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class ConversationMessageDeleteRequest(BaseModel):
    message_ids: list[int] = Field(min_length=1, max_length=100)


class SidebarState(BaseModel):
    pinned: bool | None = None
    unread: bool | None = None
    archived: bool | None = None


class ConversationRewindRequest(BaseModel):
    anchor_message_id: int = Field(ge=1)
    mode: Literal['edit', 'regenerate'] = 'regenerate'
    content: str = Field(default='', max_length=200000)
    expected_latest_message_id: int | None = Field(default=None, ge=0)


@router.post('/conversations/{conversation_id}/rewind')
async def rewind_conversation(conversation_id: str, payload: ConversationRewindRequest):
    from .. import agent_task_service, model_runtime
    from ..conversation_state_service import rewind, validate_rewind
    try:
        await asyncio.to_thread(validate_rewind, conversation_id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await model_runtime.cancel_operations(conversation_id=conversation_id, reason='history_rewind')
    await chat_run_coordinator.cancel(conversation_id, reason='history_rewind')
    await cancel_scheduled_companion_actions(conversation_id)
    await agent_task_service.stop_tasks(conversation_id, cancel_jobs=False)
    try:
        return await asyncio.to_thread(rewind, conversation_id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/conversations/{conversation_id}/turn-index')
async def conversation_turn_index(conversation_id: str, before_id: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=1000)):
    from ..conversation_state_service import turn_index
    try:
        return await asyncio.to_thread(turn_index, conversation_id, before_id, limit)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/conversations/{conversation_id}/input-references/{reference_id}')
async def read_input_reference(conversation_id: str, reference_id: str, offset: int = Query(0, ge=0),
                               limit: int | None = Query(None, ge=100, le=16000), max_chars: int = Query(12000, ge=100, le=16000)):
    from ..conversation_service import resolve_conversation_id
    from ..context_reference_service import read
    try:
        identifier = resolve_conversation_id(conversation_id)
        db.assert_conversation_writable(identifier)
        result = await asyncio.to_thread(read, SimpleNamespace(conversation_id=identifier, task_id=''), reference_id, offset, limit if limit is not None else max_chars)
        if result['kind'] == 'image':
            result['preview_url'] = f'/api/agent/conversations/{quote(identifier, safe="")}/input-references/{quote(reference_id, safe="")}/image'
        return result
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/conversations/{conversation_id}/input-references/{reference_id}/image')
async def input_reference_image(conversation_id: str, reference_id: str):
    from ..conversation_service import resolve_conversation_id
    from ..context_reference_service import image
    try:
        identifier = resolve_conversation_id(conversation_id)
        db.assert_conversation_writable(identifier)
        raw, media_type = await asyncio.to_thread(image, SimpleNamespace(conversation_id=identifier, task_id=''), reference_id)
        return Response(raw, media_type=media_type, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch('/conversations/{conversation_id}/state')
async def save_sidebar_state(conversation_id: str, payload: SidebarState):
    from ..conversation_state_service import update
    try: return update(conversation_id, **payload.model_dump(exclude_none=True))
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc


@router.post('/conversations/{conversation_id}/fork')
async def fork_conversation(conversation_id: str):
    from ..conversation_state_service import fork
    try: return await asyncio.to_thread(fork,conversation_id)
    except (ValueError,OSError) as exc: raise HTTPException(400,str(exc)) from exc


@router.get('/conversations/{conversation_id}/export')
async def export_conversation(conversation_id: str):
    from ..conversation_state_service import markdown
    try: return {'markdown':markdown(conversation_id)}
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc


@router.get("/conversations")
async def list_conversations():
    return conversation_service.list_conversations()


@router.post("/conversations")
async def create_conversation(payload: ConversationCreateRequest):
    prefix = "desktop_agent_" if payload.workspace == "agent" else "desktop_"
    conversation_id = f"{prefix}{uuid.uuid4().hex}"
    row = db.create_agent_conversation(conversation_id, payload.title)
    kind = "agent" if payload.workspace == "agent" else "desktop"
    return {**dict(row), "kind": kind, "preview": ""}


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(conversation_id: str, payload: ConversationUpdateRequest):
    if not conversation_id.startswith("desktop_"):
        raise HTTPException(status_code=400, detail="QQ 共享对话不能重命名。")
    try:
        row = db.rename_agent_conversation(conversation_id, payload.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="没有找到这个对话窗口。")
    kind = "agent" if conversation_id.startswith("desktop_agent_") else "desktop"
    return {**dict(row), "kind": kind}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    if not conversation_id.startswith("desktop_"):
        raise HTTPException(status_code=400, detail="QQ 共享对话不能删除。")
    from .. import agent_task_service, creation_service
    # Persist the gate before the first await so late writers cannot recreate messages.
    with repository.transaction() as conn:
        if conn.conversation_exists((conversation_id,)).fetchone() is None:
            raise HTTPException(404, "没有找到这个对话窗口。")
        conn.mark_deleted((conversation_id,))
    try:
        from .. import model_runtime
        await model_runtime.cancel_operations(conversation_id=conversation_id, reason="conversation_deleted")
        await chat_run_coordinator.cancel(conversation_id, reason="conversation_deleted")
        await cancel_scheduled_companion_actions(conversation_id)
        await agent_task_service.stop_tasks(conversation_id, cancel_jobs=True)
        with repository.transaction() as conn:
            jobs = conn.pending_jobs((conversation_id,)).fetchall()
            input_reference_scopes = [conversation_id, *conn.task_ids(conversation_id)]
        for job in jobs:
            await creation_service.cancel_job(job["id"])
        attachment_records = db.list_conversation_attachment_records(conversation_id)
    except BaseException as exc:
        with repository.transaction() as conn:
            conn.unmark_deleted((conversation_id,))
        if isinstance(exc, asyncio.CancelledError):
            raise
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(409, "对话尚未删除：停止关联任务失败。对话已恢复可用，可检查服务后重试删除。") from exc
    transaction = None
    try:
        transaction = conversation_service.stage_archived_attachments(attachment_records, strict=True)
    except OSError as exc:
        with repository.transaction() as conn:
            conn.unmark_deleted((conversation_id,))
        raise HTTPException(
            status_code=409,
            detail=f"对话未删除，因为{exc}。请关闭占用文件的程序后重试。",
        ) from exc
    try:
        if not db.delete_agent_conversation(conversation_id):
            raise HTTPException(status_code=404, detail="没有找到这个对话窗口。")
    except Exception as database_error:
        with repository.transaction() as conn:
            conn.unmark_deleted((conversation_id,))
        try:
            transaction.rollback()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"对话删除失败，附件回滚也失败：{exc}") from exc
        if isinstance(database_error, HTTPException):
            raise
        raise HTTPException(
            status_code=500,
            detail=f"对话未删除，附件已恢复：{database_error}",
        ) from database_error
    from ..repositories import context_offload_repository
    for scope in input_reference_scopes:
        await asyncio.to_thread(context_offload_repository.delete_scope, scope)
    try:
        attachment_cleanup = transaction.commit()
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"对话已删除，但附件清理未完成，请检查数据目录：{exc}",
        ) from exc
    return {"ok": True, "conversation_id": conversation_id, "attachment_cleanup": attachment_cleanup}


@router.post("/conversations/{conversation_id}/messages/delete")
async def delete_shared_conversation_messages(
    conversation_id: str,
    payload: ConversationMessageDeleteRequest,
):
    if conversation_id != conversation_service.primary_conversation_id():
        raise HTTPException(status_code=400, detail="只能清理当前 QQ 共享对话中的消息。")

    cancelled_runs = await chat_run_coordinator.cancel(
        conversation_id,
        reason="conversation_messages_deleted",
    )
    cancelled_background_actions = await cancel_scheduled_companion_actions(conversation_id)
    message_ids = sorted({int(message_id) for message_id in payload.message_ids if int(message_id) > 0})
    attachment_records = db.list_message_attachment_records(conversation_id, message_ids)
    try:
        transaction = conversation_service.stage_archived_attachments(attachment_records, strict=True)
    except conversation_service.AttachmentCleanupError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"消息未清理，因为{exc}。请关闭占用文件的程序后重试。",
        ) from exc

    try:
        deleted = db.delete_conversation_messages(conversation_id, message_ids)
    except Exception as database_error:
        try:
            transaction.rollback()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"消息清理失败，附件回滚也失败：{exc}") from exc
        if isinstance(database_error, HTTPException):
            raise
        raise HTTPException(
            status_code=500,
            detail=f"消息未清理，附件已恢复：{database_error}",
        ) from database_error
    if int(deleted.get("messages") or 0) <= 0:
        transaction.rollback()
        raise HTTPException(status_code=404, detail="没有找到要清理的 QQ 消息。")

    try:
        attachment_cleanup = transaction.commit()
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"消息已清理，但附件清理未完成，请检查数据目录：{exc}",
        ) from exc

    return {
        "ok": True,
        "conversation_id": conversation_id,
        "cancelled_runs": cancelled_runs,
        "cancelled_background_actions": cancelled_background_actions,
        "deleted": deleted,
        "attachment_cleanup": attachment_cleanup,
        "preserved": ["未选择的消息", "人格", "日记", "今日状态", "结构化长期记忆", "已完成的现实回访"],
    }
