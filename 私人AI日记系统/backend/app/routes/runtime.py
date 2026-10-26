"""Mode strategies and factual operation/usage views shared by the desktop modes."""
from datetime import datetime, timedelta
import math

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, model_runtime as runtime
from ..repositories import runtime_views_repository as repository
from ..config import SettingsConflictError

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


class PolicyChange(BaseModel):
    revision: int = Field(ge=0)
    selection: str
    model_id: str = Field(default="", max_length=200)
    reasoning_level: str = Field(default="inherit", max_length=40)
    enabled: bool = True


@router.get("/policies")
async def policies():
    return {"policies": [runtime.model_policy(mode) for mode in runtime.MODES]}


@router.put("/policies/{mode}")
async def update_policy(mode: str, payload: PolicyChange):
    try:
        values = payload.model_dump(exclude={"revision"})
        return runtime.save_model_policy(mode, values, expected_revision=payload.revision)
    except SettingsConflictError as exc:
        raise HTTPException(409, {"code": "settings_conflict", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/operations")
async def operations(mode: str = "", conversation_id: str = "", task_id: str = "", request_id: str = "",
                     limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    result, has_more = repository.operations(
        mode=mode, conversation_id=conversation_id, task_id=task_id,
        request_id=request_id, limit=limit, offset=offset)
    return {"operations": result, "has_more": has_more, "active": runtime.live_operations()}


@router.get("/usage")
async def usage(days: int = Query(30, ge=1, le=365)):
    start = (datetime.fromisoformat(db.now_iso()) - timedelta(days=days)).isoformat(timespec="seconds")
    from ..repositories import prompt_metrics_repository
    return {"days": days, **prompt_metrics_repository.usage_summary(start, runtime.MODES)}
