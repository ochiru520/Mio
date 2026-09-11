from fastapi import APIRouter, HTTPException, Query, Request
from urllib.parse import unquote
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import agent_task_service as tasks, db
from .. import agent_file_service as files
from ..companion_action_service import approve_companion_action

router = APIRouter(prefix="/work", tags=["agent-tasks"])


class TaskLimits(BaseModel):
    enabled: bool = False
    model_calls: int = Field(8, ge=2, le=24)
    tool_calls: int = Field(20, ge=1, le=100)
    seconds: int = Field(180, ge=15, le=1800)
    cost_yuan: float = Field(2, ge=0.01, le=100)


class FileRoots(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=30)


@router.get("/file-roots")
async def get_file_roots():
    return {"roots": files.roots(), "output_root": str(files.workspace())}


@router.put("/file-roots")
async def set_file_roots(payload: FileRoots):
    try:
        return {"roots": files.roots(payload.paths), "output_root": str(files.workspace())}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/files/{task_id}/{name}")
async def download_file(task_id: str, name: str, request: Request):
    if tasks.get(task_id) is None:
        raise HTTPException(404, "找不到任务。")
    try:
        # Decode exactly once; some ASGI adapters already decode scope.path twice.
        raw_path = request.scope.get("raw_path", b"").split(b"?", 1)[0]
        if raw_path:
            name = unquote(raw_path.rsplit(b"/", 1)[-1].decode("ascii"))
        path = files.output_file(task_id, name)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, "找不到输出文件。") from exc
    return FileResponse(path, filename=path.name)


def detail(task_id: str):
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "找不到任务。")
    task.pop("context", None)
    task.pop("allowed_tools", None)
    task["observations"] = tasks.observations(task_id)
    return task


@router.get("")
async def list_work(conversation_id: str = "", limit: int = Query(50, ge=1, le=200), status: str = "all", offset: int = Query(0, ge=0)):
    states = ("running", "responding", "ready", "waiting_jobs", "waiting_confirmation", "waiting_user", "paused", "failed", "budget_exhausted") if status == "active" else ()
    items = tasks.list_tasks(conversation_id, limit, states=states, offset=offset)
    more = bool(tasks.list_tasks(conversation_id, 1, states=states, offset=offset + limit))
    return {"tasks": [detail(task["id"]) for task in items], "limits": tasks.limits(), "has_more": more}


@router.put("/limits")
async def set_limits(payload: TaskLimits):
    return tasks.limits(payload.model_dump())


@router.get("/{task_id}")
async def get_work(task_id: str):
    return detail(task_id)


@router.get("/handoff/source/{conversation_id}")
async def get_source_handoff(conversation_id: str):
    from ..agent_handoff_service import latest_handoff
    return {"handoff": latest_handoff(conversation_id)}


@router.post("/{task_id}/pause")
async def pause_work(task_id: str):
    try:
        await tasks.pause(task_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return detail(task_id)


@router.post("/{task_id}/cancel")
async def cancel_work(task_id: str):
    try:
        await tasks.pause(task_id, cancel_jobs=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return detail(task_id)


@router.post("/{task_id}/resume")
async def resume_work(task_id: str):
    try:
        tasks.resume(task_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return detail(task_id)


@router.post("/{task_id}/actions/{action_id}/approve")
async def approve_action(task_id: str, action_id: int):
    detail(task_id)
    action = db.get_companion_action(action_id)
    parent = tasks.for_run(str(action["agent_run_id"])) if action else None
    if parent is None or parent["id"] != task_id:
        raise HTTPException(404, "确认项不属于该任务。")
    try:
        await approve_companion_action(action_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return detail(task_id)
