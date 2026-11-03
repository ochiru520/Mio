from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import isfinite
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from .screen_frame_processor import calculate_change_metrics


PACE_SETTINGS = {
    "calm": {"change_percent": 10.0, "motion_seconds": 24.0},
    "normal": {"change_percent": 6.0, "motion_seconds": 12.0},
    "dense": {"change_percent": 3.5, "motion_seconds": 6.0},
}


@dataclass
class _WatchFrame:
    data: dict[str, Any]
    size: tuple[int, int]
    sampled_at: float


class AdaptiveScreenWatch:
    """Select changed frames locally; the caller owns capture, locking and inference."""

    def __init__(self, pace: str = "normal") -> None:
        self.configure(pace)
        self.reset()

    def configure(self, pace: str) -> None:
        if pace not in PACE_SETTINGS:
            raise ValueError("陪看节奏必须为 calm、normal 或 dense")
        self._pace = pace

    def reset(self) -> None:
        self._source: tuple[Any, ...] | None = None
        self._frames: list[_WatchFrame] = []
        self._latest: _WatchFrame | None = None
        self._previous: Image.Image | None = None
        self._kept: Image.Image | None = None
        self._last_push: float | None = None
        self._motion = 0.0
        self._selected = 0
        self._skipped = 0
        self._sheets = 0
        self._replaced = 0

    @staticmethod
    def _time(now: float) -> float:
        value = float(now)
        if not isfinite(value):
            raise ValueError("陪看采样时间无效")
        return value

    def push(self, frame: dict[str, Any], now: float) -> None:
        sampled_at = self._time(now)
        data = dict(frame)
        content = bytes(data.get("content") or b"")
        if not content:
            raise ValueError("陪看画面为空")
        data["content"] = content
        data.pop("watch_sheet", None)
        with Image.open(BytesIO(content)) as source:
            size = source.size
            source.draft("RGB", (192, 108))
            thumbnail = source.convert("RGB")
        thumbnail.thumbnail((192, 108), Image.Resampling.BILINEAR)
        identity = (
            data.get("mode"), data.get("hwnd"), data.get("screen_scope"),
            data.get("title"), size,
        )
        if identity != self._source:
            self._source = identity
            self._frames.clear()
            self._previous = None
            self._kept = None
            self._last_push = None
            self._motion = 0.0

        if self._last_push is not None:
            sampled_at = max(sampled_at, self._last_push)
        entry = _WatchFrame(data, size, sampled_at)
        self._latest = entry
        elapsed = 0.0 if self._last_push is None else min(2.0, sampled_at - self._last_push)
        self._last_push = sampled_at
        adjacent = calculate_change_metrics(self._previous, thumbnail).effective_percent
        retained = calculate_change_metrics(self._kept, thumbnail).effective_percent
        self._previous = thumbnail
        settings = PACE_SETTINGS[self._pace]

        # Ignore tiny cursor/noise changes before accumulating sustained motion.
        self._motion += max(0.0, adjacent - 0.35) * elapsed
        first = self._kept is None
        changed = retained >= 1.5 and (
            adjacent >= settings["change_percent"]
            or self._motion >= settings["motion_seconds"]
        )
        if not first and not changed:
            self._skipped += 1
            return
        self._kept = thumbnail.copy()
        self._motion = 0.0
        self._selected += 1
        if len(self._frames) >= 4:
            self._frames.pop(0)
            self._replaced += 1
        self._frames.append(entry)

    def take(self, now: float, force: bool = False) -> dict[str, Any] | None:
        self._time(now)
        if self._latest is None:
            return None
        if not force:
            if not self._frames:
                return None
        selected = [self._latest] if force else list(self._frames)
        # Historical samples explain the change; the final cell must still show
        # the real current frame rather than borrowing only its newer timestamp.
        if not force and selected[-1] is not self._latest:
            if len(selected) >= 4:
                selected.pop(0)
                self._replaced += 1
            selected.append(self._latest)
        result = dict(selected[-1].data)
        if force:
            width, height = self._latest.size
            content = self._latest.data["content"]
        else:
            content = self._compose(selected)
            width, height = 1280, 768
        result["content"] = content
        result["watch_sheet"] = {
            "count": len(selected),
            "frame_ids": [item.data.get("frame_id", 0) for item in selected],
            "captured_at": [str(item.data.get("captured_at") or "") for item in selected],
            "frame_times": [item.sampled_at for item in selected],
            "captured_monotonic": [item.data.get("captured_monotonic") for item in selected],
            "order": "row-major",
            "width": width,
            "height": height,
            "forced": bool(force),
        }
        self._frames.clear()
        self._kept = self._previous.copy() if self._previous is not None else None
        self._motion = 0.0
        self._sheets += 1
        return result

    @staticmethod
    def _compose(frames: list[_WatchFrame]) -> bytes:
        canvas = Image.new("RGB", (1280, 768), (24, 26, 31))
        columns = 1 if len(frames) == 1 else 2
        rows = 1 if len(frames) <= 2 else 2
        cell_width, cell_height = canvas.width // columns, canvas.height // rows
        draw = ImageDraw.Draw(canvas)
        for index, entry in enumerate(frames):
            left = index % columns * cell_width
            top = index // columns * cell_height
            with Image.open(BytesIO(entry.data["content"])) as source:
                image = ImageOps.contain(
                    source.convert("RGB"), (cell_width, cell_height - 24), Image.Resampling.LANCZOS,
                )
            canvas.paste(image, (left + (cell_width - image.width) // 2, top + 24 + (cell_height - 24 - image.height) // 2))
            captured_at = str(entry.data.get("captured_at") or "").encode("ascii", "replace").decode("ascii")[:36]
            draw.text((left + 8, top + 6), f"#{index + 1}  {captured_at}", fill=(245, 245, 245))
        output = BytesIO()
        canvas.save(output, format="JPEG", quality=85)
        return output.getvalue()

    def status(self) -> dict[str, Any]:
        return {
            "mode": "adaptive",
            "pace": self._pace,
            "gap_seconds": 0,
            "buffered_frames": len(self._frames),
            "selected_frames": self._selected,
            "skipped_frames": self._skipped,
            "replaced_frames": self._replaced,
            "sheets": self._sheets,
        }


__all__ = ["AdaptiveScreenWatch", "PACE_SETTINGS"]
