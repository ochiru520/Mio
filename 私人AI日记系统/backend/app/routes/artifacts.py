import asyncio
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from .. import artifact_service as artifacts

router = APIRouter(prefix='/api/artifacts', tags=['artifacts'])


@router.get('')
async def list_artifacts(task_id: str = '', job_id: str = '', limit: int = Query(50,ge=1,le=100), offset: int = Query(0,ge=0)):
    items = await asyncio.to_thread(artifacts.list_artifacts,task_id=task_id,job_id=job_id,limit=limit+1,offset=offset)
    return {'artifacts':items[:limit], 'has_more':len(items)>limit}


@router.get('/{artifact_id}')
async def get_artifact(artifact_id: str):
    result = await asyncio.to_thread(artifacts.get,artifact_id)
    if result is None:raise HTTPException(404,'找不到成果。')
    return result


@router.get('/{artifact_id}/download')
async def download_artifact(artifact_id: str):
    try:
        path, item = await asyncio.to_thread(artifacts.file,artifact_id)
    except (ValueError,OSError) as exc:
        raise HTTPException(404,str(exc)) from exc
    return FileResponse(path,filename=item['name'],media_type=item['mime_type'],headers={'X-Content-Type-Options':'nosniff'})
