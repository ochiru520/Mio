"""Package acquisition metadata; deployment URLs never replace pinned checksums."""
from __future__ import annotations

import json
import os
import threading
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlsplit

from .config import settings

PACKAGE_IDS = {'genie_runtime', 'gpt_sovits'}
_lock = threading.RLock()
RELEASE_URL = 'https://github.com/ochiru520/Mio/releases'


def _config_path() -> Path:
    return settings.data_dir / '组件下载源.json'


def _read_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('invalid config')
        return value
    except (OSError, ValueError) as exc:
        raise ValueError('组件下载源配置无法读取，请重新保存下载地址。') from exc


def _urls(values: list) -> list[str]:
    if not isinstance(values, list) or len(values) > 8:
        raise ValueError('最多填写 8 个 HTTPS 下载地址。')
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError('下载地址必须是 HTTPS 链接。')
        value = value.strip()
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value) or parsed.fragment:
            raise ValueError('请填写不带账号密码和片段的完整 HTTPS 下载地址。')
        if value not in result:
            result.append(value)
    return result


def effective_source(dep_id: str, source: dict) -> dict:
    if dep_id not in PACKAGE_IDS:
        raise ValueError('此组件没有独立数据包。')
    with _lock:
        config = _read_config()
    result = dict(source)
    entry = config.get(dep_id)
    if isinstance(entry, dict) and entry.get('urls'):
        result['urls'] = _urls(entry['urls'])
    else:
        result['urls'] = _urls(source.get('urls', []))
    return result


def save_urls(dep_id: str, values: list) -> dict:
    if dep_id not in PACKAGE_IDS:
        raise ValueError('此组件不支持自定义数据包地址。')
    urls = _urls(values)
    with _lock:
        try:
            config = _read_config()
        except ValueError:
            # Preserve the unreadable file before an explicit user save repairs it.
            path = _config_path()
            path.rename(path.with_name(path.name + '.invalid-' + uuid4().hex))
            config = {}
        if urls:
            config[dep_id] = {'urls': urls}
        else:
            config.pop(dep_id, None)
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    return {'saved': True}


def package_directories() -> list[Path]:
    program = settings.agent_control_scripts_dir.parent.parent
    candidates = [settings.voice_training_dir / 'downloads', settings.workspace_root / '模型', program / '模型', program / 'models', program]
    # The installer records where companion ZIPs were supplied, not a disk scan.
    for record in (settings.data_dir/'安装来源目录.txt', settings.project_root/'安装来源目录.txt'):
        try:
            origin = record.read_text(encoding='utf-8-sig').strip()
            if origin and Path(origin).is_absolute():
                candidates.extend([Path(origin)/'模型', Path(origin)])
        except OSError:
            continue
    custom = os.environ.get('MIO_MODEL_PACKAGE_DIR', '').strip()
    if custom:
        candidates.insert(0, Path(custom))
    return list(dict.fromkeys(p.resolve() for p in candidates))


def find_local_package(source: dict) -> Path | None:
    name = str(source.get('file_name') or '')
    if not name or Path(name).name != name or '/' in name or '\\' in name:
        return None
    for directory in package_directories():
        path = directory / name
        if path.is_file():
            return path
    return None


def package_info(dep_id: str) -> dict:
    if dep_id not in PACKAGE_IDS:
        raise ValueError('此组件没有独立数据包。')
    from .dependency_installer import _package_source
    source = _package_source(dep_id)
    if not source.get('sha256') or not source.get('size_bytes'):
        raise ValueError('组件校验清单缺失，请修复 Mio 安装。')
    urls = source.get('urls', [])
    local = find_local_package(source)
    return {
        'id': dep_id, 'file_name': source.get('file_name'), 'size_bytes': source.get('size_bytes'),
        'sha256': source.get('sha256'), 'version': source.get('version'), 'urls': urls,
        'local_available': local is not None, 'release_url': RELEASE_URL,
        'message': ('已找到本机数据包，安装前会核对大小和 SHA-256。' if local else
                    '已配置下载地址，安装时会下载并核对固定版本。' if urls else
                    '此版本尚未提供公开数据包下载地址。已有 ZIP 可选择离线安装；拿到发布者提供的 HTTPS 链接后也可在此保存。'),
        'license_notice': '引擎权重和角色音色的分发范围需分别确认；主程序安装包不包含私人音色。',
    }
