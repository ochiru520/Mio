"""Companion appearance; dependencies are explicitly injected."""
from __future__ import annotations

from typing import Any, Callable
from io import BytesIO
from PIL import Image
from pathlib import Path
import base64
import binascii
import time


class AppearanceService:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_MAX_SPRITE_SHEET_BYTES: Callable[..., Any],
                 dep_MAX_SPRITE_SHEET_PIXELS: Callable[..., Any],
                 dep_PET_SPRITE_FILES: Callable[..., Any],
                 dep_settings: Callable[..., Any],
                 ) -> None:
        self._dep_MAX_SPRITE_SHEET_BYTES = dep_MAX_SPRITE_SHEET_BYTES
        self._dep_MAX_SPRITE_SHEET_PIXELS = dep_MAX_SPRITE_SHEET_PIXELS
        self._dep_PET_SPRITE_FILES = dep_PET_SPRITE_FILES
        self._dep_settings = dep_settings

    def default_avatar_path(self) -> Path | None:
        candidates = [
            self._dep_settings().companion_avatar_path,
            self._dep_settings().agent_frontend_dir / "mio-avatar.png",
            self._dep_settings().workspace_root / "澪Agent应用" / "public" / "mio-avatar.png",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None


    def profile_avatar_path(self) -> Path | None:
        candidates = [
            self._dep_settings().mio_avatar_path,
            self._dep_settings().agent_frontend_dir / "mio-avatar.png",
            self._dep_settings().workspace_root / "澪Agent应用" / "public" / "mio-avatar.png",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None


    def pet_sprite_path(self, state: str) -> Path | None:
        filename = self._dep_PET_SPRITE_FILES().get(str(state).strip().lower())
        if filename is None:
            return None
        path = self._dep_settings().companion_sprite_dir / filename
        return path if path.is_file() else None


    def pet_sprite_manifest(self) -> dict[str, Any]:
        states: list[str] = []
        latest_mtime_ns = 0
        for state in self._dep_PET_SPRITE_FILES():
            path = self.pet_sprite_path(state)
            if path is None:
                continue
            states.append(state)
            try:
                latest_mtime_ns = max(latest_mtime_ns, path.stat().st_mtime_ns)
            except OSError:
                pass
        return {
            "states": states,
            "ready": len(states) == len(self._dep_PET_SPRITE_FILES()),
            "expected_count": len(self._dep_PET_SPRITE_FILES()),
            "version": str(latest_mtime_ns) if latest_mtime_ns else "",
        }


    def _decode_image_data_url(self, data_url: str, *, max_bytes: int, label: str) -> bytes:
        if "," not in data_url:
            raise ValueError(f"{label}数据格式不正确。")
        header, encoded = data_url.split(",", 1)
        if not header.startswith("data:image/") or ";base64" not in header:
            raise ValueError(f"请选择图片文件作为{label}。")
        if len(encoded) > (max_bytes * 4 // 3) + 8:
            size_mb = max_bytes // (1024 * 1024)
            raise ValueError(f"{label}图片不能超过 {size_mb}MB。")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValueError(f"{label}图片无法读取。") from exc
        if not raw:
            raise ValueError(f"{label}图片不能为空。")
        if len(raw) > max_bytes:
            size_mb = max_bytes // (1024 * 1024)
            raise ValueError(f"{label}图片不能超过 {size_mb}MB。")
        return raw


    def save_avatar_data_url(self, data_url: str) -> Path:
        raw = self._decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="桌宠头像")
        self._dep_settings().companion_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._dep_settings().companion_avatar_path.with_suffix(".tmp.png")
        try:
            from io import BytesIO

            with Image.open(BytesIO(raw)) as image:
                image.convert("RGBA").save(temporary, format="PNG", optimize=True)
        except (OSError, ValueError) as exc:
            raise ValueError("这个文件不是可用的图片。") from exc
        temporary.replace(self._dep_settings().companion_avatar_path)
        return self._dep_settings().companion_avatar_path


    def save_profile_avatar_data_url(self, data_url: str) -> Path:
        raw = self._decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="Mio 头像")
        self._dep_settings().mio_avatar_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._dep_settings().mio_avatar_path.with_suffix(".tmp.png")
        try:
            with Image.open(BytesIO(raw)) as image:
                image.convert("RGBA").save(temporary, format="PNG", optimize=True)
        except (OSError, ValueError) as exc:
            raise ValueError("这个文件不是可用的图片。") from exc
        temporary.replace(self._dep_settings().mio_avatar_path)
        return self._dep_settings().mio_avatar_path


    def save_user_avatar_data_url(self, data_url: str) -> Path:
        raw = self._decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="用户头像")
        self._dep_settings().user_avatar_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._dep_settings().user_avatar_path.with_suffix(".tmp.png")
        try:
            with Image.open(BytesIO(raw)) as source:
                source.load()
                image = source.convert("RGBA")
                image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                image.save(temporary, format="PNG", optimize=True)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ValueError("这个文件不是可用的图片。") from exc
        temporary.replace(self._dep_settings().user_avatar_path)
        return self._dep_settings().user_avatar_path


    def save_chat_background_data_url(self, data_url: str) -> Path:
        raw = self._decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="对话背景")
        self._dep_settings().chat_background_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._dep_settings().chat_background_path.with_suffix(".tmp.jpg")
        try:
            with Image.open(BytesIO(raw)) as source:
                source.load()
                image = source.convert("RGB")
                image.thumbnail((3840, 2160), Image.Resampling.LANCZOS)
                image.save(temporary, format="JPEG", quality=90, optimize=True)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ValueError("这个文件不是可用的图片。") from exc
        temporary.replace(self._dep_settings().chat_background_path)
        return self._dep_settings().chat_background_path


    def save_sprite_sheet_data_url(self, data_url: str) -> list[Path]:
        raw = self._decode_image_data_url(data_url, max_bytes=self._dep_MAX_SPRITE_SHEET_BYTES(), label="桌宠动作表")
        try:
            with Image.open(BytesIO(raw)) as source:
                columns, rows = (3, 2) if source.width >= source.height else (2, 3)
                if source.width < columns * 32 or source.height < rows * 32:
                    raise ValueError("动作表尺寸太小，无法按 3x2 或 2x3 网格切分。")
                if source.width * source.height > self._dep_MAX_SPRITE_SHEET_PIXELS():
                    raise ValueError("动作表总像素过大，请先缩小图片。")
                source.load()
                image = source.convert("RGBA")
        except ValueError:
            raise
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("这个文件不是可用的动作表图片。") from exc

        columns, rows = (3, 2) if image.width >= image.height else (2, 3)
        cell_width = image.width // columns
        cell_height = image.height // rows
        self._dep_settings().companion_sprite_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = self._dep_settings().companion_sprite_dir / f".上传-{time.time_ns()}"
        staging_dir.mkdir()
        staged: list[tuple[Path, Path]] = []
        try:
            for index, filename in enumerate(self._dep_PET_SPRITE_FILES().values()):
                column = index % columns
                row = index // columns
                left = column * cell_width
                top = row * cell_height
                right = image.width if column == columns - 1 else (column + 1) * cell_width
                bottom = image.height if row == rows - 1 else (row + 1) * cell_height
                temporary = staging_dir / filename
                image.crop((left, top, right, bottom)).save(temporary, format="PNG", optimize=True)
                staged.append((temporary, self._dep_settings().companion_sprite_dir / filename))
            for temporary, target in staged:
                temporary.replace(target)
        except (OSError, ValueError) as exc:
            raise ValueError("动作表切分保存失败。") from exc
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)
            try:
                staging_dir.rmdir()
            except OSError:
                pass
        return [target for _, target in staged]
