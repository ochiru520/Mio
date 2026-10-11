from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .. import capability_checks as checks

router = APIRouter(prefix='/api/capabilities', tags=['capabilities'])


class StartRequest(BaseModel):
    consent: bool = False


class CancelRequest(BaseModel):
    run_id: str = Field(min_length=32, max_length=32)


@router.get('')
async def list_capabilities():
    return checks.snapshot()


@router.post('/{capability}/check')
async def start_check(capability: str, request: StartRequest):
    try:
        return await checks.start(capability, consent=request.consent)
    except KeyError:
        raise HTTPException(404, '未知能力。') from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/{capability}/cancel')
async def cancel_check(capability: str, request: CancelRequest):
    try:
        return await checks.cancel(capability, request.run_id)
    except KeyError:
        raise HTTPException(404, '未知能力。') from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
