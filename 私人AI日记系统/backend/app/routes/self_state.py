from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import route_observation_service, self_snapshot_service


router = APIRouter(prefix="/self")


class ActiveViewReportRequest(BaseModel):
    view_id: str
    section_id: str = ""
    visible: bool = True


def _parse_scopes(raw: str) -> tuple[str, ...] | None:
    if not raw.strip():
        return None
    requested = tuple(
        dict.fromkeys(part.strip().lower() for part in raw.split(",") if part.strip())
    )
    unknown = sorted(set(requested) - self_snapshot_service.SNAPSHOT_SCOPES)
    if unknown:
        raise ValueError(f"不支持的 SelfSnapshot 范围：{', '.join(unknown)}")
    return requested


@router.get("/state")
async def self_state(scopes: str = Query(default="", max_length=300)):
    try:
        selected = _parse_scopes(scopes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await asyncio.to_thread(self_snapshot_service.build_self_snapshot, selected)


@router.get("/capabilities")
async def self_capabilities():
    snapshot = await asyncio.to_thread(
        self_snapshot_service.build_self_snapshot,
        ("capabilities",),
    )
    return {"capabilities": snapshot["capabilities"], "generated_at": snapshot["generated_at"]}


@router.get("/active-view")
async def self_active_view():
    return self_snapshot_service.get_active_view()


@router.post("/active-view")
async def report_self_active_view(payload: ActiveViewReportRequest):
    try:
        return self_snapshot_service.report_active_view(
            payload.view_id,
            section_id=payload.section_id,
            visible=payload.visible,
            source="main_app",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/service-health")
async def self_service_health():
    return await asyncio.to_thread(self_snapshot_service.get_service_health)


@router.get("/last-route")
async def self_last_route():
    snapshot = await asyncio.to_thread(
        self_snapshot_service.build_self_snapshot,
        ("last_route",),
    )
    return {"last_route": snapshot["last_route"], "generated_at": snapshot["generated_at"]}


@router.get("/route-metrics")
async def self_route_metrics(task_type: str = Query(default="", max_length=60)):
    return {
        "task_type": task_type.strip(),
        "models": await asyncio.to_thread(
            route_observation_service.model_performance_snapshot,
            task_type.strip(),
        ),
    }


__all__ = ["router"]
