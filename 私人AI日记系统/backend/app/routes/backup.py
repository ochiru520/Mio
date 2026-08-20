from __future__ import annotations

import asyncio
import zipfile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .. import backup_service, maintenance_service, migration_service


router = APIRouter(prefix="/api")


@router.get("/backups")
async def backups():
    return {"backups": await asyncio.to_thread(backup_service.list_backups)}


@router.post("/backups")
async def create_backup():
    try:
        path = await asyncio.to_thread(backup_service.create_complete_backup)
        info = await asyncio.to_thread(backup_service.inspect_backup, path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"完整备份创建失败：{exc}") from exc
    return {"backup": info}


@router.post("/backups/import")
async def import_backup(
    request: Request,
    filename: str = Query(default="导入备份.zip", min_length=1, max_length=200),
):
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请选择 Mio 完整备份 ZIP。")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="备份请求大小无效。") from exc
        if declared_size <= 0 or declared_size > backup_service.MAX_IMPORT_BYTES:
            raise HTTPException(status_code=413, detail="完整备份必须大于 0 字节且不超过 2 GB。")
    staging = await asyncio.to_thread(backup_service.create_import_staging_path)
    output = None
    try:
        received = 0
        output = await asyncio.to_thread(staging.open, "wb")
        try:
            async for chunk in request.stream():
                received += len(chunk)
                if received > backup_service.MAX_IMPORT_BYTES:
                    raise HTTPException(status_code=413, detail="完整备份必须大于 0 字节且不超过 2 GB。")
                await asyncio.to_thread(output.write, chunk)
        finally:
            await asyncio.to_thread(output.close)
            output = None
        if received <= 0:
            raise HTTPException(status_code=400, detail="完整备份不能为空。")
        info = await asyncio.to_thread(backup_service.import_backup_file, filename, staging)
    except HTTPException:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=f"备份导入失败：{exc}") from exc
    finally:
        if output is not None:
            await asyncio.to_thread(output.close)
        await asyncio.to_thread(staging.unlink, missing_ok=True)
    return {"backup": info}


@router.get("/backups/{name}/download")
async def download_backup(name: str):
    try:
        path = backup_service.backup_path(name)
    except (OSError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="没有找到这个备份。") from exc
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.post("/backups/{name}/restore")
async def restore_backup(name: str, request: Request):
    maintenance_entered = False
    try:
        path = await asyncio.to_thread(backup_service.backup_path, name)
        await asyncio.to_thread(backup_service.inspect_backup, path)
        await request.app.state.enter_maintenance(f"恢复完整备份：{name}")
        maintenance_entered = True
        result = await asyncio.to_thread(backup_service.restore_backup, name)
    except FileNotFoundError as exc:
        if maintenance_entered:
            await request.app.state.finish_maintenance("rollback_complete", resume=True)
        raise HTTPException(status_code=404, detail="没有找到这个备份。") from exc
    except maintenance_service.MaintenanceModeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": "5"},
        ) from exc
    except backup_service.RestoreStateUncertainError as exc:
        if maintenance_entered:
            await request.app.state.finish_maintenance("state_uncertain", resume=False)
        raise HTTPException(
            status_code=500,
            detail=f"备份恢复后数据状态不确定，已保持只读，请重启前先检查日志：{exc}",
        ) from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        if maintenance_entered:
            await request.app.state.finish_maintenance("rollback_complete", resume=True)
        raise HTTPException(status_code=400, detail=f"备份恢复失败：{exc}") from exc
    maintenance = await request.app.state.finish_maintenance("restart_required", resume=False)
    return {**result, "maintenance": maintenance}


@router.get("/migrations/status")
async def migrations_status():
    return await asyncio.to_thread(migration_service.migration_status)
