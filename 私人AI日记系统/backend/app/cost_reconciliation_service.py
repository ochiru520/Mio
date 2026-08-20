from __future__ import annotations

import asyncio
import logging
import time

from . import db
from .llm import (
    CompletionRoute,
    ProviderCostReference,
    _completion_routes,
    _provider_log_cost_yuan,
)
from .model_registry import get_model_profile


logger = logging.getLogger(__name__)
_origin_next_lookup_at: dict[str, float] = {}
_lookup_lock = asyncio.Lock()


def queue_cost_reconciliation(
    local_request_id: str,
    conversation_id: str,
    references: tuple[ProviderCostReference, ...],
) -> None:
    if not local_request_id:
        return
    for reference in references:
        if not reference.provider_request_id:
            continue
        db.enqueue_cost_reconciliation(
            local_request_id=local_request_id,
            conversation_id=conversation_id,
            provider_request_id=reference.provider_request_id,
            profile_id=reference.profile_id,
            base_url=reference.base_url,
            estimated_cost_yuan=reference.estimated_cost_yuan,
            estimated_cost_source=reference.estimated_cost_source,
        )


def _matching_route(profile_id: str, base_url: str) -> tuple[object, CompletionRoute]:
    profile = get_model_profile(profile_id)
    routes = _completion_routes(profile)
    route = next((item for item in routes if item.base_url == base_url), None)
    if route is None:
        route = CompletionRoute(base_url=base_url, proxy_url="", label="后台补账")
    return profile, route


async def reconcile_one_due_job() -> bool:
    rows = db.list_due_cost_reconciliation_jobs(limit=1)
    if not rows:
        return False
    row = rows[0]
    job_id = int(row["id"])
    attempts = int(row["attempts"] or 0)
    try:
        profile, route = _matching_route(str(row["profile_id"]), str(row["base_url"]))
    except (ValueError, RuntimeError) as exc:
        db.retry_cost_reconciliation_job(
            job_id,
            delay_seconds=300,
            last_error=str(exc),
            max_attempts=2,
        )
        return True

    async with _lookup_lock:
        origin = str(row["base_url"])
        wait_seconds = _origin_next_lookup_at.get(origin, 0.0) - time.monotonic()
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        amount = await _provider_log_cost_yuan(
            profile,
            route,
            str(row["provider_request_id"]),
        )
        _origin_next_lookup_at[origin] = time.monotonic() + 2.0

    if amount is not None:
        db.resolve_cost_reconciliation_job(job_id, amount)
        return True

    delays = (3, 8, 20, 60, 180, 600)
    db.retry_cost_reconciliation_job(
        job_id,
        delay_seconds=delays[min(attempts, len(delays) - 1)],
        last_error="供应商账单尚未生成或查询受限",
        max_attempts=len(delays),
    )
    return True


async def reconciliation_loop() -> None:
    await asyncio.sleep(5)
    while True:
        try:
            worked = await reconcile_one_due_job()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("模型费用后台补账失败")
            worked = False
        await asyncio.sleep(1 if worked else 5)
