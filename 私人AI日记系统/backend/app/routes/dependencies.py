"""环境与模型中心：依赖检测、一键安装与进度查询。"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import dependency_installer, local_vision_service

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/dependencies")


class UninstallRequest(BaseModel):
    token: str = Field(min_length=64, max_length=64)


class PackageSourceRequest(BaseModel):
    urls: list[str] = Field(default_factory=list, max_length=8)


@router.get('/{dep_id}/package')
async def dependencies_package(dep_id: str):
    from ..dependency_package_service import package_info
    try:
        return await asyncio.to_thread(package_info, dep_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put('/{dep_id}/package-source')
async def dependencies_package_source(dep_id: str, payload: PackageSourceRequest):
    from ..dependency_package_service import package_info, save_urls
    try:
        await asyncio.to_thread(save_urls, dep_id, payload.urls)
        return await asyncio.to_thread(package_info, dep_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/{dep_id}/uninstall-preview')
async def dependencies_uninstall_preview(dep_id: str):
    from ..dependency_removal import preview
    try:
        return await asyncio.to_thread(preview, dep_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/{dep_id}/uninstall')
async def dependencies_uninstall(dep_id: str, payload: UninstallRequest):
    from ..dependency_removal import uninstall
    try:
        return await asyncio.to_thread(uninstall, dep_id, payload.token)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        logger.warning('组件卸载未完成：%s', dep_id, exc_info=True)
        raise HTTPException(500, str(exc)) from exc


@router.get('/diagnostics')
async def dependencies_diagnostics():
    from ..diagnostic_export import snapshot
    return await asyncio.to_thread(snapshot)


@router.get('/diagnostics/baseline')
async def dependencies_diagnostics_baseline():
    from ..diagnostic_export import behavior_baseline
    return await asyncio.to_thread(behavior_baseline)


@router.get("")
async def dependencies_list():
    try:
        return {"dependencies": await asyncio.to_thread(dependency_installer.list_dependencies)}
    except OSError as exc:
        logger.warning("依赖列表检查失败", exc_info=True)
        raise HTTPException(status_code=500, detail="依赖检查失败，请稍后重试。") from exc
    except Exception as exc:
        logger.warning("依赖列表检查异常", exc_info=True)
        raise HTTPException(status_code=500, detail="依赖检查失败，请稍后重试。") from exc


@router.post("/{dep_id}/install")
async def dependencies_install(dep_id: str):
    try:
        return await asyncio.to_thread(dependency_installer.install_dependency, dep_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.warning("依赖安装启动失败：%s", dep_id, exc_info=True)
        raise HTTPException(status_code=500, detail="安装启动失败，请检查权限后重试。") from exc
    except Exception as exc:
        logger.warning("依赖安装异常：%s", dep_id, exc_info=True)
        raise HTTPException(status_code=500, detail="安装启动失败，请稍后重试。") from exc


@router.get("/{dep_id}/status")
async def dependencies_install_status(dep_id: str):
    try:
        return await asyncio.to_thread(dependency_installer.install_status, dep_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("依赖状态查询异常：%s", dep_id, exc_info=True)
        raise HTTPException(status_code=500, detail="状态查询失败，请稍后重试。") from exc


@router.post("/ollama_vision/activate")
async def dependencies_activate_local_vision():
    try:
        return await asyncio.to_thread(local_vision_service.activate)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{dep_id}/verify")
async def dependencies_verify(dep_id: str):
    try:
        return await asyncio.to_thread(dependency_installer.verify_dependency, dep_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
