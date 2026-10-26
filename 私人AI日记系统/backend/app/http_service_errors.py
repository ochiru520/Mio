"""Transport adapters for stable service failures and diary date inputs."""
from __future__ import annotations

import functools
import inspect
from fastapi import HTTPException

from .diary_dates import normalize_diary_date
from .service_errors import ServiceOperationError


def service_errors_to_http(function):
    """Adapt async HTTP handlers without catching cancellation or unrelated errors."""
    @functools.wraps(function)
    async def adapted(*args, **kwargs):
        try:
            return await function(*args, **kwargs)
        except ServiceOperationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    # FastAPI resolves annotations in this wrapper's globals. Preserve the
    # original evaluated signature so endpoint request bodies retain their type.
    adapted.__signature__ = inspect.signature(function, eval_str=True)
    return adapted


def validated_diary_date(value: str) -> str:
    try:
        return normalize_diary_date(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
