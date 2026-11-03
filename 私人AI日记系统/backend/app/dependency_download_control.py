"""Cooperative control of an identified installer's own download only."""
from __future__ import annotations

import json
import uuid
from typing import Any


def control_download(dep_id: str, action: str) -> dict[str, Any]:
    from . import dependency_installer as installer

    if installer._dependency_def(dep_id) is None:
        raise ValueError("不认识的依赖项目。")
    if action not in {"pause", "resume", "cancel"}:
        raise ValueError("不支持的下载操作。")
    with installer.dependency_operation(dep_id):
        progress = installer._read_status(dep_id)
        running = installer._install_running(dep_id)
        if action == "resume" and not running and progress.get("stage") in {"paused", "cancelled", "error"}:
            # New installer identity makes stale commands harmless; pinned partials are reused.
            return installer._install_dependency(dep_id)
        capability = {"pause": "can_pause", "resume": "can_resume", "cancel": "can_cancel"}[action]
        if not running or not progress.get("install_id") or not progress.get(capability):
            raise ValueError("当前阶段不能执行此操作；仅文件下载阶段支持暂停和取消，请等待状态更新。")
        path = installer._status_path(dep_id).with_suffix(".control.json")
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps({"install_id": progress["install_id"], "action": action}), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return {"id": dep_id, "action": action, "accepted": True,
                "message": "请求已发送；请以下载进度中的实际状态为准。"}
