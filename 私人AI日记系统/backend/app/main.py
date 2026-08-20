from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import (
    backup_service,
    autonomy_service,
    companion_service,
    cost_reconciliation_service,
    daily_diary_service,
    daily_review_service,
    db,
    maintenance_service,
    migration_service,
    monthly_review_service,
    onboarding_service,
    pet_event_service,
    proactive_service,
    runtime_diagnostics,
    screen_observation_service,
    subservice_health,
    weekly_review_service,
)
from .runtime_identity import runtime_identity
from .config import settings
from .companion_action_service import backfill_explicit_structured_memories
from .documents import load_manuals
from .local_security import LocalControlMiddleware, SecureAttachmentFiles
from .routes import (
    agent,
    autonomy,
    backup,
    chat,
    companion,
    day,
    dependencies,
    diary,
    memory,
    monthly,
    onboarding,
    onebot,
    privacy,
    realtime,
    review,
    settings as settings_routes,
    stats,
    weekly,
)


logger = logging.getLogger(__name__)


BACKGROUND_TASK_FACTORIES = {
    "runtime_diagnostics_task": runtime_diagnostics.monitor_loop,
    "voice_startup_task": companion_service.start_voice_on_app_startup,
    "qq_startup_task": proactive_service.start_qq_on_app_startup,
    "qq_proactive_task": autonomy_service.autonomy_loop,
    "daily_diary_task": daily_diary_service.daily_diary_loop,
    "daily_review_task": daily_review_service.daily_review_loop,
    "backup_task": backup_service.backup_loop,
    "weekly_review_task": weekly_review_service.weekly_review_loop,
    "monthly_review_task": monthly_review_service.monthly_review_loop,
    "screen_observation_task": screen_observation_service.observation_loop,
    "pet_event_task": pet_event_service.event_loop,
    "cost_reconciliation_task": cost_reconciliation_service.reconciliation_loop,
}


def initialize_runtime() -> None:
    """Initialize persistent runtime state only when the application starts."""
    settings.ensure_directories()
    companion_service.cleanup_legacy_preview()
    db.init_db()
    migration_service.run_migrations()
    onboarding_service.prepare_first_launch_defaults()
    db.cleanup_screen_observation_history(
        retention_days=settings.screen_history_retention_days,
        max_rows_per_table=settings.screen_history_max_rows,
    )
    db.refresh_manual_memories(load_manuals())
    backfill_explicit_structured_memories()


async def _start_background_tasks(app: FastAPI, *, exclude: set[str] | None = None) -> None:
    excluded = exclude or set()
    for task_name, task_factory in BACKGROUND_TASK_FACTORIES.items():
        if task_name in excluded:
            continue
        current = getattr(app.state, task_name, None)
        if current is not None and not current.done():
            continue
        task = asyncio.create_task(task_factory(), name=task_name)
        setattr(app.state, task_name, task)
        runtime_diagnostics.register_background_task(task_name, task)


async def _stop_background_tasks(app: FastAPI, *, exclude: set[str] | None = None) -> None:
    excluded = exclude or set()
    tasks: list[tuple[str, asyncio.Task]] = []
    for task_name in BACKGROUND_TASK_FACTORIES:
        if task_name in excluded:
            continue
        task = getattr(app.state, task_name, None)
        if task is None:
            continue
        if not task.done():
            task.cancel()
        tasks.append((task_name, task))
    if tasks:
        results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for (task_name, _), result in zip(tasks, results):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                logger.error("后台任务关闭时异常：%s: %s", task_name, result)
    for task_name, _ in tasks:
        runtime_diagnostics.unregister_background_task(task_name)
        setattr(app.state, task_name, None)


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    maintenance_service.reset_runtime_state()
    companion_service.reset_frontend_ready()
    initialize_runtime()
    await _start_background_tasks(app)
    maintenance_lock = asyncio.Lock()

    async def enter_maintenance(reason: str) -> dict[str, object]:
        await maintenance_lock.acquire()
        maintenance_started = False
        try:
            maintenance_service.begin(reason)
            maintenance_started = True
            await _stop_background_tasks(app, exclude={"runtime_diagnostics_task"})
            await onebot.disconnect_all_connections(reason="Mio 正在执行数据维护")
            await asyncio.to_thread(maintenance_service.wait_for_quiescence, 30)
            return maintenance_service.status()
        except Exception:
            if maintenance_started:
                maintenance_service.finish("maintenance_failed", keep_blocked=False)
                await _start_background_tasks(app, exclude={"runtime_diagnostics_task"})
            if maintenance_lock.locked():
                maintenance_lock.release()
            raise

    async def finish_maintenance(status: str, *, resume: bool) -> dict[str, object]:
        try:
            maintenance_service.finish(status, keep_blocked=not resume)
            if resume:
                await _start_background_tasks(app, exclude={"runtime_diagnostics_task"})
            return maintenance_service.status()
        finally:
            if maintenance_lock.locked():
                maintenance_lock.release()

    app.state.enter_maintenance = enter_maintenance
    app.state.finish_maintenance = finish_maintenance
    try:
        yield
    finally:
        await _stop_background_tasks(app)
        try:
            await onebot.disconnect_all_connections(reason="Mio 后端正在关闭")
        except Exception:
            logger.exception("关闭后端时断开 QQ 连接失败")
        try:
            companion_service.shutdown()
        except Exception:
            logger.exception("关闭后端时清理桌宠资源失败")
        maintenance_service.reset_runtime_state()


