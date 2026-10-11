"""Find the same installed FFmpeg for attachment parsing and video assembly."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import settings


def resolve_ffmpeg() -> Path:
    candidates = [
        os.getenv('MIO_FFMPEG_BINARY', '').strip(),
        settings.voice_training_dir / 'cache' / 'bin' / 'ffmpeg.exe',
        settings.voice_training_dir / 'ffmpeg.exe',
        shutil.which('ffmpeg') or '',
    ]
    for candidate in candidates:
        if candidate:
            binary = Path(candidate).expanduser()
            try:
                if binary.is_file() and binary.stat().st_size > 0:
                    return binary.resolve()
            except OSError:
                continue
    raise ValueError('缺少本地 FFmpeg，请在环境设置中安装，或配置 MIO_FFMPEG_BINARY。')


def installation_status() -> dict:
    """Detect installed files without executing an arbitrary configured program."""
    try:
        binary = resolve_ffmpeg()
    except ValueError as exc:
        return {'status': 'missing', 'detail': str(exc), 'path': ''}
    return {'status': 'available', 'detail': f'已检测到 FFmpeg：{binary}；转换效果请使用实际文件验证。', 'path': str(binary)}
