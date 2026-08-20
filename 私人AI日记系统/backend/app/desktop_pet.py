from __future__ import annotations

import io
import ctypes
import json
import math
import os
import queue
import random
import time
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps, ImageTk


API_BASE = os.getenv("MIO_PET_API_BASE", f"http://127.0.0.1:{os.getenv('MIO_PORT', '8000')}").rstrip("/")
TRANSPARENT = "#ff00ff"
SHOW_EVENT_NAME = "Local\\MioAgentDesktopShow-7C53C273"
PET_CHAT_EVENT_NAME = "Local\\MioAgentDesktopPetChat-7C53C273"
SPRITE_STATES = ("idle", "blink", "speaking", "cheerful", "concerned", "shy")
BASE_WINDOW_WIDTH = 250
BASE_WINDOW_HEIGHT = 310
DEFAULT_SIZE_PERCENT = 150


def _window_offsets(x: int, y: int) -> str:
    return f"{x:+d}{y:+d}"


def _request_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 4,
) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result if isinstance(result, dict) else {}


class MioDesktopPet:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Mio 桌宠")
        self.pet_config = self._load_pet_config()
        self.pet_size_percent = self._normalize_size(self.pet_config.get("pet_size_percent"))
        self.ui_scale = self.pet_size_percent / 100
        self.window_width = round(BASE_WINDOW_WIDTH * self.ui_scale)
        self.window_height = round(BASE_WINDOW_HEIGHT * self.ui_scale)
        position_x = int(self.pet_config.get("position_x", 80))
        position_y = int(self.pet_config.get("position_y", 420))
        position_x, position_y = self._clamp_position(
            position_x,
            position_y,
            self.window_width,
            self.window_height,
        )
        self.root.geometry(
            f"{self.window_width}x{self.window_height}{_window_offsets(position_x, position_y)}"
        )
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        try:
            self.root.wm_attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.root,
            width=self.window_width,
            height=self.window_height,
            bg=TRANSPARENT,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.source_image = self._load_avatar()
        self.sprites = self._load_sprites()
        self.sprite_version = ""
        self.photo: ImageTk.PhotoImage | None = None
        self.avatar_item = self.canvas.create_image(125 * self.ui_scale, 185 * self.ui_scale)

        self.messages: queue.Queue[dict] = queue.Queue()
        self.last_message_id: int | None = None
        self.last_api_success = time.monotonic()
        self.drag_origin: tuple[int, int] | None = None
        self.window_origin: tuple[int, int] | None = None
        self.animation_start = time.monotonic()
        self.speaking = False
        self.speaking_deadline = 0.0
        self.current_emotion = "neutral"
        self.emotion_deadline = 0.0
        self.current_activity = "idle"
        self.activity_deadline = 0.0
        self.activity_revision = -1
        self.next_blink_at = time.monotonic() + random.uniform(2.8, 5.8)
        self.blink_until = 0.0
        self.observing = False
        self.screen_scope = "primary"
        self.observation_interval_ms = 1000
        self.size_window: tk.Toplevel | None = None
        self.size_value: tk.IntVar | None = None
        self.size_label: tk.Label | None = None

        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Double-Button-1>", lambda _event: self._open_pet_chat())
        self.canvas.bind("<Button-3>", self._show_menu)
        self.root.bind("<Button-3>", self._show_menu)
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="打开桌宠对话", command=self._open_pet_chat)
        self.menu.add_command(label="说句话", command=self._say_hello)
        self.menu.add_command(label="开始屏幕观察", command=self._toggle_observation)
        self.menu.add_separator()
        self.menu.add_command(label="调整大小…", command=self._open_size_control)
        self.menu.add_separator()
        self.menu.add_command(label="打开 Mio", command=self._open_main_agent)
        self.menu.add_separator()
        self.menu.add_command(label="关闭桌宠", command=self.close)

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(30, self._animate)
        self.root.after(300, self._poll_messages)
        self.root.after(500, self._show_next_message)
        self.root.after(800, self._persist_position)

    @staticmethod
    def _normalize_size(value: object) -> int:
        try:
            return max(80, min(240, int(value or DEFAULT_SIZE_PERCENT)))
        except (TypeError, ValueError):
            return DEFAULT_SIZE_PERCENT

    def _virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        try:
            left = int(ctypes.windll.user32.GetSystemMetrics(76))
            top = int(ctypes.windll.user32.GetSystemMetrics(77))
            width = int(ctypes.windll.user32.GetSystemMetrics(78))
            height = int(ctypes.windll.user32.GetSystemMetrics(79))
            if width > 0 and height > 0:
                return left, top, width, height
        except (AttributeError, OSError, ValueError):
            pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _clamp_position(self, x: int, y: int, width: int, height: int) -> tuple[int, int]:
        left, top, screen_width, screen_height = self._virtual_screen_bounds()
        max_x = max(left, left + screen_width - width)
        max_y = max(top, top + screen_height - height)
        return max(left, min(max_x, int(x))), max(top, min(max_y, int(y)))

    def _layout_canvas_items(self) -> None:
        self.canvas.configure(width=self.window_width, height=self.window_height)

    def _apply_size(self, percent: int, *, persist_position: bool = True) -> None:
        normalized = self._normalize_size(percent)
        if normalized == self.pet_size_percent:
            return
        current_x = self.root.winfo_x()
        current_y = self.root.winfo_y()
        center_x = current_x + self.window_width / 2
        center_y = current_y + self.window_height / 2
        self.pet_size_percent = normalized
        self.ui_scale = normalized / 100
        self.window_width = round(BASE_WINDOW_WIDTH * self.ui_scale)
        self.window_height = round(BASE_WINDOW_HEIGHT * self.ui_scale)
        next_x = round(center_x - self.window_width / 2)
        next_y = round(center_y - self.window_height / 2)
        next_x, next_y = self._clamp_position(next_x, next_y, self.window_width, self.window_height)
        self.root.geometry(
            f"{self.window_width}x{self.window_height}{_window_offsets(next_x, next_y)}"
        )
        self._layout_canvas_items()
        if persist_position:
            self._persist_position()

    def _set_size(self, percent: int) -> None:
        normalized = self._normalize_size(percent)
        self._apply_size(normalized, persist_position=False)
        self._persist_size(normalized)
        self._persist_position()

    def _persist_size(self, percent: int) -> None:
        try:
            status = _request_json(
                "/api/companion/size",
                method="PATCH",
                payload={"percent": self._normalize_size(percent)},
            )
            self.pet_config = dict(status.get("pet", {}).get("settings", self.pet_config))
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            self.current_emotion = "concerned"

    def _preview_size(self, value: str) -> None:
        normalized = self._normalize_size(round(float(value)))
        self._apply_size(normalized, persist_position=False)
        if self.size_label is not None:
            self.size_label.configure(text=f"{normalized}%")

    def _commit_previewed_size(self, _event=None) -> None:
        self._persist_size(self.pet_size_percent)
        self._persist_position()

    def _close_size_control(self) -> None:
        self._commit_previewed_size()
        window = self.size_window
        self.size_window = None
        self.size_value = None
        self.size_label = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _open_size_control(self) -> None:
        if self.size_window is not None:
            try:
                self.size_window.deiconify()
                self.size_window.lift()
                self.size_window.focus_force()
                return
            except tk.TclError:
                self.size_window = None

        window = tk.Toplevel(self.root)
        self.size_window = window
        window.title("调整桌宠大小")
        window.resizable(False, False)
        window.attributes("-topmost", True)
        window.configure(bg="#f7fafb")
        popup_x, popup_y = self._clamp_position(
            self.root.winfo_x() - 20,
            self.root.winfo_y() - 125,
            300,
            112,
        )
        window.geometry(
            f"300x112{_window_offsets(popup_x, popup_y)}"
        )

        heading = tk.Frame(window, bg="#f7fafb")
        heading.pack(fill="x", padx=16, pady=(13, 3))
        tk.Label(
            heading,
            text="桌宠大小",
            bg="#f7fafb",
            fg="#263943",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left")
        self.size_label = tk.Label(
            heading,
            text=f"{self.pet_size_percent}%",
            bg="#f7fafb",
            fg="#3b7181",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.size_label.pack(side="right")

        self.size_value = tk.IntVar(value=self.pet_size_percent)
        scale = tk.Scale(
            window,
            from_=80,
            to=240,
            orient="horizontal",
            resolution=1,
            showvalue=False,
            variable=self.size_value,
            command=self._preview_size,
            bg="#f7fafb",
            fg="#263943",
            activebackground="#4f8392",
            troughcolor="#d8e3e6",
            highlightthickness=0,
            bd=0,
            sliderlength=18,
        )
        scale.pack(fill="x", padx=12, pady=(0, 4))
        scale.bind("<ButtonRelease-1>", self._commit_previewed_size)
        window.protocol("WM_DELETE_WINDOW", self._close_size_control)

    def _persist_position(self) -> None:
        try:
            _request_json(
                "/api/companion/position",
                method="PATCH",
                payload={"x": self.root.winfo_x(), "y": self.root.winfo_y()},
            )
        except (OSError, urllib.error.URLError, ValueError):
            pass

    def _load_pet_config(self) -> dict:
        try:
            return dict(_request_json("/api/companion/status").get("pet", {}).get("settings", {}))
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            return {}

    def _load_avatar(self) -> Image.Image:
        try:
            image = self._load_remote_image(f"/api/companion/avatar?ts={time.time_ns()}")
        except (OSError, urllib.error.URLError, ValueError):
            image = Image.new("RGBA", (512, 512), "#dce8eb")
            draw = ImageDraw.Draw(image)
            draw.ellipse((96, 80, 416, 400), fill="#8eabb5")
            draw.text((225, 210), "MIO", fill="#ffffff")
        image = ImageOps.fit(image, (512, 512), method=Image.Resampling.LANCZOS, centering=(0.5, 0.38))
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).ellipse((10, 10, 502, 502), fill=255)
        image.putalpha(mask)
        return image

    def _load_remote_image(self, path: str) -> Image.Image:
        request = urllib.request.Request(f"{API_BASE}{path}")
        with urllib.request.urlopen(request, timeout=5) as response:
            return Image.open(io.BytesIO(response.read())).convert("RGBA")

    def _load_sprites(self) -> dict[str, Image.Image]:
        sprites: dict[str, Image.Image] = {}
        timestamp = time.time_ns()
        for state in SPRITE_STATES:
            try:
                image = self._load_remote_image(f"/api/companion/sprite/{state}?ts={timestamp}")
                sprites[state] = self._prepare_sprite(image)
            except (OSError, urllib.error.URLError, ValueError):
                continue
        return sprites

    @staticmethod
    def _prepare_sprite(image: Image.Image) -> Image.Image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        bounds = alpha.getbbox()
        if bounds:
            rgba = rgba.crop(bounds)
        rgba.thumbnail((480, 600), Image.Resampling.LANCZOS)
        prepared = Image.new("RGBA", (512, 640), (0, 0, 0, 0))
        x = (prepared.width - rgba.width) // 2
        y = prepared.height - rgba.height - 14
        prepared.alpha_composite(rgba, (x, max(0, y)))
        return prepared

    def _sprite_state(self, now: float) -> str:
        emotion_state = {
            "cheerful": "cheerful",
            "concerned": "concerned",
            "serious": "concerned",
            "gentle": "concerned",
            "shy": "shy",
        }.get(self.current_emotion)
        activity = self._effective_activity(now)
        if emotion_state in self.sprites and activity in {"responding", "speaking"}:
            return emotion_state
        if activity in {"thinking", "working"} and "blink" in self.sprites:
            return "blink"
        if activity == "listening" and "concerned" in self.sprites:
            return "concerned"
        if self.speaking and "speaking" in self.sprites:
            return "speaking"
        if emotion_state in self.sprites:
            return emotion_state
        if now < self.blink_until and "blink" in self.sprites:
            return "blink"
        if now >= self.next_blink_at and "blink" in self.sprites:
            self.blink_until = now + random.uniform(0.11, 0.18)
            self.next_blink_at = now + random.uniform(2.8, 6.2)
            return "blink"
        return "idle"

    def _effective_activity(self, now: float) -> str:
        if self.activity_deadline and now >= self.activity_deadline:
            self.current_activity = "idle"
            self.activity_deadline = 0.0
        if self.current_activity != "idle":
            return self.current_activity
        return "observing" if self.observing else "idle"

    def _apply_server_activity(self, activity: object) -> None:
        if not isinstance(activity, dict):
            return
        try:
            revision = int(activity.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        if revision == self.activity_revision:
            return
        self.activity_revision = revision
        state = str(activity.get("state") or "idle")
        self.current_activity = state if state in {
            "idle", "listening", "thinking", "working", "responding", "speaking", "observing"
        } else "idle"
        try:
            remaining_seconds = max(0.0, float(activity.get("remaining_ms") or 0) / 1000)
        except (TypeError, ValueError):
            remaining_seconds = 0.0
        self.activity_deadline = time.monotonic() + remaining_seconds if remaining_seconds else 0.0
        emotion = str(activity.get("emotion") or "neutral")
        if emotion != "neutral":
            self.current_emotion = emotion
            self.emotion_deadline = max(
                self.emotion_deadline,
                time.monotonic() + max(4.0, remaining_seconds),
            )

    @staticmethod
    def _harden_alpha(image: Image.Image) -> Image.Image:
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        alpha = image.getchannel("A").point(lambda value: 255 if value >= 80 else 0)
        image.putalpha(alpha)
        return image

    def _animate(self) -> None:
        now = time.monotonic()
        elapsed = now - self.animation_start
        activity = self._effective_activity(now)
        emotion_motion = {
            "cheerful": (1.35, 5.0, 5.0),
            "concerned": (0.72, 2.0, 2.5),
            "serious": (0.68, 1.5, 2.0),
            "gentle": (0.82, 2.5, 3.0),
            "shy": (0.78, 2.0, 2.0),
        }.get(self.current_emotion, (1.0, 3.0, 4.0))
        activity_motion = {
            "listening": (0.78, 1.8, 2.0),
            "thinking": (0.62, 1.2, 1.5),
            "working": (0.72, 1.4, 1.8),
            "observing": (0.68, 0.8, 1.2),
        }.get(activity)
        speed, sway, float_height = activity_motion or emotion_motion
        scale = 1 + math.sin(elapsed * 2.0 * speed) * 0.012
        if self.speaking or activity == "speaking":
            scale += abs(math.sin(elapsed * 8.0)) * 0.025
        sprite = self.sprites.get(self._sprite_state(now)) or self.sprites.get("idle")
        if sprite is not None:
            height = max(round(248 * self.ui_scale), int(260 * self.ui_scale * scale))
            width = max(round(198 * self.ui_scale), int(height * 0.8))
            frame = sprite.resize((width, height), Image.Resampling.LANCZOS)
            center_y = 164 * self.ui_scale
        else:
            size = max(round(186 * self.ui_scale), int(194 * self.ui_scale * scale))
            frame = self.source_image.resize((size, size), Image.Resampling.LANCZOS)
            if self.speaking:
                self._draw_mouth(frame, elapsed)
            center_y = 183 * self.ui_scale
        frame = self._harden_alpha(frame)
        self.photo = ImageTk.PhotoImage(frame)
        x = 125 * self.ui_scale + math.sin(elapsed * 0.75 * speed) * sway * self.ui_scale
        y = center_y + math.sin(elapsed * 1.25 * speed) * float_height * self.ui_scale
        self.canvas.itemconfigure(self.avatar_item, image=self.photo)
        self.canvas.coords(self.avatar_item, x, y)
        if self.speaking_deadline and now >= self.speaking_deadline:
            self.speaking = False
            self.speaking_deadline = 0
        if self.emotion_deadline and now >= self.emotion_deadline:
            self.current_emotion = "neutral"
            self.emotion_deadline = 0
        self.root.after(33, self._animate)

    def _draw_mouth(self, frame: Image.Image, elapsed: float) -> None:
        size = frame.width
        center_x = size * 0.5
        center_y = size * 0.55
        width = size * (0.07 if self.current_emotion == "shy" else 0.09)
        openness = 0.025 + abs(math.sin(elapsed * 11.0)) * 0.045
        if self.current_emotion in {"concerned", "serious"}:
            openness *= 0.75
        height = size * openness
        draw = ImageDraw.Draw(frame, "RGBA")
        draw.ellipse(
            (
                center_x - width / 2,
                center_y - height / 2,
                center_x + width / 2,
                center_y + height / 2,
            ),
            fill=(90, 42, 51, 180),
            outline=(69, 36, 43, 185),
            width=max(1, size // 120),
        )

    def _poll_messages(self) -> None:
        try:
            path = "/api/companion/feed"
            if self.last_message_id is not None:
                path += f"?after_id={self.last_message_id}"
            data = _request_json(path)
            self.last_message_id = int(data.get("latest_id") or self.last_message_id or 0)
            for message in data.get("messages") or []:
                if str(message.get("content") or "").strip():
                    self.messages.put(message)
            status = _request_json("/api/companion/status")
            self.pet_config = dict(status.get("pet", {}).get("settings", self.pet_config))
            configured_size = self._normalize_size(self.pet_config.get("pet_size_percent"))
            if self.size_window is None and configured_size != self.pet_size_percent:
                self._apply_size(configured_size, persist_position=False)
            sprite_version = str(status.get("pet", {}).get("sprite_version") or "")
            if sprite_version and sprite_version != self.sprite_version:
                self.sprites = self._load_sprites()
                self.sprite_version = sprite_version
            screen = status.get("screen") or status.get("window") or {}
            self.observing = bool(screen.get("running"))
            self.screen_scope = str(screen.get("screen_scope") or self.screen_scope)
            self.observation_interval_ms = int(screen.get("interval_ms") or self.observation_interval_ms)
            self._apply_server_activity(status.get("pet", {}).get("activity"))
            self.last_api_success = time.monotonic()
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            if time.monotonic() - self.last_api_success > 25:
                self.close()
                return
        self.root.after(1800, self._poll_messages)

    def _show_next_message(self) -> None:
        if not self.messages.empty():
            message = self.messages.get_nowait()
            content = " ".join(str(message.get("content") or "").split()).strip()
            if content:
                request_id = str(message.get("request_id") or "")
                grouped = [content]
                if request_id:
                    pending = list(self.messages.queue)
                    for queued in pending:
                        if str(queued.get("request_id") or "") != request_id:
                            break
                        self.messages.get_nowait()
                        next_content = " ".join(str(queued.get("content") or "").split()).strip()
                        if next_content:
                            grouped.append(next_content)
                content = " ".join(grouped)
                self.current_emotion = str(message.get("emotion") or "neutral")
                self.emotion_deadline = time.monotonic() + max(4, min(20, len(content) / 5 + 3))
                self.current_activity = "responding"
                self.activity_deadline = time.monotonic() + max(5, min(24, len(content) / 5 + 4))
                source = str(message.get("source") or "")
                try:
                    config = self.pet_config
                    should_speak = (
                        source != "desktop_pet"
                        and not (
                            source in {"screen", "game", "desktop_pet_wake"}
                            and bool(config.get("screen_direct_voice_enabled", True))
                        )
                        and bool(config.get("voice_enabled", True))
                        and (
                        (
                            source not in {"proactive", "desktop_proactive", "screen", "game"}
                            or source in {"proactive", "desktop_proactive"}
                            and bool(config.get("speak_proactive", False))
                            or source == "screen"
                            and bool(config.get("speak_screen_observations", False))
                            or source == "game"
                            and bool(config.get("speak_game_observations", True))
                        )
                        )
                    )
                    if should_speak:
                        _request_json(
                            "/api/companion/voice/speak",
                            method="POST",
                            payload={
                                "text": content,
                                "context": content,
                                "emotion": self.current_emotion,
                            },
                        )
                        self.speaking = True
                        self.current_activity = "speaking"
                        self.speaking_deadline = time.monotonic() + max(2, min(30, len(content) / 6 + 1))
                        self.activity_deadline = self.speaking_deadline
                except (OSError, urllib.error.URLError, ValueError):
                    pass
        self.root.after(500, self._show_next_message)

    def _say_hello(self) -> None:
        try:
            _request_json("/api/companion/voice/speak", method="POST", payload={"text": "我在这里"})
            self.speaking = True
            self.speaking_deadline = time.monotonic() + 3
            self.current_activity = "speaking"
            self.activity_deadline = self.speaking_deadline
        except (OSError, urllib.error.URLError):
            pass

    def _open_pet_chat(self) -> None:
        event_handle = ctypes.windll.kernel32.OpenEventW(0x0002, False, PET_CHAT_EVENT_NAME)
        if event_handle:
            ctypes.windll.kernel32.SetEvent(event_handle)
            ctypes.windll.kernel32.CloseHandle(event_handle)
            return
        self._open_main_agent(open_pet_chat=True)

    def _open_main_agent(self, *, open_pet_chat: bool = False) -> None:
        event_handle = ctypes.windll.kernel32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)
        if event_handle:
            ctypes.windll.kernel32.SetEvent(event_handle)
            ctypes.windll.kernel32.CloseHandle(event_handle)
            return
        try:
            suffix = "#desktop-pet-chat" if open_pet_chat else ""
            os.startfile(f"{API_BASE}/agent-app/{suffix}")
        except OSError:
            pass

    def _drag_start(self, event) -> None:
        self.drag_origin = (event.x_root, event.y_root)
        self.window_origin = (self.root.winfo_x(), self.root.winfo_y())

    def _drag_move(self, event) -> None:
        if self.drag_origin is None or self.window_origin is None:
            return
        x = self.window_origin[0] + event.x_root - self.drag_origin[0]
        y = self.window_origin[1] + event.y_root - self.drag_origin[1]
        self.root.geometry(_window_offsets(x, y))

    def _drag_end(self, _event) -> None:
        if self.drag_origin is None:
            return
        self.drag_origin = None
        self.window_origin = None
        try:
            _request_json(
                "/api/companion/position",
                method="PATCH",
                payload={"x": self.root.winfo_x(), "y": self.root.winfo_y()},
            )
        except (OSError, urllib.error.URLError, ValueError):
            pass

    def _toggle_observation(self) -> None:
        try:
            if self.observing:
                _request_json("/api/companion/screen/stop", method="POST", payload={})
                self.observing = False
            else:
                _request_json(
                    "/api/companion/screen/start",
                    method="POST",
                    payload={
                        "scope": self.screen_scope,
                        "interval_ms": self.observation_interval_ms,
                    },
                )
                self.observing = True
        except (OSError, urllib.error.URLError, ValueError):
            self.current_emotion = "concerned"

    def _show_menu(self, event) -> None:
        self.menu.entryconfigure(2, label="暂停屏幕观察" if self.observing else "开始屏幕观察")
        self.menu.tk_popup(event.x_root, event.y_root)

    def close(self) -> None:
        try:
            _request_json("/api/companion/screen/stop", method="POST", payload={}, timeout=2)
        except (OSError, urllib.error.URLError, ValueError):
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    try:
        config = dict(_request_json("/api/companion/status").get("pet", {}).get("settings", {}))
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
        config = {}
    if str(config.get("pet_renderer") or "classic").lower() == "live2d":
        from .desktop_pet_live2d import main as live2d_main

        return live2d_main(config)
    MioDesktopPet().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
