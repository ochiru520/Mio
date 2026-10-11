from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
import asyncio
from .. import execution_policy_service as policy, maintenance_service

router=APIRouter(prefix='/api/execution',tags=['execution-policy'])


class PolicyInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision: int=Field(ge=0)
    mode: Literal['read_only','assisted','autonomous']='assisted'
    read_scope: Literal['project','folders','computer']='project'
    terminal_mode: Literal['confirm','sandbox','host']='confirm'
    network_enabled: bool=False
    confirm_project_edits: bool=False
    sandbox_image: str=Field(default='python:3.12-alpine',min_length=1,max_length=150)


@router.get('/policy')
async def read():
    return await asyncio.to_thread(policy.snapshot)


@router.put('/policy')
async def save(payload: PolicyInput):
    try:
        with maintenance_service.mutation_scope():
            return await asyncio.to_thread(policy.configure,**payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(409 if '另一个窗口' in str(exc) else 400,str(exc)) from exc
