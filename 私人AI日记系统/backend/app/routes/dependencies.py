"""环境与模型中心：依赖检测、一键安装与进度查询。"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from .. import dependency_installer

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/dependencies")


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
