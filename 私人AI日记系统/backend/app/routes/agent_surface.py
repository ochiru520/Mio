"""Shared persisted presentation images; no conversation or model credentials."""
import base64
import io
import json
import threading
import uuid
from functools import wraps
from contextlib import closing
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
from PIL import Image
from .. import db

_lock = threading.RLock()

def mutation(fn):
    @wraps(fn)
    async def guarded(*args, **kwargs):
        from ..maintenance_service import mutation_scope
        with mutation_scope(), _lock: return await fn(*args, **kwargs)
    return guarded

router = APIRouter(prefix='/api/agent/surface', tags=['agent-surface'])


def _initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS agent_surface_preferences(id INTEGER PRIMARY KEY CHECK(id=1), preferences_json TEXT NOT NULL DEFAULT '{}')")


def preferences():
    _initialize()
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT preferences_json FROM agent_surface_preferences WHERE id=1').fetchone()
    result = json.loads(row[0]) if row else {}
    return {'hero_shape': 'original', **result, 'hero_url': '/api/agent/surface/images/hero' if result.get('hero_custom') else '',
            'personal_url': '/api/agent/surface/images/personal' if result.get('personal_custom') else ''}


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hero_shape: str = Field(default='original', pattern=r'^(original|circle)$')


class Picture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_url: str = Field(min_length=1, max_length=12_000_000)


def _save(values):
    result = preferences()
    result.pop('hero_url', None); result.pop('personal_url', None)
    result.update(values)
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR REPLACE INTO agent_surface_preferences VALUES(1,?)', (json.dumps(result, ensure_ascii=False),))
    return preferences()


@router.get('')
async def get_preferences():
    return preferences()


@router.put('')
@mutation
async def set_preferences(payload: Preferences):
    return _save(payload.model_dump())


def image_path(kind):
    if kind not in {'hero', 'personal'}: raise HTTPException(400, '未知图片位置。')
    return db.settings.db_path.parent / '界面图片' / ('agent-' + kind + '.png')


@router.post('/images/{kind}')
@mutation
async def upload_image(kind: str, payload: Picture):
    path = image_path(kind)
    try:
        header, encoded = payload.data_url.split(',', 1)
        if header not in {'data:image/png;base64', 'data:image/jpeg;base64', 'data:image/webp;base64'}:
            raise ValueError('请选择 PNG、JPEG 或 WebP 图片。')
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 8 * 1024 * 1024: raise ValueError('图片不能超过 8 MB。')
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 20_000_000: raise ValueError('图片像素过大。')
            image = image.convert('RGBA'); image.thumbnail((1600, 1600))
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp'); image.save(temp, format='PNG'); temp.replace(path)
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _save({kind + '_custom': True})


@router.delete('/images/{kind}')
@mutation
async def reset_image(kind: str):
    # Recoverable reset: retain the prior local image; preference stops using it.
    image_path(kind)
    return _save({kind + '_custom': False})


@router.get('/images/{kind}')
async def image(kind: str):
    path = image_path(kind)
    if not preferences().get(kind + '_custom') or not path.is_file(): raise HTTPException(404, '未设置图片。')
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-cache'})
