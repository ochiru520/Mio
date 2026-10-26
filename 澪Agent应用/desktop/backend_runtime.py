"""Embedded backend lifecycle, reserved listeners and health recovery.

No launcher import or startup side effects: identity and desktop state stay with
its caller. Callbacks read current launcher dependencies instead of copying its
mutable port, socket, health error or monkeypatched functions.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import socket
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class HealthDependencies:
    health_matches_runtime: Callable[[Path], bool]
    health_error: Callable[[], str]
    port_is_open: Callable[[], bool]
    port: Callable[[], int]
    record_recovery_state: Callable[..., None]


@dataclass(frozen=True)
class ServerDependencies:
    host: str
    port: int
    health: Callable[[], dict[str, object] | None]
    same_path: Callable[[object, Path], bool]
    port_is_open: Callable[[], bool]
    get_listener: Callable[[], socket.socket | None]
    set_listener: Callable[[socket.socket | None], None]
    bind_listener: Callable[..., socket.socket]
    log_path: Path


class BackendStartupError(RuntimeError):
    """Keep a failed start's resources reachable until bounded shutdown finishes."""
    def __init__(self, message, server, thread):
        super().__init__(message)
        self.server = server
        self.thread = thread


def close_backend_listener(dependencies: ServerDependencies) -> None:
    listener = dependencies.get_listener()
    if listener is not None:
        listener.close()
        dependencies.set_listener(None)


