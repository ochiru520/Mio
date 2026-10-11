"""Local settings endpoints; protected by the application's existing API guard."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from .. import wechat_service as service

router = APIRouter(prefix='/api/wechat', tags=['wechat'])


class PollRequest(BaseModel):
    login_id: str = Field(min_length=32, max_length=32)
    verify_code: str = Field(default='', max_length=12)


class EnableRequest(BaseModel):
    enabled: bool


async def guarded(callback, *args):
    try:
        return await callback(*args)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(400, detail=str(exc)) from None
    except Exception:
        raise HTTPException(502, detail='微信服务暂时未响应，请稍后重试。') from None


@router.get('/status')
async def status():
    return service.public_status()


@router.post('/login')
async def login():
    return await guarded(service.start_login)


@router.post('/login/status')
async def login_status(payload: PollRequest):
    return await guarded(service.login_status, payload.login_id, payload.verify_code)


@router.patch('/connection')
async def connection(payload: EnableRequest):
    return await guarded(service.set_enabled, payload.enabled)


@router.delete('/connection')
async def forget():
    return await guarded(service.forget)


@router.get('/attachments/{attachment_id}')
def attachment(attachment_id: str):
    from contextlib import closing
    from pathlib import Path
    import hashlib
    import re
    from .. import db, mobile_file_service
    if not re.fullmatch(r'phonefile_[a-f0-9]{32}', attachment_id):
        raise HTTPException(404, '没有找到微信附件。')
    with closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM mobile_task_attachments WHERE id=? AND self_id LIKE 'wechat:%'", (attachment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '没有找到微信附件。')
    path = Path(row['path']).resolve()
    if not path.is_relative_to(mobile_file_service.root().resolve()) or not path.is_file():
        raise HTTPException(404, '微信附件已不可用。')
    if path.stat().st_size != row['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
        raise HTTPException(409, '微信附件内容已改变，无法下载。')
    return FileResponse(path, filename=row['name'])
