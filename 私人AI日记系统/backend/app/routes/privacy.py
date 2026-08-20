from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import privacy_service


router = APIRouter(prefix="/api/privacy")


@router.get("/status")
async def privacy_status():
    return await asyncio.to_thread(privacy_service.privacy_status)


@router.post("/pause")
async def pause_sensitive_capabilities():
    try:
        return await privacy_service.pause_sensitive_capabilities()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"暂停敏感能力失败：{exc}") from exc


@router.post("/resume")
async def resume_sensitive_capabilities():
    try:
        return await asyncio.to_thread(privacy_service.resume_sensitive_capabilities)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"恢复敏感能力失败：{exc}") from exc
