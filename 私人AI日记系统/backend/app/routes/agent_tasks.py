from ..repositories import agent_task_views_repository as repository
from fastapi import APIRouter, HTTPException, Query, Request
from urllib.parse import unquote
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel, Field
import json
from types import SimpleNamespace
from typing import Literal

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


class InputEdit(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    expected_revision: int = Field(ge=1)


class BrowserControl(BaseModel):
    expected_revision: int = Field(ge=1)
    visible: bool
    active: bool
    owner: str = Field(pattern='^(user|agent)$')


class BrowserSelection(BaseModel):
    x: float = Field(ge=0, le=20000)
    y: float = Field(ge=0, le=20000)
    expected_revision: int = Field(ge=1)


class BrowserUserAction(BaseModel):
    expected_revision: int = Field(ge=1)
    operation: Literal['click', 'type', 'press', 'scroll', 'back']
    x: float = Field(default=0, ge=0, le=20000)
    y: float = Field(default=0, ge=0, le=20000)
    value: str = Field(default='', max_length=12000)


class TerminalInput(BaseModel):
    text: str = Field(default='', max_length=12000)
    close: bool = False
    waiting_input: bool = False


def _context(task_id):
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(404, '找不到任务。')
    return SimpleNamespace(task_id=task_id, conversation_id=task['conversation_id'], source='desktop', run_id='owner-preview')


@router.get('/notifications')
async def all_task_notifications(conversation_id: str = ''):
    from ..repositories import agent_tasks_repository
    items = agent_tasks_repository.notifications(conversation_id=conversation_id)
    for item in items:
        item['target'] = {'task_id': item['task_id'], 'conversation_id': item['conversation_id']}
    return {'notifications': items}


@router.put('/{task_id}/inputs/{input_id}')
async def edit_task_input(task_id: str, input_id: int, payload: InputEdit):
    from ..agent_input_service import revise
    try:
        return revise(task_id, input_id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete('/{task_id}/inputs/{input_id}')
async def withdraw_task_input(task_id: str, input_id: int, expected_revision: int = Query(ge=1)):
    from ..agent_input_service import revise
    try:
        return revise(task_id, input_id, expected_revision=expected_revision, withdraw=True)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/{task_id}/browser/pages')
async def browser_pages(task_id: str):
    from .. import browser_service
    return await browser_service.pages(_context(task_id))


@router.put('/{task_id}/browser/pages/{page_id}/control')
async def browser_control(task_id: str, page_id: str, payload: BrowserControl):
    from .. import browser_service
    try:
        return await browser_service.set_control(page_id, _context(task_id), **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/{task_id}/browser/pages/{page_id}/frame')
async def browser_frame(task_id: str, page_id: str):
    from .. import browser_service
    try:
        return Response(await browser_service.frame(page_id, _context(task_id)), media_type='image/png', headers={'Cache-Control': 'no-store'})
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/{task_id}/browser/pages/{page_id}/select')
async def browser_select(task_id: str, page_id: str, payload: BrowserSelection):
    from .. import browser_service
    try:
        return await browser_service.select_element(page_id, _context(task_id), **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/{task_id}/browser/pages/{page_id}/user-action')
async def browser_user_action(task_id: str, page_id: str, payload: BrowserUserAction):
    from .. import browser_service
    try:
        return await browser_service.user_action(page_id, _context(task_id), **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/{task_id}/terminal/{job_id}/input')
async def terminal_input(task_id: str, job_id: str, payload: TerminalInput):
    from .. import terminal_service
    import asyncio
    try:
        return await asyncio.to_thread(terminal_service.send_input, _context(task_id), job_id, **payload.model_dump())
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/{task_id}/notifications')
async def task_notifications(task_id: str):
    _context(task_id)
    from ..repositories import agent_tasks_repository
    items = agent_tasks_repository.notifications(task_id)
    for item in items:
        item['target'] = {'task_id': task_id, 'conversation_id': item['conversation_id']}
    return {'notifications': items}


@router.get('/{task_id}/input-references/{reference_id}')
async def task_input_reference(task_id: str, reference_id: str, offset: int = Query(0, ge=0), max_chars: int = Query(12000, ge=100, le=16000)):
    from ..context_reference_service import read
    try:
        result = read(_context(task_id), reference_id, offset, max_chars)
        if result['kind'] == 'image':
            result['preview_url'] = f'/api/agent/work/{task_id}/input-references/{reference_id}/image'
        return result
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/{task_id}/input-references/{reference_id}/image')
async def task_input_reference_image(task_id: str, reference_id: str):
    from ..context_reference_service import image
    try:
        raw, media_type = image(_context(task_id), reference_id)
        return Response(raw, media_type=media_type, headers={'Cache-Control': 'no-store'})
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/{task_id}/notifications/{notification_id}/dismiss')
async def dismiss_task_notification(task_id: str, notification_id: int):
    _context(task_id)
    from ..repositories import agent_tasks_repository
    if not agent_tasks_repository.dismiss_notification(task_id, notification_id):
        raise HTTPException(404, '提醒不属于此任务。')
    return {'dismissed': True, 'resolved': False}


@router.post('/{task_id}/notifications/{notification_id}/present')
async def present_task_notification(task_id: str, notification_id: int):
    _context(task_id)
    from ..repositories import agent_tasks_repository
    if not agent_tasks_repository.present_notification(task_id, notification_id):
        raise HTTPException(404, '提醒不属于此任务。')
    return {'presented': True}


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
    with repository.transaction() as conn:
        row = conn.last_response((task['id'], task['conversation_id'])).fetchone()
    task['last_response'] = row['content'] if row else ''
    return task


def _run_for_task(task_id: str, run_id: str = ""):
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "找不到任务。")
    with repository.transaction() as conn:
        run_row = conn.task_run((task_id, run_id, run_id)).fetchone()
    if run_id and run_row is None:
        raise HTTPException(404, "执行轮次不属于该任务。")
    run_id = str(run_row["run_id"] if run_row else "")
    if not run_id:
        return task, None
    run = db.get_agent_run(run_id)
    if run is None:
        return task, None
    return task, run


def _decode_json(value: str, fallback):
    try:
        return json.loads(value or "null")
    except (TypeError, ValueError):
        return fallback


@router.get("/{task_id}/events")
async def get_work_events(task_id: str, after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), run_id: str = ""):
    _, run = _run_for_task(task_id, run_id)
    if run is None:
        return {"events": [], "next_id": after_id, "has_more": False, "run_id": None, "status": "legacy"}
    rows = db.list_agent_task_history(task_id, kind="events", run_id=run_id, after_id=after_id, limit=limit + 1)
    has_more = len(rows) > limit
    rows = rows[:limit]
    events = [{"id": int(row["id"]), "run_id": row["run_id"], "event_key": row["event_key"],
               "event_type": row["event_type"], "payload": _decode_json(row["payload_json"], {}),
               "created_at": row["created_at"]} for row in rows]
    next_id = events[-1]["id"] if events else after_id
    return {"events": events, "next_id": next_id, "has_more": has_more,
            "run_id": run["run_id"], "status": run["status"]}


@router.get("/{task_id}/messages")
async def get_work_messages(task_id: str, after_id: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), run_id: str = ""):
    _, run = _run_for_task(task_id, run_id)
    if run is None:
        return {"messages": [], "next_id": after_id, "has_more": False, "run_id": None}
    rows = db.list_agent_task_history(task_id, kind="messages", run_id=run_id, after_id=after_id, limit=limit + 1)
    has_more = len(rows) > limit
    rows = rows[:limit]
    messages = [{"id": int(row["id"]), "run_id": row["run_id"], "turn_index": int(row["turn_index"]),
                 "message_key": row["message_key"], "role": row["role"],
                 "content": _decode_json(row["content_json"], None),
                 "metadata": _decode_json(row["metadata_json"], {}), "created_at": row["created_at"]}
                for row in rows]
    next_id = messages[-1]["id"] if messages else after_id
    return {"messages": messages, "next_id": next_id, "has_more": has_more,
            "run_id": run["run_id"]}


@router.get('/{task_id}/stream')
async def stream_work(task_id: str,request: Request,after_id: int=Query(0,ge=0)):
    _run_for_task(task_id)
    from ..agent_progress_service import stream
    try: cursor=max(after_id,int(request.headers.get('last-event-id','0')))
    except ValueError: raise HTTPException(400,'进度游标无效。')
    return StreamingResponse(stream(task_id,request,cursor),media_type='text/event-stream',
        headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


@router.get('/{task_id}/inputs')
async def task_inputs(task_id: str,after_version: int=Query(0,ge=0),limit: int=Query(20,ge=1,le=50)):
    _run_for_task(task_id)
    import asyncio
    from ..agent_input_service import read
    return await asyncio.to_thread(read,task_id,after_version=after_version,limit=limit)


@router.get("/{task_id}/runs")
async def get_work_runs(task_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    _run_for_task(task_id)
    rows = db.list_agent_task_runs(task_id, limit=limit + 1, offset=offset)
    return {"runs": [{key: row[key] for key in ("run_id", "status", "created_at", "finished_at", "model_id", "model_calls", "tool_calls", "error")} for row in rows[:limit]],
            "has_more": len(rows) > limit, "next_offset": offset + min(limit, len(rows))}


@router.get("/{task_id}/citations")
async def get_work_citations(task_id: str, after_id: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    _run_for_task(task_id)
    from .. import agent_source_service
    import asyncio
    result = await asyncio.to_thread(agent_source_service.listing, task_id, after_id=after_id, limit=limit)
    with repository.transaction() as conn:
        rows = conn.reply_citations((task_id,)).fetchall()
    from ..agent_citation_service import decode_attachments
    replies = []
    for row in rows:
        for item in decode_attachments(row["attachments_json"]):
            if item.get("kind") == "citation_manifest":
                replies.append({"message_id": row["id"], "created_at": row["created_at"], **item})
    result["reply_citations"] = replies
    return result


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
