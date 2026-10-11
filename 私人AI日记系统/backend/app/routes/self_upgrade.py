"""User controls for self-development; model tools cannot change authorization."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from .. import self_upgrade_service as service

router = APIRouter(prefix='/api/self-upgrade', tags=['self-upgrade'])


class Configuration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    backend_root: str = Field(min_length=1, max_length=2000)
    desktop_root: str = Field(min_length=1, max_length=2000)
    development_root: str = Field(default='', max_length=2000)
    python_executable: str = Field(default='', max_length=2000)
    expected_revision: int = Field(ge=0)
    allow_auto_apply: bool = False


class Create(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal: str = Field(min_length=1, max_length=3000)
    acceptance: list[str] = Field(min_length=1, max_length=30)
    expected_revision: int = Field(ge=1)
    conversation_id: str = Field(default='', max_length=200)


class Apply(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirmed: bool = False


class Validate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    backend_tests: list[str] = Field(default_factory=lambda: ['tests/test_self_upgrade.py'], max_length=30)


def invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/status')
def status():
    return invoke(service.status)


@router.put('/configuration')
def configure(body: Configuration):
    return invoke(service.configure, **body.model_dump())


@router.post('/jobs')
def create(body: Create):
    return invoke(service.create, **body.model_dump())


@router.get('/jobs/{job_id}')
def get(job_id: str):
    return invoke(service.get, job_id)


@router.post('/jobs/{job_id}/validate')
def validate(job_id: str, body: Validate | None = None):
    return invoke(service.validate, job_id, backend_tests=body.backend_tests if body else None)


@router.post('/jobs/{job_id}/build')
def build(job_id: str):
    return invoke(service.build, job_id)


@router.post('/jobs/{job_id}/preview')
def preview(job_id: str):
    return invoke(service.preview, job_id)


@router.post('/jobs/{job_id}/stop')
def stop(job_id: str):
    return invoke(service.stop, job_id)


@router.post('/jobs/{job_id}/apply')
def apply(job_id: str, body: Apply):
    return invoke(service.apply, job_id, confirmed=body.confirmed)


@router.post('/rollback')
def rollback(body: Apply):
    return invoke(service.rollback, confirmed=body.confirmed)
