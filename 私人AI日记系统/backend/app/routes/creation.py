from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .. import creation_service
from ..creation_models import CreationAssetRequest, CreationJobRequest, CreationPresetRequest
from .. import creation_custom
from ..creation_custom import EnvironmentConfig, WorkflowDefaults, WorkflowImport


router = APIRouter(prefix="/api/creation", tags=["creation"])


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/configuration")
async def creation_configuration():
    return {"environment": creation_custom.environment_config(), "defaults": creation_custom.default_ids()}


@router.put("/configuration/environment")
async def creation_save_environment(payload: EnvironmentConfig):
    try:
        from .. import db
        with db.get_conn() as conn:
            pending = conn.execute("SELECT 1 FROM creation_jobs WHERE status IN ('created','needs_confirmation','submitted','queued','running','cancel_requested') LIMIT 1").fetchone()
        if pending:
            raise ValueError("请在当前创作任务结束后修改 ComfyUI 连接。")
        return {"environment": creation_custom.save_environment(payload)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.put("/configuration/workflows")
async def creation_save_workflow_defaults(payload: WorkflowDefaults):
    try:
        return {"defaults": creation_custom.save_defaults(payload)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/comfyui/discover")
async def creation_discover():
    return await asyncio.to_thread(creation_custom.discover_comfyui)


@router.get("/workflows/local")
async def local_workflows(query: str = Query('', max_length=200)):
    from ..local_discovery import discover_workflows
    return await asyncio.to_thread(discover_workflows, query)


@router.get("/workflows/local/read")
async def local_workflow_read(path: str = Query(..., max_length=2000)):
    from ..local_discovery import read_workflow
    try:
        return await asyncio.to_thread(read_workflow, path)
    except (ValueError, OSError) as exc:
        raise _bad_request(ValueError(str(exc))) from exc


@router.post("/workflows")
async def creation_import_workflow(payload: WorkflowImport):
    try:
        return {"workflow": creation_custom.import_workflow(payload)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/workflows/preview")
async def creation_preview_workflow(payload: creation_custom.WorkflowPreview):
    try:
        info = None
        if isinstance(payload.prompt.get("nodes"), list):
            import httpx
            async with httpx.AsyncClient(**creation_service._client_kwargs(15)) as client:
                try:
                    info = await creation_service._object_info(client)
                except httpx.HTTPError as exc:
                    raise ValueError("无法连接 ComfyUI，请先在创作环境中启动并连接，再选择工作流。") from exc
        return creation_custom.prepare_import(payload.prompt, info)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.delete("/workflows/{workflow_id}")
async def creation_remove_workflow(workflow_id: str):
    try:
        return creation_custom.remove_workflow(workflow_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/bootstrap")
async def creation_bootstrap():
    return await creation_service.bootstrap()


@router.get("/health")
async def creation_health(include_nodes: bool = False):
    return await creation_service.comfyui_health(include_nodes=include_nodes)


@router.post("/comfyui/start")
async def creation_start_comfyui():
    try:
        return await creation_service.start_comfyui()
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/preflight")
async def creation_preflight(workflow_id: str = ""):
    try:
        return await creation_service.comfyui_preflight(workflow_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/jobs")
async def creation_jobs(limit: int = Query(default=40, ge=1, le=200)):
    return {"jobs": creation_service.list_jobs(limit)}


@router.get("/jobs/{job_id}")
async def creation_job(job_id: str):
    job = creation_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="找不到创作任务。")
    return {"job": job}


@router.post("/jobs")
async def submit_creation_job(payload: CreationJobRequest):
    try:
        job, created = creation_service.create_job(payload)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return {"job": job, "created": created}


@router.post("/jobs/{job_id}/confirm")
async def confirm_creation_job(job_id: str):
    try:
        return {"job": creation_service.confirm_job(job_id)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/jobs/{job_id}/retry")
async def retry_creation_job(job_id: str):
    try:
        return {"job": creation_service.retry_job(job_id)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/jobs/{job_id}/cancel")
async def cancel_creation_job(job_id: str):
    try:
        return {"job": await creation_service.cancel_job(job_id)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


from pydantic import BaseModel, Field


class ReconciliationRequest(BaseModel):
    outcome: str = Field(pattern='^(not_completed|completed_external|still_unknown)$')
    receipt: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=1000)


@router.post('/jobs/{job_id}/reconcile')
async def reconcile_creation_job(job_id: str, payload: ReconciliationRequest):
    try:
        return {'job': creation_service.reconcile_job(job_id, **payload.model_dump())}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/jobs/{job_id}/outputs/{index}")
async def creation_output(job_id: str, index: int):
    try:
        path, mime_type, name = creation_service.output_file(job_id, index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=mime_type, filename=name, headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/workflow")
async def creation_workflow(job_id: str):
    try:
        path, name = creation_service.workflow_source_file(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Keep the download header ASCII-only; the source filename itself may be
    # Chinese and Starlette cannot encode it directly into latin-1 headers.
    return FileResponse(path, media_type="application/json", filename=f"workflow-{job_id}.json", headers={"Cache-Control": "no-store"})


@router.get("/presets")
async def creation_presets(kind: str = ""):
    try:
        return {"presets": creation_service.list_presets(kind)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/presets")
async def create_creation_preset(payload: CreationPresetRequest):
    try:
        return {"preset": creation_service.save_preset(payload)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.put("/presets/{preset_id}")
async def update_creation_preset(preset_id: str, payload: CreationPresetRequest):
    try:
        return {"preset": creation_service.save_preset(payload, preset_id)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.delete("/presets/{preset_id}")
async def remove_creation_preset(preset_id: str):
    if not creation_service.delete_preset(preset_id):
        raise HTTPException(status_code=404, detail="找不到创作预设。")
    return {"ok": True}


@router.get("/assets")
async def creation_assets():
    return {"assets": creation_service.list_assets()}


@router.post("/assets")
async def upload_creation_asset(payload: CreationAssetRequest):
    try:
        return {"asset": creation_service.save_asset(payload.name, payload.mime_type, payload.data_url)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/assets/{asset_id}/content")
async def creation_asset_content(asset_id: str):
    try:
        path, mime_type, name = creation_service.asset_file(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=mime_type, filename=name, headers={"Cache-Control": "private, max-age=300"})
