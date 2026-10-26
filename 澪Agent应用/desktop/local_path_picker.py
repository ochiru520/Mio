"""Read only the local path or workflow chosen in the native dialog.

This boundary never persists configuration or creates a project directory.
"""
from __future__ import annotations


import json
from pathlib import Path


def select_agent_path(window, kind: str = "directory") -> dict[str, object]:
    """Only return a path/content the user selected in the native dialog."""
    if window is None:
        return {"ok": False, "error": "窗口尚未就绪。"}
    if kind not in {"directory", "comfyui", "workflow"}:
        return {"ok": False, "error": "不支持的选择类型。"}
    try:
        import webview
        options = {"directory": str(Path.home()), "allow_multiple": False}
        if kind == "workflow":
            options["file_types"] = ("ComfyUI JSON (*.json)",)
        selected = window.create_file_dialog(
            webview.OPEN_DIALOG if kind == "workflow" else webview.FOLDER_DIALOG, **options)
        if not selected:
            return {"ok": False, "canceled": True}
        path = Path(selected[0] if isinstance(selected, (tuple, list)) else selected).resolve(strict=True)
        if kind == "workflow":
            if not path.is_file() or path.suffix.lower() != ".json":
                raise ValueError("请选择工作流 JSON 文件。")
            with path.open("rb") as handle:
                content = handle.read(2_000_001)
            if len(content) > 2_000_000:
                raise ValueError("工作流不能超过 2 MB。")
            return {"ok": True, "path": str(path), "name": path.name, "prompt": json.loads(content.decode("utf-8-sig"))}
        if not path.is_dir():
            raise ValueError("请选择文件夹。")
        if kind == "comfyui":
            from app.creation_custom import normalize_root
            path = normalize_root(path)
        return {"ok": True, "path": str(path)}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
