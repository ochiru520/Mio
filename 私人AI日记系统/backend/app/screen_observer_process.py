from __future__ import annotations

import json
import ctypes
import os
import queue
import secrets
import subprocess
import sys
import threading
import uuid
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = 1
WORKER_ARGUMENT = "--screen-observer-worker"


class ScreenObserverProcess:
    """Run native screen capture in a disposable child process."""

    def __init__(self, *, command_timeout: float = 10.0) -> None:
        self._command_timeout = max(2.0, float(command_timeout))
        self._request_lock = threading.RLock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._process: subprocess.Popen[bytes] | None = None
        self._connection: Connection | None = None
        self._reader: threading.Thread | None = None
        self._generation = 0
        self._desired_mode = "screen"
        self._desired_scope = "primary"
        self._desired_hwnd = 0
        self._desired_interval_ms = 1000
        self._desired_running = False
        self._has_selection = False
        self._last_error = ""
        self._restart_count = 0
        self._cached_preview: bytes | None = None

    @staticmethod
    def _worker_command(port: int, auth_key: str) -> list[str]:
        arguments = [
            WORKER_ARGUMENT,
            f"--screen-observer-port={port}",
            f"--screen-observer-auth={auth_key}",
            f"--screen-observer-parent={os.getpid()}",
        ]
        if getattr(sys, "frozen", False):
            return [sys.executable, *arguments]
        return [sys.executable, "-m", "app.screen_observer_process", *arguments]

    @staticmethod
    def _worker_environment() -> dict[str, str]:
        environment = os.environ.copy()
        environment["MIO_SCREEN_OBSERVER_WORKER"] = "1"
        if not getattr(sys, "frozen", False):
            backend_dir = str(Path(__file__).resolve().parents[1])
            current = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = backend_dir + (os.pathsep + current if current else "")
        return environment

    def _default_status(self) -> dict[str, Any]:
        title = "全部屏幕" if self._desired_scope == "all" else "主屏幕"
        if self._desired_mode == "window":
            title = "已选择窗口" if self._desired_hwnd else ""
        return {
            "running": False,
            "mode": self._desired_mode,
            "screen_scope": self._desired_scope,
            "hwnd": self._desired_hwnd or None,
            "title": title,
            "change_percent": 0.0,
            "captured_at": "",
            "interval_ms": self._desired_interval_ms,
            "preview_available": self._cached_preview is not None,
            "frame_id": 0,
            "pending_change_percent": 0.0,
            "error": self._last_error,
            "capture_backend": "独立进程（未启动）",
            "capture_backend_error": "",
            "process_isolated": True,
            "process_alive": False,
            "process_pid": 0,
            "process_restarts": self._restart_count,
        }

    def _worker_log_path(self) -> Path:
        runtime_root = os.getenv("MIO_RUNTIME_ROOT", "").strip()
        root = Path(runtime_root) if runtime_root else Path.cwd()
        log_dir = root / "数据" / "桌宠"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "屏幕观察器.log"

    def _start_process_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._terminate_process_locked()
        auth_key = secrets.token_bytes(32)
        listener = Listener(("127.0.0.1", 0), authkey=auth_key)
        listener._listener._socket.settimeout(8.0)
        port = int(listener.address[1])
        log_handle = self._worker_log_path().open("ab")
        try:
            process = subprocess.Popen(
                self._worker_command(port, auth_key.hex()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_handle,
                env=self._worker_environment(),
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            connection = listener.accept()
        except Exception:
            if "process" in locals() and process.poll() is None:
                process.terminate()
            raise
        finally:
            listener.close()
            log_handle.close()
        self._process = process
        self._connection = connection
        self._generation += 1
        generation = self._generation
        self._reader = threading.Thread(
            target=self._reader_loop,
            args=(connection, generation),
            name="mio-screen-observer-ipc",
            daemon=True,
        )
        self._reader.start()
        self._send_locked("ping", {}, timeout=8.0)
        self._last_error = ""

    def _reader_loop(self, connection: Connection, generation: int) -> None:
        try:
            while True:
                response = connection.recv()
                if not isinstance(response, dict):
                    continue
                if response.get("protocol") != PROTOCOL_VERSION:
                    continue
                request_id = str(response.get("request_id") or "")
                with self._pending_lock:
                    target = self._pending.get(request_id)
                if target is not None:
                    target.put(response)
        except (EOFError, OSError):
            pass
        finally:
            if generation != self._generation:
                return
            error = {
                "protocol": PROTOCOL_VERSION,
                "ok": False,
                "error": "屏幕观察器进程已退出",
            }
            with self._pending_lock:
                pending = list(self._pending.values())
            for target in pending:
                target.put(dict(error))

    def _send_locked(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        process = self._process
        connection = self._connection
        if process is None or process.poll() is not None or connection is None:
            raise RuntimeError("屏幕观察器进程不可用")
        request_id = uuid.uuid4().hex
        target: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = target
        request = {
            "protocol": PROTOCOL_VERSION,
            "request_id": request_id,
            "command": command,
            "payload": payload,
        }
        try:
            connection.send(request)
            try:
                response = target.get(timeout=timeout or self._command_timeout)
            except queue.Empty as exc:
                raise TimeoutError(f"屏幕观察器执行 {command} 超时") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "屏幕观察器执行失败"))
        return response.get("result")

    def _restore_state_locked(self) -> None:
        if not self._has_selection:
            return
        try:
            if self._desired_mode == "window" and self._desired_hwnd:
                self._send_locked("select", {"hwnd": self._desired_hwnd})
            else:
                self._send_locked("select_screen", {"scope": self._desired_scope})
        except RuntimeError:
            # Window handles are ephemeral. A closed game must not prevent the
            # restarted observer from serving a fresh window list.
            if self._desired_mode != "window":
                raise
            self._desired_mode = ""
            self._desired_hwnd = 0
            self._desired_running = False
            self._has_selection = False
            self._cached_preview = None
            return
        if self._desired_running:
            self._send_locked("start", {"interval_ms": self._desired_interval_ms})

    def _ensure_process_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        had_process = self._process is not None
        self._start_process_locked()
        if had_process:
            self._restart_count += 1
        self._restore_state_locked()

    def _terminate_process_locked(self) -> None:
        process = self._process
        self._process = None
        connection = self._connection
        self._connection = None
        self._generation += 1
        if process is None:
            return
        try:
            if process.poll() is None and connection is not None:
                request = {
                    "protocol": PROTOCOL_VERSION,
                    "request_id": secrets.token_hex(8),
                    "command": "close",
                    "payload": {},
                }
                connection.send(request)
                process.wait(timeout=1.5)
        except Exception:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _request(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        with self._request_lock:
            for attempt in range(2):
                try:
                    self._ensure_process_locked()
                    return self._send_locked(command, payload or {})
                except (BrokenPipeError, OSError, RuntimeError, TimeoutError) as exc:
                    self._last_error = str(exc)
                    self._terminate_process_locked()
                    if attempt:
                        raise RuntimeError(f"屏幕观察器不可用：{exc}") from exc
            raise RuntimeError("屏幕观察器不可用")

    def list_windows(self) -> list[dict[str, Any]]:
        result = self._request("list_windows")
        return list(result or [])

    def select(self, hwnd: int) -> dict[str, Any]:
        self._cached_preview = None
        result = dict(self._request("select", {"hwnd": int(hwnd)}))
        self.take_preview()
        self._desired_mode = "window"
        self._desired_hwnd = int(hwnd)
        self._has_selection = True
        return self._decorate_status(result)

    def select_screen(self, scope: str = "primary") -> dict[str, Any]:
        normalized = "all" if scope == "all" else "primary"
        self._cached_preview = None
        result = dict(self._request("select_screen", {"scope": normalized}))
        self.take_preview()
        self._desired_mode = "screen"
        self._desired_scope = normalized
        self._desired_hwnd = 0
        self._has_selection = True
        return self._decorate_status(result)

    def capture(self) -> dict[str, Any]:
        return self._decorate_status(dict(self._request("capture")))

    def start(self, interval_ms: int = 1000) -> dict[str, Any]:
        normalized = max(500, min(5000, int(interval_ms)))
        result = dict(self._request("start", {"interval_ms": normalized}))
        self._desired_interval_ms = normalized
        self._desired_running = True
        return self._decorate_status(result)

    def stop(self) -> dict[str, Any]:
        with self._request_lock:
            self._desired_running = False
            if self._process is not None and self._process.poll() is None:
                try:
                    preview_result = self._send_locked("take_preview", {}, timeout=5.0)
                    preview_content = (preview_result or {}).get("content")
                    if preview_content:
                        self._cached_preview = bytes(preview_content)
                except Exception as exc:
                    self._last_error = str(exc)
                try:
                    self._send_locked("stop", {}, timeout=5.0)
                except Exception as exc:
                    self._last_error = str(exc)
            self._terminate_process_locked()
        return self._default_status()

    def take_preview(self) -> bytes | None:
        with self._request_lock:
            if self._process is None or self._process.poll() is not None:
                return self._cached_preview
            try:
                result = self._send_locked("take_preview", {})
            except (BrokenPipeError, OSError, RuntimeError, TimeoutError) as exc:
                self._last_error = str(exc)
                return self._cached_preview
            if not result:
                return self._cached_preview
            content = result.get("content")
            if content:
                self._cached_preview = bytes(content)
            return self._cached_preview

    def claim_analysis_frame(self, after_frame_id: int = 0) -> dict[str, Any] | None:
        result = self._request("claim_analysis_frame", {"after_frame_id": int(after_frame_id)})
        if not result:
            return None
        return dict(result)

    def _decorate_status(self, result: dict[str, Any]) -> dict[str, Any]:
        result["process_isolated"] = True
        result["process_alive"] = self._process is not None and self._process.poll() is None
        result["process_pid"] = int(self._process.pid) if self._process is not None and self._process.poll() is None else 0
        result["process_restarts"] = self._restart_count
        return result

    def status(self) -> dict[str, Any]:
        if self._process is None and not self._desired_running:
            return self._default_status()
        try:
            return self._decorate_status(dict(self._request("status")))
        except RuntimeError as exc:
            self._last_error = str(exc)
            return self._default_status()

    def close(self) -> None:
        with self._request_lock:
            self._desired_running = False
            self._terminate_process_locked()


def _worker_result(observer: Any, command: str, payload: dict[str, Any]) -> Any:
    if command == "ping":
        return {"pid": os.getpid()}
    if command == "list_windows":
        return observer.list_windows()
    if command == "select":
        return observer.select(int(payload.get("hwnd") or 0))
    if command == "select_screen":
        return observer.select_screen(str(payload.get("scope") or "primary"))
    if command == "capture":
        return observer.capture()
    if command == "start":
        return observer.start(int(payload.get("interval_ms") or 1000))
    if command == "stop":
        return observer.stop()
    if command == "status":
        return observer.status()
    if command == "take_preview":
        content = observer.take_preview()
        return {"content": content} if content else None
    if command == "claim_analysis_frame":
        frame = observer.claim_analysis_frame(int(payload.get("after_frame_id") or 0))
        if frame is None:
            return None
        return dict(frame)
    raise ValueError(f"不支持的屏幕观察器命令：{command}")


def worker_main() -> int:
    os.environ["MIO_SCREEN_OBSERVER_WORKER"] = "1"
    from .companion_observation_service import WindowObserver

    port_argument = next(
        (item for item in sys.argv if item.startswith("--screen-observer-port=")),
        "",
    )
    auth_argument = next(
        (item for item in sys.argv if item.startswith("--screen-observer-auth=")),
        "",
    )
    parent_argument = next(
        (item for item in sys.argv if item.startswith("--screen-observer-parent=")),
        "",
    )
    if not port_argument or not auth_argument or not parent_argument:
        raise RuntimeError("屏幕观察器缺少本地连接参数")
    port = int(port_argument.split("=", 1)[1])
    auth_key = bytes.fromhex(auth_argument.split("=", 1)[1])
    parent_pid = int(parent_argument.split("=", 1)[1])
    connection = Client(("127.0.0.1", port), authkey=auth_key)

    def watch_parent() -> None:
        if os.name == "nt":
            synchronize = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
            if not handle:
                os._exit(0)
            try:
                ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
            os._exit(0)
        while True:
            try:
                os.kill(parent_pid, 0)
            except OSError:
                os._exit(0)
            threading.Event().wait(2)

    threading.Thread(target=watch_parent, name="mio-screen-parent-watch", daemon=True).start()
    observer = WindowObserver()
    try:
        while True:
            try:
                request = connection.recv()
                if not isinstance(request, dict):
                    continue
                if request.get("protocol") != PROTOCOL_VERSION:
                    continue
                request_id = str(request.get("request_id") or "")
                command = str(request.get("command") or "")
                if command == "close":
                    break
                result = _worker_result(observer, command, dict(request.get("payload") or {}))
                response = {
                    "protocol": PROTOCOL_VERSION,
                    "request_id": request_id,
                    "ok": True,
                    "result": result,
                }
            except Exception as exc:
                response = {
                    "protocol": PROTOCOL_VERSION,
                    "request_id": str(locals().get("request_id") or ""),
                    "ok": False,
                    "error": str(exc),
                }
            connection.send(response)
    except (EOFError, OSError):
        pass
    finally:
        observer.stop()
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(worker_main())


__all__ = ["ScreenObserverProcess", "worker_main"]
