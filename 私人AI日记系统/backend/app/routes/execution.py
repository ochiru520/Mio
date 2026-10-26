"""Owner-only local execution configuration and previews; not mounted on mobile."""
from ..repositories import execution_history_repository as repository
import asyncio
from types import SimpleNamespace
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from .. import db, workspace_service as workspace, code_workspace_service as code
from .. import desktop_control_service as desktop, terminal_service as terminal
from .. import execution_capability_defaults_service as defaults

router = APIRouter(prefix='/api/workspace/execution', tags=['local-execution'])


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CapabilityDefaults(Input):
    expected_revision: int = Field(ge=1)
    read_enabled: bool = True
    write_enabled: bool = True
    execute_enabled: bool = True
    desktop_enabled: bool = True


class CodeConfig(Input):
    root: str = Field(min_length=1, max_length=2000)
    read_enabled: bool = False
    write_enabled: bool = False
    execute_enabled: bool = False
    verification: dict[str, str] = Field(default_factory=dict)
    expected_revision: int = Field(ge=0)


class DesktopConfig(Input):
    enabled: bool = False
    windows: list[dict] = Field(default_factory=list, max_length=30)
    executables: list[str] = Field(default_factory=list, max_length=30)
    expected_revision: int = Field(ge=0)


async def call(function, *args, **kwargs):
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


def context(project_id, conversation_id, task_id=''):
    project = workspace.project_for_conversation(conversation_id)
    if not project or project['id'] != project_id or not conversation_id.startswith('desktop_agent_'):
        raise ValueError('对话不属于此 Agent 项目。')
    if task_id:
        from ..agent_task_service import get
        task = get(task_id)
        if not task or task['conversation_id'] != conversation_id:
            raise ValueError('任务不属于当前会话。')
    return SimpleNamespace(conversation_id=conversation_id, task_id=task_id, source='desktop', run_id='owner-preview')


@router.get('/defaults')
async def capability_defaults():
    return await call(defaults.configuration)


@router.put('/defaults')
async def save_capability_defaults(payload: CapabilityDefaults):
    from .. import maintenance_service
    try:
        with maintenance_service.mutation_scope():
            return await asyncio.to_thread(defaults.configure, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(409 if '另一个窗口' in str(exc) else 400, str(exc)) from exc


@router.get('/projects/{project_id}')
async def project_config(project_id: str):
    return {'code': await call(code.configuration, project_id), 'desktop': await call(desktop.configuration, project_id),
            'shells': terminal.shells()}


@router.put('/projects/{project_id}/code')
async def code_config(project_id: str, payload: CodeConfig):
    from .. import maintenance_service
    with maintenance_service.mutation_scope():
        return await call(code.configure, project_id, **payload.model_dump())


@router.put('/projects/{project_id}/desktop')
async def desktop_config(project_id: str, payload: DesktopConfig):
    from .. import maintenance_service
    with maintenance_service.mutation_scope():
        return await call(desktop.configure, project_id, **payload.model_dump())


@router.get('/desktop/windows')
async def desktop_windows():
    return await call(desktop.rpc, 'windows', {})


@router.get('/desktop/observations/{observation_id}/image')
async def desktop_image(observation_id: str):
    path = await call(desktop.image, observation_id)
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.get('/projects/{project_id}/files')
async def files(project_id: str, conversation_id: str, path: str = '', offset: int = Query(0, ge=0)):
    ctx = await call(context, project_id, conversation_id)
    return await call(code.list_files, ctx, path, 100, offset)


@router.get('/projects/{project_id}/file')
async def file(project_id: str, conversation_id: str, path: str, offset: int = Query(0, ge=0)):
    ctx = await call(context, project_id, conversation_id)
    return await call(code.read_file, ctx, path, offset, 300)


@router.get('/projects/{project_id}/history')
async def execution_history(project_id: str, conversation_id: str):
    await call(context, project_id, conversation_id)
    code.initialize(); terminal.initialize(); desktop.initialize()
    with repository.read() as conn:
        from ..execution_review_service import change_summary
        changes = [change_summary(row) for row in conn.changes((project_id, conversation_id))]
        jobs = [dict(row) for row in conn.jobs((project_id, conversation_id))]
        images = [dict(row) for row in conn.observations((project_id, conversation_id))]
    return {'changes': changes, 'jobs': jobs, 'observations': images}


@router.get('/projects/{project_id}/changes/{change_id}')
async def change(project_id: str, change_id: str, conversation_id: str):
    ctx = await call(context, project_id, conversation_id)
    return await call(code.diff, ctx, change_id)


@router.get('/projects/{project_id}/jobs/{job_id}')
async def job(project_id: str, job_id: str, conversation_id: str, task_id: str, offset: int = Query(0, ge=0)):
    ctx = await call(context, project_id, conversation_id, task_id)
    return await call(terminal.read, ctx, job_id, offset, 12000, deliver=False)


@router.post('/projects/{project_id}/jobs/{job_id}/stop')
async def stop_job(project_id: str, job_id: str, conversation_id: str, task_id: str):
    ctx = await call(context, project_id, conversation_id, task_id)
    return await call(terminal.stop, ctx, job_id)