def create_app() -> FastAPI:
    app = FastAPI(title="私人 AI 日记系统", lifespan=app_lifespan)
    app.add_middleware(LocalControlMiddleware)

    @app.middleware("http")
    async def enforce_maintenance_mode(request, call_next):
        method = request.method.upper()
        path_parts = [part for part in request.url.path.rstrip("/").split("/") if part]
        is_restore_request = (
            method == "POST"
            and len(path_parts) == 4
            and path_parts[:2] == ["api", "backups"]
            and path_parts[-1] == "restore"
        )
        if method not in {"POST", "PUT", "PATCH", "DELETE"} or is_restore_request:
            return await call_next(request)
        try:
            with maintenance_service.mutation_scope():
                return await call_next(request)
        except maintenance_service.MaintenanceModeError as exc:
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "5"},
                content={
                    "detail": str(exc),
                    "maintenance": maintenance_service.status(),
                },
            )

    @app.middleware("http")
    async def collect_runtime_diagnostics(request, call_next):
        request_id = runtime_diagnostics.request_started(request.method, request.url.path)
        try:
            response = await call_next(request)
        except BaseException as exc:
            runtime_diagnostics.request_finished(
                request_id,
                status_code=None,
                error=type(exc).__name__,
            )
            raise
        runtime_diagnostics.request_finished(request_id, status_code=response.status_code)
        return response

    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
    app.mount("/local-site", StaticFiles(directory=str(settings.site_custom_dir), check_dir=False), name="local-site")
    app.mount("/photos", StaticFiles(directory=str(settings.photo_dir), check_dir=False), name="photos")
    app.mount(
        "/agent-files",
        SecureAttachmentFiles(directory=str(settings.agent_attachment_dir), check_dir=False),
        name="agent-files",
    )
    app.include_router(chat.router)
    app.include_router(agent.router)
    app.include_router(autonomy.router)
    app.include_router(backup.router)
    app.include_router(companion.router)
    app.include_router(dependencies.router)
    app.include_router(diary.router)
    app.include_router(day.router)
    app.include_router(memory.router)
    app.include_router(monthly.router)
    app.include_router(onboarding.router)
    app.include_router(onebot.router)
    app.include_router(privacy.router)
    app.include_router(realtime.router)
    app.include_router(review.router)
    app.include_router(settings_routes.router)
    app.include_router(stats.router)
    app.include_router(weekly.router)

    if settings.agent_frontend_dir.exists():
        agent_frontend_root = settings.agent_frontend_dir.resolve()

        @app.get("/agent-app", include_in_schema=False)
        async def agent_app_redirect():
            return RedirectResponse(url="/agent-app/")

        @app.get("/agent-app/{asset_path:path}", include_in_schema=False)
        async def agent_app(asset_path: str):
            requested = (agent_frontend_root / asset_path).resolve()
            if requested.is_relative_to(agent_frontend_root) and requested.is_file():
                relative_parts = requested.relative_to(agent_frontend_root).parts
                cache_control = (
                    "no-store"
                    if requested.name == "index.html" or "live2d-pet" in relative_parts
                    else "public, max-age=31536000, immutable"
                )
                return FileResponse(requested, headers={"Cache-Control": cache_control})
            return FileResponse(
                agent_frontend_root / "index.html",
                headers={"Cache-Control": "no-store"},
            )

    @app.get("/")
    async def root():
        return RedirectResponse(url="/agent-app/")

    @app.get("/health")
    async def health():
        identity = runtime_identity()
        return {
            "ok": True,
            "project_root": str(settings.project_root),
            "db_path": str(settings.db_path),
            "diary_dir": str(settings.diary_dir),
            "exe_path": identity["exe_path"],
            "build_id": identity["build_id"],
            "runtime_root": identity["runtime_root"],
            "state_root": identity["state_root"],
            "database_path": identity["database_path"],
            "runtime_identity": identity,
            "runtime_diagnostics": runtime_diagnostics.snapshot(),
            "subservices": subservice_health.snapshot(),
            "maintenance": maintenance_service.status(),
        }

    return app


app = create_app()
