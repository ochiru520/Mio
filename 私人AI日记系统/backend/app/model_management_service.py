"""User-facing optional model inventory and explicit, bounded storage operations."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import ctypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid

from .config import settings

_LOCK = threading.RLock()
_CATALOG_LOCK = threading.Lock()
_CATALOG_KEY = None
_CATALOG_CACHE: dict[str, dict] = {}
_CATALOG_RUNNING: set[str] = set()
_CATALOG_REPEAT: set[str] = set()
CATALOG_TTL_SECONDS = 30
RESERVE_BYTES = 8 * 1024**3
VOICE_FIELDS = ('gpt_sovits_gpt_weights', 'gpt_sovits_sovits_weights', 'gpt_sovits_ref_audio',
                'so_vits_svc_model_path', 'so_vits_svc_config_path')
CATALOG = (
    dict(id='genie_runtime', label='轻量本地声音引擎', category='speech', dependency_id='genie_runtime',
         description='在电脑上运行转换后的 ONNX 音色；不需要 CUDA。', compatibility='只加载对应 Genie 音色包，不能直接加载 .ckpt / .pth。',
         requirements='Windows 64 位及相应运行环境；安装后通过试听确认。', verification='verified_local',
         sources=[dict(label='Mio 引擎与音色下载', url='https://github.com/ochiru520/Mio/releases')]),
    dict(id='gpt_sovits', label='Mio 轻量音色包', category='speech', dependency_id='gpt_sovits',
         description='角色声音和参考素材，需要先安装轻量声音引擎。', compatibility='Genie ONNX 音色；与原生 V2 CUDA 权重分别管理。',
         requirements='先安装轻量声音引擎。', verification='verified_local',
         sources=[dict(label='Mio 音色下载', url='https://github.com/ochiru520/Mio/releases')]),
    dict(id='mio_v2', label='V2 原声与自训声音', category='speech', dependency_id=None,
         description='使用 GPT-SoVITS V2 原始权重，支持中文和日语流式合成。', compatibility='V2 配套 .ckpt / .pth 与参考音频；Pro、ProPlus、v5 不能混用。',
         requirements='兼容 NVIDIA CUDA 环境；到声音设置导入权重并加载测试。', verification='verified_local',
         sources=[dict(label='GPT-SoVITS 官方说明', url='https://github.com/RVC-Boss/GPT-SoVITS'),
                  dict(label='基础模型（ModelScope）', url='https://modelscope.cn/models/AI-ModelScope/GPT-SoVITS'),
                  dict(label='基础模型（Hugging Face）', url='https://huggingface.co/lj1995/GPT-SoVITS')]),
    dict(id='whisper', label='听懂声音', category='recognition', dependency_id='whisper',
         description='本地识别麦克风或电脑声音中的文字。', compatibility='使用 Mio 支持的 faster-whisper 环境；外部共用模型不由 Mio 删除。',
         requirements='安装后进行录音识别测试，语言和速度受模型及电脑影响。', verification='verified_local',
         sources=[dict(label='faster-whisper 官方项目', url='https://github.com/SYSTRAN/faster-whisper')]),
    dict(id='ollama_vision', label='看懂屏幕（本地）', category='vision', dependency_id='ollama_vision',
         description='在本机分析图片，支持保留旧视觉模型或验证后切换推荐模型；本地失败不会自动转发整屏到云端。', compatibility='Mio 独立 Ollama 模型库；模型可分别管理，与系统已有 Ollama 分开。',
         requirements='安装完成后启动并验证；与语音共同运行需要另行检查资源。', verification='verified_local',
         sources=[dict(label='Ollama 官方下载', url='https://ollama.com/download/windows'),
                  dict(label='Qwen3.5 模型说明', url='https://ollama.com/library/qwen3.5')]),
    dict(id='aniflive', label='AnifLive 快速声音（实验）', category='speech', dependency_id=None,
         description='使用 GPT-SoVITS V2 自训权重进行流式加速，可在这里安装和转换音色。', compatibility='支持配套 V2 权重与参考音频；Genie、ProPlus、v5 不能直接混用。全部动态形状精度仍待验证。',
         requirements='Windows 64 位与 NVIDIA 显卡；首次下载运行库并为本机编译，中日发声检查通过后试听选择。', verification='experimental',
         sources=[dict(label='AnifLive 官方项目', url='https://github.com/Hiruynk/AnifLive-TTS')]),
    dict(id='gpt_sovits_v5', label='GPT-SoVITS v5（高级）', category='speech', dependency_id=None,
         description='连接已经安装的 v5 兼容服务。', compatibility='以实际服务报告版本为准；V2 微调权重不能直接使用。',
         requirements='需要自行准备兼容服务和对应模型，目前不是一键安装项目。', verification='unverified',
         sources=[dict(label='GPT-SoVITS 项目', url='https://github.com/RVC-Boss/GPT-SoVITS')]),
    dict(id='indextts2', label='IndexTTS（高级）', category='speech', dependency_id=None,
         description='通过参考音频克隆声音，可在声音设置选择版本。', compatibility='2 与 2.5 服务和模型分别配套，不使用 GPT-SoVITS 训练权重。',
         requirements='先准备相应版本的独立适配服务。', verification='unverified',
         sources=[dict(label='IndexTTS 官方模型与说明', url='https://github.com/index-tts/index-tts')]),
)


def _canonical(path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _plain(path: Path) -> None:
    for current in (*reversed(path.absolute().parents), path.absolute()):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('目录包含符号链接或目录联接，请选择普通模型目录。')


def _tree(root: Path) -> list[dict]:
    _plain(root)
    if not root.exists():
        return []
    paths = [root]
    if root.is_dir():
        def fail(error):
            raise error
        for folder, dirs, files in os.walk(root, followlinks=False, onerror=fail):
            paths.extend(Path(folder) / name for name in sorted(dirs + files))
    records = []
    for path in paths:
        _plain(path)
        info = path.stat()
        if path.is_file():
            records.append(dict(path=str(path), size=info.st_size, mtime=info.st_mtime_ns))
    return sorted(records, key=lambda item: item['path'])


def _token(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def hardware() -> dict:
    result = dict(ram_total_bytes=None, ram_available_bytes=None, gpus=[],
                  recommendation='先选择需要的功能；下载完成后进行实际测试，再决定是否常驻。',
                  limitations=['硬件检测用于提示，不代表模型或语音与视觉同时运行已经通过。'])
    if os.name == 'nt':
        class Memory(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(key, ctypes.c_ulonglong) for key in
                ('total', 'free', 'total_page', 'free_page', 'total_virtual', 'free_virtual', 'free_ext')]
        memory = Memory(); memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result.update(ram_total_bytes=memory.total, ram_available_bytes=memory.free)
    try:
        probe = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,memory.free', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if probe.returncode == 0:
            for line in probe.stdout.splitlines():
                name, total, free = line.rsplit(',', 2)
                result['gpus'].append(dict(name=name.strip(), total_bytes=int(total.strip()) * 1024**2,
                                           free_bytes=int(free.strip()) * 1024**2))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    if not result['gpus']:
        result['recommendation'] = '未检测到可用 NVIDIA 显卡；优先考虑轻量本地声音或已配置的云端声音，其他能力先检查兼容性。'
    return result


def _storage_root(group: str) -> Path:
    if group not in {'voice', 'vision'}:
        raise ValueError('不支持的模型存储分组。')
    return Path(settings.voice_training_dir if group == 'voice' else settings.local_vision_dir).absolute()


def _managed_targets(group: str) -> list[Path]:
    from .dependency_removal import _targets, _assert_safe
    ids = ('genie_runtime', 'gpt_sovits', 'whisper') if group == 'voice' else ('ollama_vision',)
    paths = sorted(set(path.absolute() for dep_id in ids for path in _targets(dep_id)[1]), key=lambda p: len(p.parts))
    if group == 'vision':
        # The selected model belongs to the model library, not the program build.
        # Copy it with the library so relocating cannot silently choose another model.
        paths.append((_storage_root(group) / 'model-selection.json').absolute())
    result = [path for index, path in enumerate(paths) if not any(path.is_relative_to(parent) for parent in paths[:index])]
    for path in result:
        _assert_safe(_storage_root(group), path)
    return result


def _storage_records(group: str) -> list[dict]:
    return [record for target in _managed_targets(group) for record in _tree(target)]


def _nearest_existing(path: Path) -> Path:
    while not path.exists():
        if path == path.parent:
            raise ValueError('无法找到目标磁盘。')
        path = path.parent
    return path


def _catalog_items() -> list[dict]:
    from .voice_asset_service import stable_selection
    with stable_selection():
        return _catalog_items_snapshot()


def _catalog_items_snapshot() -> list[dict]:
    from . import companion_service, dependency_installer, local_voice_engines, local_vision_service, system_audio_service
    dependencies = {item['id']: item for item in dependency_installer.list_dependencies()}
    config = companion_service.load_config()
    voice = companion_service.voice_runtime_health()
    current = ('mio_v2' if config.get('voice_engine') == 'gpt_sovits' and config.get('local_voice_runtime') == 'mio_v2'
               else 'genie_runtime' if config.get('voice_engine') == 'gpt_sovits' and config.get('local_voice_runtime') == 'genie'
               else config.get('voice_engine'))
    vision = local_vision_service.passive_status()
    recognition = system_audio_service.status()
    items = []
    for definition in CATALOG:
        entry = dict(definition); dep = dependencies.get(entry['dependency_id'], {})
        entry.update(size_bytes=None, install_mode='managed_build' if entry['id'] == 'aniflive' else 'automatic' if entry['dependency_id'] else 'external',
                     installed=dep.get('status') in {'ready', 'installed', 'unverified', 'degraded'},
                     status=dep.get('status', 'missing'), running=False, selected=current == entry['id'],
                     install_path=dep.get('install_path', ''), can_release=False,
                     can_uninstall=bool(dep.get('can_uninstall')), uninstall_impact=dep.get('uninstall_impact', ''),
                     limitations=[entry['compatibility']], installing=bool(dep.get('installing')))
        entry['dependency'] = dep
        if entry['dependency_id'] in {'genie_runtime', 'gpt_sovits'}:
            from .dependency_package_service import package_info
            try:
                package = package_info(entry['dependency_id']); entry['size_bytes'] = package['size_bytes']
                entry['package'] = package
            except (OSError, ValueError):
                pass
        if entry['id'] in local_voice_engines.ENGINES:
            managed = local_voice_engines.manager.status(entry['id'], config)
            entry['installed'] = managed['managed_available']
            entry['running'] = bool(managed['managed_running'])
            entry['status'] = 'loading' if managed['service_loading'] else 'running' if entry['running'] else 'installed' if entry['installed'] else 'missing'
            entry['can_release'] = entry['running']
            entry['limitations'].append(managed.get('managed_install_error') or '')
        elif entry['id'] in {'genie_runtime', 'gpt_sovits'}:
            entry['running'] = current == 'genie_runtime' and bool(voice.get('managed_running'))
            entry['can_release'] = entry['running']
        elif entry['id'] == 'ollama_vision':
            entry['running'] = bool(vision.get('owned_server'))
            entry['can_release'] = entry['running']
        elif entry['id'] == 'whisper':
            entry['running'] = bool(recognition.get('running'))
            entry['can_release'] = entry['running']
        items.append(entry)
    return items


def _catalog_storage() -> list[dict]:
    storage = []
    for group, label in (('voice', '声音与识别模型'), ('vision', '本地视觉模型')):
        root = _storage_root(group)
        try:
            records = _storage_records(group); size = sum(row['size'] for row in records)
            if group == 'voice':
                size += sum(row['size'] for row in _tree(root / 'aniflive-managed'))
            free = shutil.disk_usage(_nearest_existing(root)).free
            storage.append(dict(id=group, label=label, path=str(root), size_bytes=size, free_bytes=free))
        except (ValueError, OSError) as exc:
            storage.append(dict(id=group, label=label, path=str(root), size_bytes=None, free_bytes=None, error=str(exc)))
    return storage


def _catalog_update(section: str, key, operation) -> None:
    try:
        value = operation()
        update = dict(value=value, at=time.monotonic(), checked_at=datetime.now().astimezone().isoformat(timespec='seconds'), error='')
    except Exception:
        # Catalog discovery must never erase its last valid observation or leak
        # private exception details. Mutations still revalidate their own plans.
        update = dict(at=time.monotonic(), error='检查未完成，可重新检查；其他模型与功能仍可查看。')
    with _CATALOG_LOCK:
        if _CATALOG_KEY == key:
            if section in _CATALOG_REPEAT:
                update['at'] = 0
            _CATALOG_CACHE[section] = {**_CATALOG_CACHE.get(section, {}), **update}
        _CATALOG_REPEAT.discard(section)
        _CATALOG_RUNNING.discard(section)


def catalog(refresh: bool = False) -> dict:
    """Return static choices immediately; disk walks and probes run once in background.

    Each section has its own worker and observation timestamp, so a stuck probe
    cannot withhold the catalog, block the other sections or spawn a worker on
    every poll. Cached values are explicitly labelled until the next observation.
    """
    global _CATALOG_KEY
    key = (str(settings.voice_training_dir), str(settings.local_vision_dir), str(settings.companion_dir))
    operations = {'items': _catalog_items, 'storage': _catalog_storage, 'hardware': hardware}
    pending = []
    with _CATALOG_LOCK:
        if key != _CATALOG_KEY:
            _CATALOG_KEY = key; _CATALOG_CACHE.clear()
        now = time.monotonic()
        for section, operation in operations.items():
            cached = _CATALOG_CACHE.get(section, {})
            if refresh and section in _CATALOG_RUNNING:
                _CATALOG_REPEAT.add(section)
            installing = section == 'items' and any(row.get('dependency', {}).get('installing') for row in cached.get('value', []))
            ttl = 1.5 if installing else CATALOG_TTL_SECONDS
            if section not in _CATALOG_RUNNING and (refresh or now - cached.get('at', 0) >= ttl or not cached):
                _CATALOG_RUNNING.add(section); pending.append((section, operation))
        snapshot = copy.deepcopy(_CATALOG_CACHE)
        running = set(_CATALOG_RUNNING)
    for section, operation in pending:
        threading.Thread(target=_catalog_update, args=(section, key, operation), daemon=True,
                         name=f'mio-model-catalog-{section}').start()
    sections = {}
    for section in operations:
        row = snapshot.get(section, {})
        sections[section] = dict(state='checking' if section in running else 'error' if row.get('error') else 'ready',
            checked_at=row.get('checked_at'), stale=section in running or bool(row.get('error')),
            error=row.get('error', ''), has_value='value' in row)
    default_items = [dict(definition, status='checking', installed=None, running=None, selected=False,
                          install_mode='managed_build' if definition['id'] == 'aniflive' else 'automatic' if definition['dependency_id'] else 'external',
                          size_bytes=None, can_release=False, can_uninstall=False,
                          limitations=[definition['compatibility']]) for definition in CATALOG]
    return dict(items=snapshot.get('items', {}).get('value', default_items),
                storage=snapshot.get('storage', {}).get('value', []),
                hardware=snapshot.get('hardware', {}).get('value'), sections=sections,
                checking=bool(running), checked_at=snapshot.get('items', {}).get('checked_at'))


def _idle(group: str):
    from . import dependency_installer
    from . import anif_setup_service
    from .call_session_service import manager
    if manager.status().get('active'):
        raise ValueError('请先结束语音通话，再操作模型资源。')
    if group == 'voice' and anif_setup_service.busy():
        raise ValueError('Anif 正在安装或编译，请等待完成或先取消，再操作声音资源。')
    if group == 'vision':
        from . import local_vision_models, local_vision_service
        if local_vision_models.download_status().get('active'):
            raise ValueError('视觉模型正在下载，请先取消或等待完成后再操作安装位置。')
        local_vision_service.assert_model_idle()
    ids = ('ollama_vision',) if group == 'vision' else ('genie_runtime', 'gpt_sovits', 'whisper')
    if any(dependency_installer._install_running(dep_id) for dep_id in ids):
        raise ValueError('相关组件正在安装，请等待完成后再操作。')


def release(model_id: str) -> dict:
    from . import companion_service, local_voice_engines, local_vision_service, system_audio_service, genie_tts_service
    from . import dependency_installer, voice_asset_service
    known = {item['id'] for item in CATALOG}
    if model_id not in known:
        raise ValueError('不支持的模型。')
    if model_id == 'ollama_vision':
        with dependency_installer.dependency_operation('ollama_vision'), local_vision_service._lifecycle_lock:
            _idle('vision')
            local_vision_service.stop_server()
    else:
        with voice_asset_service._selection_transaction(), _LOCK, dependency_installer.dependency_operation('genie_runtime'), local_voice_engines.manager.lifecycle_lock, genie_tts_service._worker_lock:
            _idle('voice')
            if model_id == 'whisper':
                system_audio_service.stop()
            elif model_id in local_voice_engines.ENGINES:
                local_voice_engines.manager.stop(model_id)
            else:
                genie_tts_service.stop_worker()
    return dict(id=model_id, released=True, message='已停止相关本地服务；模型文件和设置保留，下次使用会重新加载。')


def storage_preview(group: str, path: str, migrate: bool = False) -> dict:
    source = _storage_root(group); destination = Path(str(path)).expanduser()
    if group == 'voice' and (source / 'aniflive-managed').exists():
        raise ValueError('请先在 Anif 安装面板卸载受管组件，再更改声音模型位置；原始音色会保留，新位置需要重新编译。')
    if not destination.is_absolute() or any(ord(char) < 32 for char in str(path)):
        raise ValueError('请选择完整的模型安装目录。')
    destination = destination.absolute(); _plain(destination); _plain(source)
    resolved = destination.resolve(); origin = source.resolve()
    protected = [settings.data_dir, settings.db_path, settings.companion_dir, settings.workspace_root, Path.home(), Path(destination.anchor)]
    if any(resolved == Path(value).resolve() or (Path(value).resolve().is_relative_to(resolved)) for value in protected):
        raise ValueError('目标目录不能是系统、个人数据或项目根目录。')
    private = [settings.data_dir, settings.companion_dir]
    if any(resolved.is_relative_to(Path(value).resolve()) for value in private):
        raise ValueError('模型目录不能放入个人数据或音色资料目录。')
    other = _storage_root('vision' if group == 'voice' else 'voice').resolve()
    if resolved == other or resolved.is_relative_to(other) or other.is_relative_to(resolved):
        raise ValueError('声音和视觉模型需要分别使用独立目录。')
    if resolved == origin or resolved.is_relative_to(origin) or origin.is_relative_to(resolved):
        raise ValueError('新目录不能与当前模型目录重合或互相包含。')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('请选择不存在或为空的专用目录，避免覆盖其他文件。')
    records = _storage_records(group)
    size = sum(row['size'] for row in records)
    if any(target.exists() for target in _managed_targets(group)) and not migrate:
        raise ValueError('当前目录已有模型，请选择“复制并切换”迁移，避免已安装模型失去连接。')
    free = shutil.disk_usage(_nearest_existing(destination)).free
    if free < (size if migrate else 0) + RESERVE_BYTES:
        raise ValueError('目标磁盘空间不足，复制后至少需要保留 8 GiB。')
    public = dict(group=group, source=str(source), destination=str(destination), size_bytes=size, copy_bytes=size if migrate else 0,
                  free_bytes=free, mode='copy_and_switch' if migrate else 'new_location',
                  impact='只复制 Mio 安装器管理的模型与环境，然后切换安装位置；原文件保留，便于回退。' if migrate else '以后安装的相关组件保存到新目录。',
                  preserved='聊天、日记、个人训练素材、外部音色引用及原位置文件保留；此操作不会释放旧盘空间。')
    # Free space changes continuously; validate it again, but never use it as plan identity.
    identity = {key: value for key, value in public.items() if key != 'free_bytes'}
    if group == 'voice':
        from . import companion_service
        voice_config = companion_service.load_config()
        identity['voice_profiles'] = voice_config.get('voice_profiles') or {}
    public['token'] = _token(dict(**identity, records=records))
    return public


def _rewrite_owned_voice_paths(config: dict, source: Path, destination: Path, targets: list[Path]) -> dict:
    changes = {}
    profiles = json.loads(json.dumps(config.get('voice_profiles') or {}))
    changed = False
    for profile in profiles.values():
        for field in VOICE_FIELDS:
            value = profile.get(field)
            if not value:
                continue
            candidate = Path(value).absolute()
            if any(candidate.is_relative_to(target) for target in targets):
                profile[field] = str(destination / candidate.relative_to(source)); changed = True
    if changed:
        changes['voice_profiles'] = profiles
    return changes


def storage_commit(group: str, path: str, migrate: bool, token: str) -> dict:
    if group == 'voice':
        from .voice_asset_service import _selection_transaction
        with _selection_transaction():
            return _storage_commit_impl(group, path, migrate, token)
    return _storage_commit_impl(group, path, migrate, token)


def _storage_commit_impl(group: str, path: str, migrate: bool, token: str) -> dict:
    from . import companion_service, dependency_installer, config as configuration, dependency_probe, environment_check_service
    with _LOCK, dependency_installer.dependency_operation('ollama_vision' if group == 'vision' else 'genie_runtime'):
        _idle(group)
        plan = storage_preview(group, path, migrate)
        if not token or token != plan['token']:
            raise ValueError('模型文件或安装目录已变化，请重新预览迁移范围。')
        source, destination = Path(plan['source']), Path(plan['destination'])
        from . import local_voice_engines, genie_tts_service, local_vision_service, system_audio_service
        targets = _managed_targets(group)
        # Keep lifecycle locks through copy and config commit: no model can restart midway.
        with ExitStack() as stack:
            stack.enter_context(local_vision_service._lifecycle_lock if group == 'vision' else local_voice_engines.manager.lifecycle_lock)
            if group == 'vision':
                local_vision_service.assert_model_idle()
                local_vision_service.stop_server()
            else:
                stack.enter_context(genie_tts_service._worker_lock)
                local_voice_engines.manager.stop_all(); genie_tts_service.stop_worker(); system_audio_service.stop()
            current = storage_preview(group, path, migrate)
            if current['token'] != token:
                raise ValueError('停止服务后模型文件已变化，请重新预览。')
            staging = destination.parent / ('.mio-model-migrate-' + uuid.uuid4().hex)
            _plain(staging); staging.mkdir(parents=True, exist_ok=False)
            before_config = companion_service.load_config() if group == 'voice' else None
            if group == 'voice':
                from . import voice_asset_service
                before_assets = voice_asset_service._asset_records()
            else:
                before_assets = None
            voice_saved, root_saved, installed = False, False, False
            changes = {}
            after_assets = None
            root_key = 'voice_training_dir' if group == 'voice' else 'local_vision_dir'
            try:
                if migrate:
                    for target in targets:
                        if not target.exists():
                            continue
                        relative = target.relative_to(source); output = staging / relative
                        output.parent.mkdir(parents=True, exist_ok=True)
                        if target.is_dir():
                            shutil.copytree(target, output, copy_function=shutil.copy2)
                        else:
                            shutil.copy2(target, output)
                    records = _storage_records(group)
                    if storage_preview(group, path, migrate)['token'] != token:
                        raise ValueError('复制期间源模型文件发生变化，请重新预览。')
                    for row in records:
                        original = Path(row['path']); copied = staging / original.relative_to(source)
                        if copied.stat().st_size != row['size'] or _digest(copied) != _digest(original):
                            raise OSError('迁移校验失败，原文件与原配置已保留。')
                    if storage_preview(group, path, migrate)['token'] != token:
                        raise ValueError('校验期间源模型文件发生变化，请重新预览。')
                _plain(destination)
                if destination.exists():
                    destination.rmdir()
                staging.rename(destination); installed = True
                if before_config is not None:
                    latest = companion_service.load_config()
                    if latest.get('voice_profiles') != before_config.get('voice_profiles'):
                        raise ValueError('迁移期间音色设置已变化，请重新预览；原配置和文件保留。')
                    changes = _rewrite_owned_voice_paths(before_config, source, destination, targets)
                    if changes:
                        voice_saved = True
                        companion_service.save_config(changes)
                    effective = dict(before_config, **changes)
                    after_assets = voice_asset_service.relocated_records(before_assets, source, destination, targets, effective)
                    if after_assets != before_assets:
                        voice_asset_service._write_assets(after_assets)
                if group == 'voice':
                    root_saved = True  # Persistence may succeed before notification fails.
                configuration.save_runtime_settings({root_key: str(destination)})
                root_saved = True
                dependency_probe.invalidate(); environment_check_service.refresh_detection_cache()
                return dict(ok=True, group=group, path=str(destination), copied_bytes=plan['copy_bytes'], source_retained=True,
                            restart_required=True, message='新安装位置已保存。受管文件复制校验完成，原文件保留；请重启 Mio 后验证。')
            except BaseException:
                if root_saved and (group != 'voice' or Path(getattr(settings, root_key)).absolute() == destination.absolute()):
                    configuration.save_runtime_settings({root_key: str(source)})
                if voice_saved:
                    latest = companion_service.load_config()
                    if latest.get('voice_profiles') == changes.get('voice_profiles'):
                        companion_service.save_config({'voice_profiles': before_config['voice_profiles']})
                if after_assets is not None and after_assets != before_assets and voice_asset_service._asset_records() == after_assets:
                    voice_asset_service._write_assets(before_assets)
                # This transaction owns only its unique staging/new destination.
                cleanup = destination if installed else staging
                if cleanup.exists():
                    shutil.rmtree(cleanup)
                raise


def _voice_owned(path: Path) -> bool:
    root = settings.companion_dir.absolute()
    candidate = path.absolute()
    if candidate.is_relative_to(root / '音色权重'):
        return True
    return candidate.parent == root and candidate.name.startswith('音色参考-')


def voices() -> dict:
    from .voice_asset_service import stable_selection
    with stable_selection():
        return _voices_snapshot()


def _voices_snapshot() -> dict:
    from . import companion_service
    from . import voice_asset_service
    config = companion_service.load_config(); profiles = config.get('voice_profiles') or {}
    conversions = voice_asset_service.conversions(config)
    conversion_references = voice_asset_service.referenced_originals(config)
    references = {}
    for profile_id, profile in profiles.items():
        for field in VOICE_FIELDS:
            if profile.get(field):
                references.setdefault(_canonical(profile[field]), set()).add(profile_id)
    result = []
    for profile_id, profile in profiles.items():
        unique = {_canonical(profile[field]): Path(profile[field]) for field in VOICE_FIELDS if profile.get(field)}
        owned, external, shared = [], [], []
        for canonical, path in unique.items():
            if references[canonical] - {profile_id} or canonical in conversion_references or path.is_file() and path.stat().st_nlink > 1:
                shared.append(str(path))
            elif not _voice_owned(path):
                external.append(str(path))
            elif path.is_file():
                _plain(path); owned.append(dict(path=str(path), size=path.stat().st_size))
        info = voice_asset_service.inspect_profile(profile_id, config, known_conversions=conversions)
        info.update(owned_bytes=sum(item['size'] for item in owned), external_files=external,
                    shared_files=shared, can_delete_files=bool(owned),
                    conversion_references=[str(path) for path in unique.values() if _canonical(path) in conversion_references])
        result.append(info)
    return dict(voices=result, message='升级保留已保存的音色与引擎选择；切换前加载验证，原始音色与转换副本分别保留。')


def voice_removal_preview(profile_id: str, delete_files: bool = False, remove_profile: bool = True) -> dict:
    from . import companion_service
    from . import voice_asset_service
    config = companion_service.load_config(); profiles = config.get('voice_profiles') or {}
    if profile_id not in profiles:
        raise ValueError('音色不存在。')
    if remove_profile and len(profiles) <= 1:
        raise ValueError('请至少保留一个音色配置，或先添加其他音色。')
    if not remove_profile and not delete_files:
        raise ValueError('请选择删除文件或删除音色配置。')
    if profile_id == config.get('default_voice_profile_id'):
        raise ValueError('请先切换并验证另一个音色，再移除或清理当前默认音色；不会自动改用其他声音。')
    if remove_profile and any(other_id != profile_id and candidate.get('so_vits_svc_base_profile_id') == profile_id
                              for other_id, candidate in profiles.items()):
        raise ValueError('其他变声音色仍使用此音色作为基底，请先更换其基底音色；原始音色和配置保留。')
    if delete_files:
        voice_asset_service.require_known_conversion_references()
    info = next(item for item in _voices_snapshot()['voices'] if item['id'] == profile_id)
    if remove_profile and (any(row.get('registered') for row in info.get('conversions', []))
            or config.get('voice_engine') == 'aniflive' and config.get('aniflive_voice_profile') == profile_id):
        raise ValueError('此音色仍关联已登记的 Anif 加速副本，请先到 Anif 安装面板卸载加速副本；原始音色会保留。')
    protected = {_canonical(path) for path in info['external_files'] + info['shared_files']}
    paths = sorted({str(Path(value).absolute()) for key in VOICE_FIELDS if (value := profiles[profile_id].get(key))
                    and _canonical(value) not in protected and _voice_owned(Path(value)) and Path(value).is_file()}) if delete_files else []
    records = [record for path in paths for record in _tree(Path(path))]
    if len(records) != len(paths):
        raise ValueError('音色文件已变化，请重新检查删除范围。')
    result = dict(id=profile_id, name=info['name'], delete_files=delete_files, remove_profile=remove_profile,
                  paths=paths, size_bytes=sum(row['size'] for row in records),
                  shared_files=info['shared_files'], external_files=info['external_files'],
                  conversion_references=info.get('conversion_references', []),
                  conversions=info.get('conversions', []),
                  asset_record=voice_asset_service.profile_record(profile_id),
                  replacement_profile_id=next((key for key in profiles if key != profile_id), '') if remove_profile and profile_id == config.get('default_voice_profile_id') else '',
                  impact='删除选定音色配置。' if remove_profile else '保留音色配置，删除其独占的 Mio 管理文件；需要重新导入后才能使用。',
                  preserved='其他音色或 Anif 转换副本引用的原件、硬链接共用文件、外部目录模型和个人素材保留。转换副本需在 Anif 面板单独卸载。')
    result['token'] = _token(dict(**result, records=records, profiles=profiles, default=config.get('default_voice_profile_id')))
    return result


def voice_remove(profile_id: str, token: str, delete_files: bool = False, remove_profile: bool = True) -> dict:
    from . import companion_service, dependency_installer, local_voice_engines, genie_tts_service
    from . import voice_asset_service
    with voice_asset_service._selection_transaction(), _LOCK, dependency_installer.dependency_operation('genie_runtime'), local_voice_engines.manager.lifecycle_lock, genie_tts_service._worker_lock:
        _idle('voice')
        plan = voice_removal_preview(profile_id, delete_files, remove_profile)
        if not token or token != plan['token']:
            raise ValueError('音色配置或文件已变化，请重新检查删除范围。')
        config = companion_service.load_config()
        if plan['paths'] or profile_id == config.get('default_voice_profile_id'):
            companion_service.stop_voice_service()
        if voice_removal_preview(profile_id, delete_files, remove_profile)['token'] != token:
            raise ValueError('停止声音期间音色或引用已变化，请重新检查删除范围；未删除文件。')
        staged = []
        proposed_profiles = None
        proposed_default = None
        try:
            for raw in plan['paths']:
                path = Path(raw); _plain(path)
                temporary = path.with_name('.mio-removal-' + uuid.uuid4().hex + path.suffix)
                path.rename(temporary); staged.append((path, temporary))
            latest = companion_service.load_config()
            if latest.get('voice_profiles') != config.get('voice_profiles') or latest.get('default_voice_profile_id') != config.get('default_voice_profile_id'):
                raise ValueError('清理期间音色设置已变化，请重新检查删除范围；未删除文件或覆盖新设置。')
            if remove_profile:
                profiles = dict(config['voice_profiles']); profiles.pop(profile_id)
                default = config.get('default_voice_profile_id')
                proposed_profiles = profiles
                proposed_default = default if default in profiles else next(iter(profiles))
                companion_service.save_config({'voice_profiles': profiles,
                    'default_voice_profile_id': proposed_default})
                voice_asset_service.forget_profile(profile_id)
        except BaseException:
            try:
                if proposed_profiles is not None:
                    current = companion_service.load_config()
                    if current.get('voice_profiles') == proposed_profiles and current.get('default_voice_profile_id') == proposed_default:
                        companion_service.save_config({'voice_profiles': config['voice_profiles'],
                            'default_voice_profile_id': config.get('default_voice_profile_id')})
            finally:
                for original, temporary in reversed(staged):
                    if temporary.exists():
                        temporary.rename(original)
            raise
        remaining, removed = [], 0
        for original, temporary in staged:
            size = temporary.stat().st_size
            try:
                temporary.unlink(); removed += size
            except OSError:
                # Keep the original recoverable name if the filesystem refuses cleanup.
                try:
                    temporary.rename(original); remaining.append(str(original))
                except OSError:
                    remaining.append(str(temporary))
        return dict(ok=not remaining, id=profile_id, removed_bytes=removed, profile_removed=remove_profile,
                    remaining_files=remaining,
                    message='音色配置已处理，但部分文件被占用，请关闭声音后手动清理列出的文件。' if remaining else '已完成音色清理；共享文件和外部模型保留。')


def _cache_install_ids() -> set[str]:
    from . import dependency_installer
    return {item['id'] for item in dependency_installer._DEPENDENCY_DEFS if item.get('kind') == 'script'} | set(dependency_installer._running_installs)


def _cache_idle() -> None:
    from . import dependency_installer, local_vision_service
    if any(dependency_installer._install_running(dep_id) for dep_id in sorted(_cache_install_ids())):
        raise ValueError('组件仍在下载、暂停或安装中，请先取消或等待完成，再清理下载缓存。')
    if local_vision_service.passive_status().get('pulling'):
        raise ValueError('视觉模型仍在下载，请先取消下载，再清理下载缓存。')


def _cache_records() -> list[dict]:
    """Direct children only; neither HF caches nor installed model trees are candidates."""
    from . import companion_service, dependency_installer
    from .dependency_removal import _assert_safe
    sources = [dependency_installer._package_source(dep_id) for dep_id in ('genie_runtime', 'gpt_sovits')]
    names = {str(source.get('file_name') or '') for source in sources}
    names = {name for name in names if name and Path(name).name == name and '/' not in name and '\\' not in name and name.endswith('.zip')}
    choices = [(settings.voice_training_dir.absolute(), settings.voice_training_dir.absolute()/'downloads', names),
               (settings.local_vision_dir.absolute(), settings.local_vision_dir.absolute(), {'ollama-windows-amd64.zip'})]
    config = companion_service.load_config()
    references = {_canonical(profile[field]) for profile in (config.get('voice_profiles') or {}).values()
                  for field in VOICE_FIELDS if profile.get(field)}
    records = []
    for root, directory, package_names in choices:
        _plain(directory)
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise ValueError('下载缓存位置不是普通目录，请先检查安装位置。')
        patterns = [re.compile(re.escape(name) + r'(?:\.part|\.source-[0-9a-f]{16}\.part)?\Z') for name in package_names]
        for path in sorted(directory.iterdir()):
            if not any(pattern.fullmatch(path.name) for pattern in patterns):
                continue
            _assert_safe(root, path); _plain(path)
            if not path.is_file() or _canonical(path) in references:
                continue
            info = path.stat()
            if info.st_nlink > 1:
                continue
            records.append(dict(path=str(path), size=info.st_size, mtime=info.st_mtime_ns,
                                ctime=info.st_ctime_ns, inode=info.st_ino))
    return records


def cache_preview() -> dict:
    _cache_idle()
    records = _cache_records()
    result = dict(paths=[row['path'] for row in records], size_bytes=sum(row['size'] for row in records),
        impact='仅清理 Mio 专用目录中的固定下载包和下载断点；之后重新安装或继续下载时可能需要重新获取。',
        preserved='已安装模型、运行环境、HF 缓存、个人文件、被音色引用或硬链接共用的文件及外部安装包保留。')
    result['token'] = _token(dict(**result, records=records))
    return result


def cache_clear(token: str) -> dict:
    from . import dependency_installer
    with _LOCK, ExitStack() as stack:
        # Installer launch takes these same cross-process groups. Hold all of them
        # until cleanup finishes, then check receipts again to exclude active jobs.
        groups = {'genie_runtime' if item in {'genie_runtime','gpt_sovits','whisper'} else item for item in _cache_install_ids()}
        for group in sorted(groups):
            stack.enter_context(dependency_installer.dependency_operation(group))
        plan = cache_preview()
        if not token or token != plan['token']:
            raise ValueError('下载缓存文件或引用已变化，请重新预览清理范围。')
        staged = []
        try:
            for raw in plan['paths']:
                original = Path(raw); _plain(original)
                temporary = original.with_name('.mio-cache-removal-' + uuid.uuid4().hex)
                original.rename(temporary); staged.append((original, temporary))
        except BaseException:
            for original, temporary in reversed(staged):
                temporary.rename(original)
            raise
        removed, remaining = 0, []
        for original, temporary in staged:
            size = temporary.stat().st_size
            try:
                temporary.unlink(); removed += size
            except OSError:
                try:
                    temporary.rename(original); remaining.append(str(original))
                except OSError:
                    remaining.append(str(temporary))
        return dict(ok=not remaining, removed_bytes=removed, remaining_files=remaining,
                    message='部分下载缓存被占用，已保留列出的文件，请关闭相关程序后重试。' if remaining else '下载缓存已清理，已安装模型与环境保留。')
