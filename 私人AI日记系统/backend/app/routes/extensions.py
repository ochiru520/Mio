"""Local desktop extension lifecycle API; no endpoint executes arbitrary tools."""
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import Field
from .. import extension_service as service, agent_skill_service as skills
from ..extension_tools import SkillDraft

router=APIRouter(tags=['extensions'])


class VersionRequest(service.Input):
    version: str = ''
    expected_revision: int = Field(ge=1)


class EnabledRequest(VersionRequest):
    enabled: bool


class RevisionRequest(service.Input):
    expected_revision: int = Field(ge=1)


class SkillRollback(RevisionRequest):
    revision: int = Field(ge=1)


async def call(function,*args,**kwargs):
    try:
        return await asyncio.to_thread(function,*args,**kwargs)
    except service.ConflictError as exc:
        raise HTTPException(409,str(exc)) from exc
    except (ValueError,OSError) as exc:
        raise HTTPException(400,str(exc)) from exc


@router.get('/api/extensions')
async def listing():
    return {'extensions':await call(service.listing)}


@router.post('/api/extensions')
async def save(payload: service.SaveRequest):
    return await call(service.save,**payload.model_dump())


@router.post('/api/extensions/skills/drafts')
async def draft(payload: SkillDraft):
    return await call(skills.save_draft,**payload.model_dump())


@router.get('/api/extensions/skills/drafts/{draft_id}')
async def read_draft(draft_id: str):
    return await call(skills.read_draft,draft_id)


@router.post('/api/extensions/skills/drafts/{draft_id}/activate')
async def activate(draft_id: str):
    return await call(skills.activate_draft,draft_id)


@router.post('/api/workspace/skills/{skill_id}/rollback')
async def skill_rollback(skill_id: str,payload: SkillRollback):
    return await call(skills.rollback,skill_id,**payload.model_dump())


@router.get('/api/extensions/{extension_id}')
async def read(extension_id: str,version: str=''):
    return await call(service.read,extension_id,version)


@router.post('/api/extensions/{extension_id}/validate')
async def validate(extension_id: str,payload: VersionRequest):
    try:
        return await service.validate(extension_id,**payload.model_dump())
    except service.ConflictError as exc:
        raise HTTPException(409,str(exc)) from exc
    except (ValueError,OSError) as exc:
        raise HTTPException(400,str(exc)) from exc


@router.post('/api/extensions/{extension_id}/enabled')
async def enabled(extension_id: str,payload: EnabledRequest):
    return await call(service.enabled,extension_id,**payload.model_dump())


@router.post('/api/extensions/{extension_id}/rollback')
async def rollback(extension_id: str,payload: RevisionRequest):
    return await call(service.rollback,extension_id,**payload.model_dump())


@router.get('/api/extensions/{extension_id}/panel')
async def panel(extension_id: str):
    return await call(service.panel,extension_id)
