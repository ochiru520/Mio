import asyncio
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal
from .. import artifact_service as artifacts
from ..artifact_preview_service import OfficePreviewError

router = APIRouter(prefix='/api/artifacts', tags=['artifacts'])


@router.get('')
async def list_artifacts(task_id: str = '', job_id: str = '', project_id: str = '', limit: int = Query(50,ge=1,le=100), offset: int = Query(0,ge=0),
                         q: str = Query('',max_length=200), kind: str = Query('',pattern=r'^$|^(document|image|video|audio)$'), favorite: bool = False, conversation_id: str = ''):
    try:
        items = await asyncio.to_thread(artifacts.list_artifacts,task_id=task_id,job_id=job_id,project_id=project_id,limit=limit+1,offset=offset,
                                        q=q,kind=kind,favorite=favorite,conversation_id=conversation_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'artifacts':items[:limit], 'has_more':len(items)>limit, 'next_offset':offset+len(items[:limit])}


class QualityReview(BaseModel):
    status: Literal['not_reviewed', 'user_approved', 'user_rejected']
    note: str = Field(default='', max_length=2000)


class Favorite(BaseModel):
    favorite: bool


@router.post('/{artifact_id}/favorite')
async def favorite_artifact(artifact_id: str, payload: Favorite):
    try: return await asyncio.to_thread(artifacts.set_favorite, artifact_id, payload.favorite)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc


@router.get('/{artifact_id}/preview')
async def preview_artifact(artifact_id: str):
    try:
        return await asyncio.to_thread(artifacts.preview, artifact_id)
    except OfficePreviewError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/{artifact_id}/review')
async def review_artifact(artifact_id: str, payload: QualityReview):
    try:
        return await asyncio.to_thread(artifacts.review, artifact_id, payload.status, payload.note)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/{artifact_id}/verify')
async def verify_artifact(artifact_id: str):
    try:
        return await asyncio.to_thread(artifacts.verify, artifact_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


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


@router.get('/{artifact_id}/content')
async def artifact_content(artifact_id: str):
    try: path, item = await asyncio.to_thread(artifacts.file, artifact_id)
    except (ValueError, OSError) as exc: raise HTTPException(404, str(exc)) from exc
    mime = artifacts.content_mime(item)
    # SVG/HTML are executable content; never serve them as same-origin active pages.
    safe = mime == 'application/pdf' or mime.startswith(('video/', 'audio/')) or mime in {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp'}
    return FileResponse(path, filename=item['name'], media_type=mime if safe else 'application/octet-stream',
        content_disposition_type='inline' if safe else 'attachment',
        headers={'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'", 'Cache-Control': 'no-store'})
