from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import environment_check_service, onboarding_service


router = APIRouter(prefix="/api/onboarding")


@router.get("/status")
async def onboarding_status():
    return await asyncio.to_thread(onboarding_service.onboarding_status)


@router.get("/environment")
async def onboarding_environment():
    try:
        return await asyncio.to_thread(environment_check_service.environment_status)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"环境检查失败：{exc}") from exc


@router.post("/complete")
async def complete_onboarding(payload: dict[str, object]):
    try:
        return await asyncio.to_thread(onboarding_service.complete_onboarding, payload)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
