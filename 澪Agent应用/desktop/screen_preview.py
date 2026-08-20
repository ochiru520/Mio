from __future__ import annotations

import ctypes
import io
import os
import queue
import threading
import tkinter as tk
import urllib.error
import urllib.request

from PIL import Image, ImageTk


def _process_is_alive(process_id: int) -> bool:
    if process_id <= 0 or os.name != "nt":
        return True
    synchronize = 0x00100000
    wait_timeout = 0x00000102
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, process_id)
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def run_preview_window(*, host: str, port: int, parent_pid: int = 0) -> int:
    root = tk.Tk()
    root.title("Mio · 独立屏幕预览")
    root.geometry("900x570")
    root.minsize(640, 420)
    root.configure(background="#172126")

    image_label = tk.Label(
        root,
        text="正在读取屏幕画面",
        foreground="#d7e3e6",
        background="#172126",
        font=("Microsoft YaHei UI", 12),
    )
    image_label.pack(fill="both", expand=True, padx=12, pady=(12, 6))
    status_label = tk.Label(
        root,
        text="预览只保存在内存中，不会写入磁盘",
        foreground="#9ba9af",
        background="#172126",
        font=("Microsoft YaHei UI", 9),
        anchor="w",
    )
    status_label.pack(fill="x", padx=14, pady=(0, 10))

    closed = threading.Event()
    fetch_lock = threading.Lock()
    updates: queue.Queue[tuple[str, object]] = queue.Queue()
    preview_url = f"http://{host}:{port}/api/companion/screen/preview"

    def apply_preview(content: bytes) -> None:
        if closed.is_set():
            return
        try:
            with Image.open(io.BytesIO(content)) as source:
                image = source.convert("RGB")
            available_width = max(320, root.winfo_width() - 28)
            available_height = max(240, root.winfo_height() - 72)
            image.thumbnail((available_width, available_height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            image_label.configure(image=photo, text="")
            image_label.image = photo
            status_label.configure(text="独立预览每 3 秒刷新 · 画面不会写入磁盘")
        except Exception as exc:
            status_label.configure(text=f"预览图解码失败：{exc}")

    def show_error(message: str) -> None:
        if closed.is_set():
            return
        status_label.configure(text=message)

    def fetch_preview() -> None:
        if not fetch_lock.acquire(blocking=False):
            return
        try:
            request = urllib.request.Request(
                f"{preview_url}?t={os.times().elapsed}",
                headers={"Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(request, timeout=4) as response:
                content = response.read()
            updates.put(("preview", content))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                updates.put(("error", "还没有可预览的画面，请先开始观察或让 Mio 看一眼"))
            else:
                updates.put(("error", f"预览读取失败：HTTP {exc.code}"))
        except Exception as exc:
            updates.put(("error", f"预览读取失败：{exc}"))
        finally:
            fetch_lock.release()

    def apply_updates() -> None:
        if closed.is_set():
            return
        while True:
            try:
                kind, value = updates.get_nowait()
            except queue.Empty:
                break
            if kind == "preview":
                apply_preview(bytes(value))
            else:
                show_error(str(value))
        root.after(100, apply_updates)

    def poll() -> None:
        if closed.is_set():
            return
        if parent_pid and not _process_is_alive(parent_pid):
            root.destroy()
            return
        threading.Thread(target=fetch_preview, name="mio-preview-fetch", daemon=True).start()
        root.after(3000, poll)

    def close() -> None:
        closed.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(50, apply_updates)
    root.after(100, poll)
    root.mainloop()
    closed.set()
    return 0


__all__ = ["run_preview_window"]
