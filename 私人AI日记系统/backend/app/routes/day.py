from __future__ import annotations

from fastapi import APIRouter

from .. import day_end_service
from ..http_service_errors import service_errors_to_http

router = APIRouter()


@router.post("/api/day/end-today")
@service_errors_to_http
async def end_today():
    return await day_end_service.end_today()
