"""Describe process and renderer readiness separately; no process or I/O ownership."""
from typing import Any


STARTUP_TIMEOUT_SECONDS = 15.0


def startup_status(*, alive: bool, runtime_kind: str, renderers: list[dict[str, Any]],
                   launch_id: str, show_request_id: str, elapsed: float,
                   last_error: str = "") -> dict[str, Any]:
    current = [item for item in renderers if item.get("runtime") in {"electron", "pywebview"}
               and (not launch_id or item.get("launch_id") == launch_id)]
    ready = any(item.get("model_ready") is True and item.get("visible") is True
                and (not show_request_id or item.get("show_request_id") == show_request_id)
                for item in current)
    if not alive and not current:
        phase, message = ("failed", last_error) if last_error else ("stopped", "桌宠未启动")
    elif last_error:
        phase, message = "failed", last_error
    elif ready:
        phase, message = "ready", "桌宠模型已加载，窗口已显示"
    elif any(item.get("startup_error") for item in current):
        phase, message = "failed", next(item["startup_error"] for item in current if item.get("startup_error"))
    elif runtime_kind not in {"electron_live2d", "external_renderer"}:
        phase, message = "unverified", "备用桌宠进程已启动，尚未确认窗口显示"
    elif elapsed >= STARTUP_TIMEOUT_SECONDS:
        phase, message = "failed", "桌宠启动超时：进程存在，但模型或窗口尚未就绪。请查看桌宠日志，或开启显卡兼容模式后重启。"
    else:
        phase, message = "starting", "正在等待桌宠模型加载和窗口显示…"
    return {"state": phase, "message": message, "ready": phase == "ready"}
