from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import autonomy_service


router = APIRouter(prefix="/api/agent/autonomy")


class AutonomyPolicyRequest(BaseModel):
    paused: bool | None = None
    autonomy_level: str | None = None
    quiet_start_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=None, ge=0, le=23)
    minimum_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    daily_behavior_limit: int | None = Field(default=None, ge=0, le=100)
    daily_budget_yuan: float | None = Field(default=None, ge=0, le=100)
    capability_overrides: dict[str, str] | None = None


class AgentGoalRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    conversation_id: str = Field(default="", max_length=120)
    autonomy_level: str = Field(default="", max_length=40)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    due_at: str = Field(default="", max_length=40)


class GoalStatusRequest(BaseModel):
    status: str


@router.get("")
async def autonomy_snapshot(limit: int = Query(default=100, ge=1, le=500)):
    return await asyncio.to_thread(autonomy_service.snapshot, limit=limit)


@router.patch("/policy")
async def update_autonomy_policy(payload: AutonomyPolicyRequest):
    try:
        changes = payload.model_dump(exclude_none=True)
        return await asyncio.to_thread(autonomy_service.update_policy, changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pause")
async def pause_autonomy():
    return await asyncio.to_thread(autonomy_service.update_policy, {"paused": True})


@router.post("/resume")
async def resume_autonomy():
    return await asyncio.to_thread(autonomy_service.update_policy, {"paused": False})


@router.post("/goals")
async def create_agent_goal(payload: AgentGoalRequest):
    try:
        return await asyncio.to_thread(
            autonomy_service.create_goal,
            payload.title,
            description=payload.description,
            conversation_id=payload.conversation_id,
            autonomy_level=payload.autonomy_level,
            capabilities=payload.capabilities,
            due_at=payload.due_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/goals/{goal_id}/status")
async def update_agent_goal_status(goal_id: int, payload: GoalStatusRequest):
    try:
        return await asyncio.to_thread(autonomy_service.set_goal_status, goal_id, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/behaviors/{behavior_id}/approve")
async def approve_autonomy_behavior(behavior_id: int):
    try:
        return await autonomy_service.approve_behavior(behavior_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/behaviors/{behavior_id}/cancel")
async def cancel_autonomy_behavior(behavior_id: int):
    try:
        return await asyncio.to_thread(autonomy_service.cancel_behavior, behavior_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
