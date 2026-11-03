"""Model inventory and explicitly previewed local resource changes."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

from .. import model_management_service as service
from .. import anif_setup_service as anif_setup
from .. import local_vision_models as vision_models
from .. import voice_asset_service as voice_assets

router = APIRouter(prefix='/api/model-management')
logger = logging.getLogger(__name__)


class StorageRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2048)
    migrate: bool = False


class StorageCommit(StorageRequest):
    token: str = Field(min_length=64, max_length=64)


class VoiceRemoval(BaseModel):
    delete_files: bool = False
    remove_profile: bool = True


class VoiceRemovalCommit(VoiceRemoval):
    token: str = Field(min_length=64, max_length=64)


class CacheClear(BaseModel):
    token: str = Field(min_length=64, max_length=64)


class VisionModelRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=200)


class VisionModelRemoval(VisionModelRequest):
    token: str = Field(min_length=64, max_length=64)


class VoiceSelection(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64)
    engine: str = Field(pattern='^(mio_v2|genie|aniflive)$')


class AnifSetupRequest(BaseModel):
    voice_id: str = Field(min_length=1, max_length=64)


async def _invoke(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.warning('模型资源操作未完成：%s', getattr(function, '__name__', 'resource_operation'), exc_info=True)
        raise HTTPException(500, '模型操作未完成，请检查文件权限或占用后重试；详细原因见本地诊断。') from exc


@router.get('')
async def inventory(refresh: bool = False):
    return await _invoke(service.catalog, refresh)


@router.get('/anif/setup')
async def anif_setup_status():
    return await _invoke(anif_setup.status)


@router.post('/anif/setup')
async def anif_setup_start(payload: AnifSetupRequest):
    return await _invoke(anif_setup.start, payload.voice_id)


@router.post('/anif/setup/cancel')
async def anif_setup_cancel():
    return await _invoke(anif_setup.cancel)


@router.post('/anif/setup/activate')
async def anif_setup_activate():
    return await _invoke(anif_setup.activate)


@router.get('/anif/setup/audio/{language}')
async def anif_setup_audio(language: str, job_id: str):
    path = await _invoke(anif_setup.audio_path, language, job_id)
    return FileResponse(path, media_type='audio/wav', filename=f'anif-preview-{language}.wav',
                        headers={'Cache-Control': 'no-store'})


@router.post('/anif/setup/uninstall-preview')
async def anif_setup_uninstall_preview():
    return await _invoke(anif_setup.uninstall_preview)


@router.post('/anif/setup/uninstall')
async def anif_setup_uninstall(payload: CacheClear):
    return await _invoke(anif_setup.uninstall, payload.token)


@router.get('/voices')
async def voices():
    return await _invoke(service.voices)


@router.post('/voices/select')
async def select_voice(payload: VoiceSelection):
    return await _invoke(voice_assets.select, payload.profile_id, payload.engine)


@router.get('/vision/models')
async def vision_inventory():
    return await _invoke(vision_models.inventory)


@router.post('/vision/models/download')
async def vision_download(payload: VisionModelRequest):
    return await _invoke(vision_models.start_download, payload.model_id)


@router.post('/vision/models/cancel-download')
async def vision_download_cancel():
    return await _invoke(vision_models.cancel_download)


@router.post('/vision/models/verify')
async def vision_verify(payload: VisionModelRequest):
    return await _invoke(vision_models.verify, payload.model_id)


@router.post('/vision/models/select')
async def vision_select(payload: VisionModelRequest):
    return await _invoke(vision_models.select, payload.model_id)


@router.post('/vision/models/remove-preview')
async def vision_removal_preview(payload: VisionModelRequest):
    return await _invoke(vision_models.removal_preview, payload.model_id)


@router.post('/vision/models/remove')
async def vision_remove(payload: VisionModelRemoval):
    return await _invoke(vision_models.remove, payload.model_id, payload.token)


@router.get('/cache/preview')
async def cache_preview():
    return await _invoke(service.cache_preview)


@router.post('/cache/clear')
async def cache_clear(payload: CacheClear):
    return await _invoke(service.cache_clear, payload.token)


@router.post('/{model_id}/release')
async def release(model_id: str):
    return await _invoke(service.release, model_id)


@router.post('/storage/{group}/preview')
async def storage_preview(group: str, payload: StorageRequest):
    return await _invoke(service.storage_preview, group, payload.path, payload.migrate)


@router.post('/storage/{group}/commit')
async def storage_commit(group: str, payload: StorageCommit):
    return await _invoke(service.storage_commit, group, payload.path, payload.migrate, payload.token)


@router.post('/voices/{profile_id}/removal-preview')
async def voice_removal_preview(profile_id: str, payload: VoiceRemoval):
    return await _invoke(service.voice_removal_preview, profile_id, payload.delete_files, payload.remove_profile)


@router.post('/voices/{profile_id}/remove')
async def voice_remove(profile_id: str, payload: VoiceRemovalCommit):
    return await _invoke(service.voice_remove, profile_id, payload.token, payload.delete_files, payload.remove_profile)
