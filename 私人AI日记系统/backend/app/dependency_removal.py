"""Remove only the fixed optional assets managed by Mio, after a reviewed plan."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
from contextlib import ExitStack
from pathlib import Path

from .config import settings

SUPPORTED = {'genie_runtime', 'gpt_sovits', 'whisper', 'ollama_vision'}
IMPACTS = {
    'genie_runtime': '停止本地语音并删除 Genie 引擎。已安装的角色音色保留，重新安装引擎后可继续使用。',
    'gpt_sovits': '停止本地语音并删除 Mio 默认音色模型。个人训练素材、参考音频、其他音色和 Genie 引擎保留。',
    'whisper': '停止声音识别并删除 Mio 目录内的 Whisper 模型缓存。共用 Python 环境和其他软件的模型保留。',
    'ollama_vision': '停止 Mio 本地视觉，删除其独立运行器、模型库和下载缓存；系统 Ollama 不受影响。',
}


def _targets(dep_id: str) -> tuple[Path, list[Path]]:
    voice = settings.voice_training_dir.absolute()
    vision = settings.local_vision_dir.absolute()
    choices = {
        'genie_runtime': (voice, ['.genie-env', 'GenieData']),
        'gpt_sovits': (voice, ['models/genie/mio-v1', '.mio-native-voice-complete']),
        'whisper': (voice, ['cache/faster-whisper']),
        'ollama_vision': (vision, ['Ollama', 'models', 'ollama-windows-amd64.zip']),
    }
    if dep_id not in choices:
        raise ValueError('此项目不支持模型卸载。')
    root, names = choices[dep_id]
    if dep_id == 'whisper':
        # The detector also supports bundled model directories, not just hub cache.
        names += [str(path.relative_to(root)) for path in sorted((root/'models').glob('faster-whisper-*'))]
    # Installer-owned downloads only; never accept paths supplied by the browser.
    if dep_id in {'genie_runtime', 'gpt_sovits'}:
        from .dependency_installer import _package_source
        name = str(_package_source(dep_id).get('file_name') or '')
        if name and Path(name).name == name and '/' not in name and '\\' not in name and name.endswith('.zip'):
            names += ['downloads/' + name, 'downloads/' + name + '.part']
    return root, [root / name for name in names]


def _assert_safe(root: Path, target: Path) -> None:
    # Check lexical and resolved boundaries and every ancestor, including junctions.
    resolved_root = root.resolve()
    reserved = {Path(root.anchor).resolve(), Path.home().resolve(), (Path.home()/'.ollama').resolve(),
                settings.workspace_root.resolve(), settings.data_dir.resolve(), settings.runtime_config_path.parent.resolve()}
    if resolved_root in reserved:
        raise ValueError('组件目录不是独立模型目录，已停止卸载；请检查存储位置。')
    if target == root or not target.is_relative_to(root) or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('卸载路径超出组件目录，已停止。')
    for path in (target, *target.parents):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('组件目录包含符号链接或目录联接，请先手动处理，未执行卸载。')
    for protected in (settings.data_dir, settings.db_path, settings.diary_dir,
                      settings.companion_config_path, settings.runtime_config_path):
        if Path(protected).resolve().is_relative_to(target.resolve()):
            raise ValueError('卸载范围包含个人数据，已停止。')


def _inventory(root: Path, targets: list[Path]) -> tuple[str, int, list[str]]:
    digest = hashlib.sha256()
    size = 0
    present = []
    for target in targets:
        _assert_safe(root, target)
        digest.update(str(target).encode())
        if not target.exists():
            continue
        present.append(str(target))
        paths = [target]
        if target.is_dir():
            def fail(error):
                raise error
            for directory, dirs, files in os.walk(target, followlinks=False, onerror=fail):
                for name in sorted(dirs + files):
                    path = Path(directory) / name
                    info = path.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise ValueError('组件内部包含链接，已停止卸载以保护外部文件。')
                    paths.append(path)
        for path in sorted(paths):
            info = path.stat()
            digest.update(f'{path}|{info.st_size}|{info.st_mtime_ns}'.encode())
            if path.is_file():
                size += info.st_size
    return digest.hexdigest(), size, present


def availability(dep_id: str) -> dict:
    if dep_id not in SUPPORTED:
        return {'can_uninstall': False}
    root, targets = _targets(dep_id)
    try:
        for target in targets:
            _assert_safe(root, target)
        return {'can_uninstall': any(p.exists() for p in targets), 'uninstall_impact': IMPACTS[dep_id]}
    except (OSError, ValueError) as exc:
        return {'can_uninstall': False, 'uninstall_blocker': str(exc)}


def preview(dep_id: str) -> dict:
    root, targets = _targets(dep_id)
    token, size, paths = _inventory(root, targets)
    return {'id': dep_id, 'token': token, 'paths': paths, 'size_bytes': size, 'impact': IMPACTS[dep_id],
            'preserved': '聊天、日记、记忆、API 配置和个人训练素材保留。卸载后可重新安装。'}


def uninstall(dep_id: str, token: str) -> dict:
    from . import dependency_installer as installer, dependency_probe, environment_check_service
    from . import genie_tts_service, local_vision_service, system_audio_service
    with installer.dependency_operation(dep_id), ExitStack() as stack:
        from .call_session_service import manager
        if dep_id in {'genie_runtime', 'gpt_sovits', 'whisper'} and manager.status().get('active'):
            raise ValueError('通话正在使用语音能力，请结束通话后再卸载。')
        group = ('ollama_vision',) if dep_id == 'ollama_vision' else ('genie_runtime', 'gpt_sovits', 'whisper')
        if any(installer._install_running(item) for item in group):
            raise ValueError('相关组件正在安装，请等待完成后再卸载。')
        root, targets = _targets(dep_id)
        # Freeze managed workers while deleting their files; stop does not switch to cloud.
        if dep_id in {'genie_runtime', 'gpt_sovits'}:
            if not genie_tts_service._worker_lock.acquire(blocking=False):
                raise ValueError('本地语音正在使用，请播放结束后再卸载。')
            stack.callback(genie_tts_service._worker_lock.release)
        elif dep_id == 'ollama_vision':
            if not local_vision_service._lifecycle_lock.acquire(blocking=False):
                raise ValueError('本地视觉正在操作，请稍后再卸载。')
            stack.callback(local_vision_service._lifecycle_lock.release)
        elif dep_id == 'whisper':
            if not system_audio_service._lock.acquire(blocking=False):
                raise ValueError('声音识别正在启动，请稍后再卸载。')
            stack.callback(system_audio_service._lock.release)
        plan = preview(dep_id)
        if not token or token != plan['token']:
            raise ValueError('组件文件已变化，请重新查看卸载范围并确认。')
        if dep_id in {'genie_runtime', 'gpt_sovits'}:
            genie_tts_service.stop_worker()
        elif dep_id == 'whisper':
            system_audio_service.stop()
        else:
            local_vision_service.stop_server()
        try:
            # Revalidate the entire plan after stopping processes and before any deletion.
            _inventory(root, targets)
            for target in targets:
                _assert_safe(root, target)
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            installer._write_status(dep_id, {'id': dep_id, 'stage': 'uninstalled', 'done': True,
                'finalized': True, 'error': '', 'message': '已卸载，可重新安装。'})
        except OSError as exc:
            raise OSError('卸载未全部完成，文件可能被占用；请关闭相关功能后重新检查并重试。') from exc
        finally:
            dependency_probe.invalidate()
            environment_check_service.refresh_detection_cache()
        return {'id': dep_id, 'uninstalled': True, 'removed_bytes': plan['size_bytes'],
                'message': '已卸载，个人数据和配置已保留；需要时可重新安装。'}
