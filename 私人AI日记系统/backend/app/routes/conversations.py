from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
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
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM agent_conversations WHERE id=?", (conversation_id,)).fetchone() is None:
            raise HTTPException(404, "没有找到这个对话窗口。")
        conn.execute("INSERT OR IGNORE INTO deleted_conversations VALUES(?)", (conversation_id,))
    try:
        from .. import model_runtime
        await model_runtime.cancel_operations(conversation_id=conversation_id, reason="conversation_deleted")
        await chat_run_coordinator.cancel(conversation_id, reason="conversation_deleted")
        await cancel_scheduled_companion_actions(conversation_id)
        await agent_task_service.stop_tasks(conversation_id, cancel_jobs=True)
        with db.get_conn() as conn:
            jobs = conn.execute("SELECT id FROM creation_jobs WHERE conversation_id=? AND status NOT IN ('completed','failed','cancelled','timed_out')", (conversation_id,)).fetchall()
        for job in jobs:
            await creation_service.cancel_job(job["id"])
        attachment_records = db.list_conversation_attachment_records(conversation_id)
    except BaseException as exc:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM deleted_conversations WHERE id=?", (conversation_id,))
        if isinstance(exc, asyncio.CancelledError):
            raise
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(409, "对话尚未删除：停止关联任务失败。对话已恢复可用，可检查服务后重试删除。") from exc
    transaction = None
    try:
        transaction = conversation_service.stage_archived_attachments(attachment_records, strict=True)
    except conversation_service.AttachmentCleanupError as exc:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM deleted_conversations WHERE id=?", (conversation_id,))
        raise HTTPException(
            status_code=409,
            detail=f"对话未删除，因为{exc}。请关闭占用文件的程序后重试。",
        ) from exc
    try:
        if not db.delete_agent_conversation(conversation_id):
            raise HTTPException(status_code=404, detail="没有找到这个对话窗口。")
    except Exception as database_error:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM deleted_conversations WHERE id=?", (conversation_id,))
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
