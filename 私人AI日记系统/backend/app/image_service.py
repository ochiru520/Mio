from __future__ import annotations

import base64
import io
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from .config import settings


ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


@dataclass(frozen=True)
class ImageAttachment:
    data_url: str
    mime_type: str
    source: str
    content: bytes = b""


EXTENSION_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _guess_mime_type(source: str, content_type: str = "") -> str:
    mime_type = content_type.split(";", 1)[0].strip().lower()
    if mime_type in ALLOWED_IMAGE_MIME_TYPES:
        return mime_type

    guessed, _ = mimetypes.guess_type(urlparse(source).path or source)
    if guessed in ALLOWED_IMAGE_MIME_TYPES:
        return guessed

    return ""


def _detect_mime_type(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _mime_type_for_content(content: bytes, source: str = "", content_type: str = "") -> str:
    detected = _detect_mime_type(content)
    if detected:
        return detected
    return _guess_mime_type(source, content_type)


def _to_data_url(content: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _normalise_raster_image(content: bytes, source: str) -> tuple[bytes, str]:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.seek(0)
            if image.width * image.height > 50_000_000:
                raise RuntimeError(f"图片尺寸过大：{source}")
            image.thumbnail((4096, 4096))
            has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
            output = io.BytesIO()
            if has_alpha:
                image.convert("RGBA").save(output, format="PNG", optimize=True)
                return output.getvalue(), "image/png"
            image.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError) as exc:
        raise RuntimeError(f"暂时无法识别这种图片格式：{source}") from exc


async def _download_image(url: str) -> ImageAttachment:
    content_type = ""
    chunks: list[bytes] = []
    total_size = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"图片下载失败：HTTP {response.status_code}")

            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > settings.qq_image_max_bytes:
                        raise RuntimeError("图片太大，先压缩一下再发我。")
                except ValueError:
                    pass

            content_type = response.headers.get("content-type", "")
            async for chunk in response.aiter_bytes():
                total_size += len(chunk)
                if total_size > settings.qq_image_max_bytes:
                    raise RuntimeError("图片太大，先压缩一下再发我。")
                chunks.append(chunk)

    content = b"".join(chunks)
    mime_type = _mime_type_for_content(content, url, content_type)
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise RuntimeError(f"暂不支持这种图片格式：{mime_type or 'unknown'}")

    return ImageAttachment(data_url=_to_data_url(content, mime_type), mime_type=mime_type, source=url, content=content)


def _local_path_from_source(source: str) -> Path:
    parsed = urlparse(source)
    if parsed.scheme == "file":
        if parsed.netloc:
            return Path(unquote(f"{parsed.netloc}{parsed.path}"))
        return Path(unquote(parsed.path.lstrip("/")))
    return Path(source)


def _read_local_image(path_text: str) -> ImageAttachment:
    path = _local_path_from_source(path_text)
    if not path.exists() or not path.is_file():
        raise RuntimeError("图片文件不存在。")

    content = path.read_bytes()
    if len(content) > settings.qq_image_max_bytes:
        raise RuntimeError("图片太大，先压缩一下再发我。")

    mime_type = _mime_type_for_content(content, path_text)
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise RuntimeError(f"暂不支持这种图片格式：{mime_type or 'unknown'}")

    return ImageAttachment(data_url=_to_data_url(content, mime_type), mime_type=mime_type, source=path_text, content=content)


def _read_base64_image(source: str) -> ImageAttachment:
    encoded = source.removeprefix("base64://")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RuntimeError("图片 base64 内容无效。") from exc

    if len(content) > settings.qq_image_max_bytes:
        raise RuntimeError("图片太大，先压缩一下再发我。")

    mime_type = _mime_type_for_content(content)
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise RuntimeError(f"暂不支持这种图片格式：{mime_type or 'unknown'}")
    return ImageAttachment(data_url=_to_data_url(content, mime_type), mime_type=mime_type, source="base64://...", content=content)


def image_attachment_from_data_url(data_url: str, source: str = "desktop") -> ImageAttachment:
    prefix, separator, encoded = data_url.partition(",")
    if not separator or not prefix.lower().startswith("data:image/") or ";base64" not in prefix.lower():
        raise RuntimeError("图片数据格式无效。")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RuntimeError("图片 base64 内容无效。") from exc
    if len(content) > settings.qq_image_max_bytes:
        raise RuntimeError("图片太大，先压缩一下再发我。")
    mime_type = _mime_type_for_content(content, source, prefix[5:].split(";", 1)[0])
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        content, mime_type = _normalise_raster_image(content, source)
    return ImageAttachment(
        data_url=_to_data_url(content, mime_type),
        mime_type=mime_type,
        source=source,
        content=content,
    )


def archive_image_attachments(attachments: list[ImageAttachment]) -> list[str]:
    """把收到的图片按逻辑日期存到 数据/照片/，返回保存的文件名。"""
    if not settings.photo_archive_enabled or not attachments:
        return []
    from datetime import datetime

    from . import db

    date = db.today_string()
    folder = settings.photo_dir / date
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(db.now_iso()).strftime("%H%M%S")

    saved: list[str] = []
    for index, attachment in enumerate(attachments, start=1):
        if not attachment.content:
            continue
        ext = EXTENSION_BY_MIME.get(attachment.mime_type, "jpg")
        name = f"{stamp}_{index}.{ext}"
        target = folder / name
        counter = 1
        while target.exists():
            name = f"{stamp}_{index}_{counter}.{ext}"
            target = folder / name
            counter += 1
        target.write_bytes(attachment.content)
        saved.append(name)
    return saved


async def load_image_attachments(image_sources: list[str]) -> tuple[list[ImageAttachment], list[str]]:
    attachments: list[ImageAttachment] = []
    errors: list[str] = []

    for source in image_sources[: settings.qq_image_max_count]:
        clean_source = source.strip()
        if not clean_source:
            continue

        try:
            if clean_source.startswith(("http://", "https://")):
                attachments.append(await _download_image(clean_source))
            elif clean_source.startswith("base64://"):
                attachments.append(_read_base64_image(clean_source))
            else:
                attachments.append(_read_local_image(clean_source))
        except Exception as exc:
            errors.append(str(exc))

    skipped = max(0, len(image_sources) - settings.qq_image_max_count)
    if skipped:
        errors.append(f"这次最多看 {settings.qq_image_max_count} 张图，后面 {skipped} 张先跳过。")

    return attachments, errors