def start_backend(runtime_root: Path, dependencies: ServerDependencies):
    existing = dependencies.health()
    if existing is not None:
        if not dependencies.same_path(existing.get("project_root"), runtime_root):
            raise RuntimeError(f"{dependencies.port}端口上正在运行另一份 Mio 后端。请先关闭旧后端，再启动桌面应用。")
        return None, None
    if dependencies.port_is_open():
        raise RuntimeError(f"{dependencies.port}端口已被其他程序占用，Mio 无法启动。")

    if dependencies.get_listener() is None:
        dependencies.set_listener(dependencies.bind_listener(dependencies.port, allow_fallback=False))

    import uvicorn
    from app.main import app

    config = uvicorn.Config(app, host=dependencies.host, port=dependencies.port,
                            log_config=None, access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run,
                              kwargs={"sockets": [dependencies.get_listener()]},
                              name="mio-backend", daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            health = dependencies.health()
            if health is not None and dependencies.same_path(health.get("project_root"), runtime_root):
                return server, thread
            if not thread.is_alive():
                break
            time.sleep(0.25)
    except Exception:
        server.should_exit = True
        # Runtime.start retains ownership even if Thread.start or a health probe raises.
        raise BackendStartupError(f"Mio 的后端没有正常启动。日志位置：{dependencies.log_path}", server, thread)
    server.should_exit = True
    raise BackendStartupError(f"Mio 的后端没有正常启动。日志位置：{dependencies.log_path}", server, thread)


class BackendRuntime:
    def __init__(self, runtime_root: Path, *, start_backend: Callable, port_is_open: Callable,
                 close_listener: Callable):
        self.runtime_root = runtime_root
        self.server = None
        self.thread: threading.Thread | None = None
        self._start_backend = start_backend
        self._port_is_open = port_is_open
        self._close_listener = close_listener
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            try:
                self.server, self.thread = self._start_backend(self.runtime_root)
            except BackendStartupError as exc:
                self.server, self.thread = exc.server, exc.thread
                raise

    def current_thread(self) -> threading.Thread | None:
        with self._lock:
            return self.thread

    def stop(self, *, timeout: float = 8) -> bool:
        with self._lock:
            server, thread = self.server, self.thread
            if server is not None:
                server.should_exit = True
            # Thread.start can fail before an OS thread is created.
            if thread is not None and thread.ident is not None:
                thread.join(timeout=max(0.0, timeout))
            stopped = thread is None or not thread.is_alive()
            if stopped:
                self.server = self.thread = None
                self._close_listener()
            return stopped

    def recover(self, reason: str) -> bool:
        logging.error("Attempting independent backend recovery: %s", reason)
        if not self.stop(timeout=8):
            logging.error("Backend thread did not stop; recovery aborted")
            return False
        try:
            self.start()
        except Exception:
            logging.exception("Failed to restart embedded backend")
            return False
        return self.thread is not None and self.thread.is_alive() and self._port_is_open()


def local_backend_diagnostics() -> dict[str, object]:
    try:
        from app.runtime_diagnostics import snapshot

        result = snapshot()
        try:
            from app.subservice_health import snapshot as subservice_snapshot

            result["subservices"] = subservice_snapshot()
        except Exception:
            logging.exception("Unable to collect local subservice diagnostics")
        return result if isinstance(result, dict) else {}
    except ModuleNotFoundError:
        return {}
    except Exception:
        logging.exception("Unable to collect local backend diagnostics")
        return {}


def recover_local_subservices(diagnostics: dict[str, object]) -> dict[str, object]:
    try:
        from app.subservice_health import recover_failed

        health = diagnostics.get("subservices")
        return recover_failed(health if isinstance(health, dict) else None)
    except ModuleNotFoundError:
        return {"attempted": False, "recovered": [], "failed": [], "fused": []}
    except Exception as exc:
        logging.exception("Unable to recover failed backend subservices")
        return {
            "attempted": True,
            "recovered": [],
            "failed": [{"service_id": "unknown", "error": str(exc)[:500]}],
            "fused": [],
        }


def watch_backend_health(
    stop_event: threading.Event,
    runtime_root: Path,
    *,
    dependencies: HealthDependencies,
    interval_seconds: float = 10,
    failure_threshold: int = 3,
    backend_runtime=None,
    diagnostics_provider=local_backend_diagnostics,
    subservice_recovery_provider=recover_local_subservices,
    recovery_window_seconds: float = 600,
    recovery_max_attempts: int = 2,
) -> None:
    consecutive_failures = 0
    recovery_attempts: deque[float] = deque()
    while not stop_event.wait(max(0.01, interval_seconds)):
        if dependencies.health_matches_runtime(runtime_root):
            consecutive_failures = 0
            continue
        consecutive_failures += 1
        logging.warning(
            "Mio backend health check failed (%s/%s): %s",
            consecutive_failures,
            failure_threshold,
            dependencies.health_error() or "健康接口不可用",
        )
        if consecutive_failures >= max(1, failure_threshold):
            detail = dependencies.health_error() or "健康接口不可用"
            backend_thread = backend_runtime.current_thread() if backend_runtime is not None else None
            thread_alive = bool(backend_thread is not None and backend_thread.is_alive())
            port_open = dependencies.port_is_open()
            diagnostics = diagnostics_provider()
            port_text = f"{dependencies.port()}端口" + ("仍在监听" if port_open else "未监听")
            reason = (
                f"后端连续 {consecutive_failures} 次健康检查失败；最近错误：{detail}；"
                f"{'后端线程存活' if thread_alive else '后端线程已退出或不可管理'}；"
                f"{port_text}"
            )
            if thread_alive and port_open:
                subservice_recovery = subservice_recovery_provider(diagnostics)
                diagnostics = dict(diagnostics)
                diagnostics["subservice_recovery"] = subservice_recovery
                recovered_services = list(subservice_recovery.get("recovered") or [])
                logging.error(
                    "Backend health timed out while thread and port stayed alive; "
                    "full application recovery suppressed. recovered_subservices=%s "
                    "reason=%s diagnostics=%s",
                    recovered_services,
                    reason,
                    json.dumps(diagnostics, ensure_ascii=False, sort_keys=True),
                )
                dependencies.record_recovery_state(
                    "subservice_recovered" if recovered_services else "degraded_no_restart",
                    reason,
                    thread_alive=True,
                    port_open=True,
                    diagnostics=diagnostics,
                    attempts_in_window=len(recovery_attempts),
                )
                consecutive_failures = 0
                continue

            current = time.monotonic()
            cutoff = current - max(1.0, recovery_window_seconds)
            while recovery_attempts and recovery_attempts[0] < cutoff:
                recovery_attempts.popleft()
            if len(recovery_attempts) >= max(1, recovery_max_attempts):
                logging.critical("Backend recovery circuit opened: %s", reason)
                dependencies.record_recovery_state(
                    "fused",
                    reason,
                    thread_alive=thread_alive,
                    port_open=port_open,
                    diagnostics=diagnostics,
                    attempts_in_window=len(recovery_attempts),
                )
                return
            if backend_runtime is None:
                logging.error("Backend cannot be recovered independently: %s", reason)
                dependencies.record_recovery_state(
                    "unmanaged",
                    reason,
                    thread_alive=thread_alive,
                    port_open=port_open,
                    diagnostics=diagnostics,
                    attempts_in_window=len(recovery_attempts),
                )
                return

            recovery_attempts.append(current)
            if backend_runtime.recover(reason):
                logging.warning("Backend recovered without restarting the WebView: %s", reason)
                dependencies.record_recovery_state(
                    "recovered",
                    reason,
                    thread_alive=thread_alive,
                    port_open=port_open,
                    diagnostics=diagnostics,
                    attempts_in_window=len(recovery_attempts),
                )
                consecutive_failures = 0
                continue
            logging.critical("Independent backend recovery failed: %s", reason)
            dependencies.record_recovery_state(
                "recovery_failed",
                reason,
                thread_alive=thread_alive,
                port_open=port_open,
                diagnostics=diagnostics,
                attempts_in_window=len(recovery_attempts),
            )
            return


def bind_backend_listener(port: int, *, host: str, allow_fallback: bool) -> socket.socket:
    """Reserve the actual listener, including Windows excluded-port checks."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((host, port))
        except OSError as exc:
            if not allow_fallback or (exc.errno not in {13, 48, 98, 10013, 10048}
                                      and getattr(exc, "winerror", None) not in {10013, 10048}):
                raise
            listener.bind((host, 0))
            logging.warning("后端端口 %s 无法绑定，自动使用可用端口 %s", port, listener.getsockname()[1])
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise
