"""Validate and prepare ComfyUI imports for HTTP and local-file tools."""
from __future__ import annotations

import httpx

from . import creation_custom, creation_service


async def preview_workflow(prompt: dict) -> dict:
    info = None
    if isinstance(prompt.get("nodes"), list):
        async with httpx.AsyncClient(**creation_service._client_kwargs(15)) as client:
            try:
                info = await creation_service._object_info(client)
            except httpx.HTTPError as exc:
                raise ValueError("无法连接 ComfyUI，请先在创作环境中启动并连接，再选择工作流。") from exc
    return creation_custom.prepare_import(prompt, info)
