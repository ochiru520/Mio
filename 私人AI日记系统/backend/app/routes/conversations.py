from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import conversation_service, db


router = APIRouter()


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=60)


class ConversationUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)


@router.get("/conversations")
async def list_conversations():
    return conversation_service.list_conversations()


@router.post("/conversations")
async def create_conversation(payload: ConversationCreateRequest):
    conversation_id = f"desktop_{uuid.uuid4().hex}"
    row = db.create_agent_conversation(conversation_id, payload.title)
    return {**dict(row), "kind": "desktop", "preview": ""}


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
    return {**dict(row), "kind": "desktop"}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    if not conversation_id.startswith("desktop_"):
        raise HTTPException(status_code=400, detail="QQ 共享对话不能删除。")
    attachment_records = db.list_conversation_attachment_records(conversation_id)
    transaction = None
    try:
        transaction = conversation_service.stage_archived_attachments(attachment_records, strict=True)
    except conversation_service.AttachmentCleanupError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"对话未删除，因为{exc}。请关闭占用文件的程序后重试。",
        ) from exc
    try:
        if not db.delete_agent_conversation(conversation_id):
            raise HTTPException(status_code=404, detail="没有找到这个对话窗口。")
    except Exception as database_error:
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
