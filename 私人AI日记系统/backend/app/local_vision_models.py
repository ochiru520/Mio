"""Per-model lifecycle for Mio's private Ollama library; never touch system Ollama."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from PIL import Image, ImageDraw

from .ollama_model_files import manifest_complete

RECOMMENDED_MODEL = 'qwen3.5:2b'
LEGACY_MODEL = 'qwen2.5vl:3b'
STATE_FILENAME = 'model-selection.json'
MODELS = (
    dict(id=RECOMMENDED_MODEL, name='Qwen3.5-2B', recommended=True, legacy=False,
         sources=[dict(label='模型说明与下载来源', url='https://ollama.com/library/qwen3.5:2b')]),
    dict(id=LEGACY_MODEL, name='Qwen2.5-VL 3B', recommended=False, legacy=True,
         sources=[dict(label='模型说明与下载来源', url='https://ollama.com/library/qwen2.5vl:3b')]),
)


def _service():
    from . import local_vision_service
    return local_vision_service


def _root() -> Path:
    return Path(_service().settings.local_vision_dir).absolute()


def _plain(path: Path) -> None:
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('视觉模型目录包含链接或目录联接，请先检查安装位置。')


def _model(model_id: str) -> str:
    if model_id not in {item['id'] for item in MODELS}:
        raise ValueError('请选择列表中支持的本地视觉模型。')
    return model_id


def manifest_path(model_id: str, root: Path | None = None) -> Path:
    model_id = _model(model_id)
    name, tag = model_id.split(':')
    return (root or _root()) / 'models' / 'manifests' / 'registry.ollama.ai' / 'library' / name / tag


def _read_state() -> dict:
    path = _root() / STATE_FILENAME
    _plain(path)
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(state, dict):
            raise ValueError
        if not isinstance(state.get('verified', {}), dict):
            raise ValueError
        selected = state.get('selected_model')
        if selected is not None:
            _model(selected)
        return state
    except (ValueError, TypeError) as exc:
        raise ValueError('本地视觉模型选择记录无效，已保留原文件；请修复后重试，不会自动改用其他模型。') from exc


def _write_state(state: dict) -> None:
    root = _root(); _plain(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / STATE_FILENAME; _plain(target)
    temporary = root / ('.model-selection-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def effective_model(root: Path | None = None) -> str:
    """Read-only migration: keep an installed legacy model until explicitly switched."""
    if root is not None and Path(root).absolute() != _root():
        path = Path(root) / STATE_FILENAME
        _plain(path)
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding='utf-8-sig'))
                if state.get('selected_model') is not None:
                    return _model(state['selected_model'])
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError('视觉模型选择记录无效，未自动切换。') from exc
        return LEGACY_MODEL if manifest_complete(manifest_path(LEGACY_MODEL, Path(root)), Path(root) / 'models') else RECOMMENDED_MODEL
    state = _read_state()
    if state.get('selected_model') is not None:
        return state['selected_model']
    return LEGACY_MODEL if manifest_complete(manifest_path(LEGACY_MODEL), _root() / 'models') else RECOMMENDED_MODEL


def pin_current() -> str:
    """Persist the upgrade decision before a download can change installed inventory."""
    with _service()._lifecycle_lock:
        state = _read_state()
        model_id = effective_model()
        if not state.get('selected_model'):
            _write_state(dict(state, schema_version=1, selected_model=model_id,
                              selection_origin='upgrade_preserved' if model_id == LEGACY_MODEL else 'recommended_default'))
        return model_id


def selection_identity() -> tuple[str, str]:
    state = _read_state()
    return effective_model(), str(state.get('selected_revision') or '')


def _assets(manifest: Path) -> list[Path]:
    _plain(manifest)
    try:
        data = json.loads(manifest.read_text(encoding='utf-8-sig'))
        rows = [data['config'], *data['layers']]
        if not data['layers']:
            raise ValueError
        digests = {row['digest'] for row in rows}
        if not all(isinstance(value, str) and re.fullmatch(r'sha256:[a-fA-F0-9]{64}', value) for value in digests):
            raise ValueError
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('模型清单无效，不能安全判断共享文件；未删除任何模型。') from exc
    paths = [_root() / 'models' / 'blobs' / digest.replace(':', '-') for digest in sorted(digests)]
    for path in paths:
        _plain(path)
    return paths


def _records(paths: list[Path]) -> list[dict]:
    records = []
    for path in paths:
        _plain(path)
        if path.exists():
            info = path.stat()
            if not path.is_file():
                raise ValueError('模型文件类型异常，请重新检查。')
            records.append(dict(path=str(path), size=info.st_size, mtime_ns=info.st_mtime_ns,
                                inode=info.st_ino, links=info.st_nlink))
    return records


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _fingerprint(model_id: str) -> str:
    manifest = manifest_path(model_id)
    if not manifest_complete(manifest, _root() / 'models'):
        return ''
    return _digest(dict(manifest=manifest.read_text(encoding='utf-8-sig'), records=_records([manifest, *_assets(manifest)])))


def _idle() -> None:
    service = _service()
    service.assert_model_idle()
    if service._pull_process is not None and service._pull_process.poll() is None:
        raise ValueError('视觉模型仍在下载，请先取消或等待完成。')
    from . import dependency_installer
    if dependency_installer._install_running('ollama_vision'):
        raise ValueError('视觉运行环境正在安装，请等待完成。')


@contextmanager
def _operation():
    from . import dependency_installer
    with dependency_installer.dependency_operation('ollama_vision'), _service()._lifecycle_lock:
        _idle()
        yield


def _stored_verification(model_id: str, state: dict) -> dict:
    record = (state.get('verified') or {}).get(model_id, {})
    if isinstance(record, dict) and record.get('fingerprint') and record['fingerprint'] == _fingerprint(model_id):
        return {key: record.get(key) for key in ('ready', 'state', 'error', 'checked_at')}
    return dict(ready=False, state='unverified', error='', checked_at=None)


def download_status() -> dict:
    service = _service()
    process = service._pull_process
    running = process is not None and process.poll() is None
    exit_code = process.poll() if process is not None and not running else None
    error = f'模型下载未完成（退出码 {exit_code}），可重试续传。' if exit_code not in (None, 0) else ''
    if process is not None and exit_code == 0 and service._pull_model in {row['id'] for row in MODELS}:
        if not manifest_complete(manifest_path(service._pull_model), _root() / 'models'):
            error = '下载进程已结束，但模型文件仍不完整，请重试续传。'
    return dict(active=running, running=running, model_id=service._pull_model,
                percent=None, can_cancel=running, error=error,
                message='正在下载，可取消后重试续传。' if running else error or '没有正在下载的模型。')


def inventory() -> dict:
    service = _service(); state = _read_state(); current = effective_model()
    models = []
    for definition in MODELS:
        model_id = definition['id']; manifest = manifest_path(model_id)
        installed = manifest_complete(manifest, _root() / 'models')
        verification = _stored_verification(model_id, state) if installed else dict(ready=False, state='missing', error='', checked_at=None)
        try:
            records = _records([manifest, *_assets(manifest)]) if manifest.exists() else []
            size = sum(item['size'] for item in records)
            shared = set()
            for other in (_root() / 'models' / 'manifests').rglob('*'):
                _plain(other)
                if other.is_file() and other != manifest:
                    shared.update(_assets(other))
            shared_size = sum(item['size'] for item in records if Path(item['path']) in shared)
        except (OSError, ValueError):
            size, shared_size = None, None
        models.append(dict(definition, label=definition['name'], installed=installed, selected=model_id == current,
                           verified=bool(verification.get('ready')), verification=verification,
                           status='verified' if verification.get('ready') else 'installed' if installed else 'incomplete' if manifest.exists() else 'missing',
                           size_bytes=size, shared_bytes=shared_size, can_remove=manifest.exists() and model_id != current))
    message = '旧视觉模型已保留；可下载、验证推荐模型后自行切换，旧模型不会自动删除。' if current == LEGACY_MODEL else '升级保留已有选择；下载其他模型不会自动切换，也不会删除旧模型。'
    return dict(models=models, selected_model=current, selected_model_id=current,
                recommended_model=RECOMMENDED_MODEL, recommended_model_id=RECOMMENDED_MODEL,
                migration_notice=message, migration_message=message,
                runtime_installed=service.installation_status()['runtime_installed'],
                download=download_status())


def start_download(model_id: str) -> dict:
    model_id = _model(model_id)
    with _operation():
        if not _service().installation_status()['runtime_installed']:
            raise ValueError('请先安装本地视觉运行环境，再下载所选模型。')
        pin_current()
        if not manifest_complete(manifest_path(model_id), _root() / 'models'):
            _service()._start_model_pull(model_id)
    return inventory()


def cancel_download() -> dict:
    from . import dependency_installer
    service = _service()
    with dependency_installer.dependency_operation('ollama_vision'), service._lifecycle_lock:
        if dependency_installer._install_running('ollama_vision'):
            raise ValueError('视觉运行环境正在安装，请在安装任务中取消。')
        service.cancel_model_pull()
    return inventory()


def _verify_locked(model_id: str) -> dict:
    service = _service(); pin_current()
    before = _fingerprint(model_id)
    if not before:
        raise ValueError('请先完整下载这个模型，再验证或切换。')
    service.start_server()
    # A real image request is required; a text-only response does not prove vision capability.
    sample = Image.new('RGB', (96, 96), 'white')
    ImageDraw.Draw(sample).rectangle((24, 24, 72, 72), fill='blue')
    encoded = BytesIO(); sample.save(encoded, format='PNG')
    image = base64.b64encode(encoded.getvalue()).decode('ascii')
    checked = time.time()
    try:
        result = service._request_json('/api/chat', method='POST', timeout=120,
            payload=dict(model=model_id, stream=False, think=False, keep_alive='90s',
                         messages=[dict(role='user', content='Describe this image briefly.', images=[image])],
                         options=dict(num_ctx=2048, num_predict=64, temperature=0)))
        content = str((result.get('message') or {}).get('content') or '').strip()
        ready = bool(content) and result.get('done') is True and result.get('done_reason') != 'length' and not result.get('error')
        record = dict(ready=ready, state='ready' if ready else 'failed',
                      error='' if ready else '图片推理未返回完整可见内容。', checked_at=checked)
    except Exception as exc:
        record = dict(ready=False, state='failed', error=f'图片推理失败（{type(exc).__name__}），请检查资源与本地诊断。', checked_at=checked)
    if before != _fingerprint(model_id):
        raise ValueError('验证期间模型文件已变化，请重新验证；原选择保留。')
    state = _read_state(); verified = dict(state.get('verified') or {})
    verified[model_id] = dict(record, fingerprint=before)
    _write_state(dict(state, verified=verified))
    service.clear_model_probe()
    return record


def verify(model_id: str) -> dict:
    model_id = _model(model_id)
    with _operation():
        record = _verify_locked(model_id)
    return dict(ok=record['ready'], model_id=model_id, verification=record,
                message='图片推理检查通过，可试听使用效果后切换。' if record['ready'] else record['error'], catalog=inventory())


def select(model_id: str) -> dict:
    model_id = _model(model_id)
    with _operation():
        pin_current(); state = _read_state()
        if not _stored_verification(model_id, state).get('ready'):
            raise ValueError('请先对这个模型执行图片推理验证；验证通过后才能切换，原选择已保留。')
        service = _service(); service.unload_model()
        _write_state(dict(state, selected_model=model_id, selected_revision=uuid.uuid4().hex, selection_origin='user'))
        service.clear_model_probe()
    return dict(ok=True, model_id=model_id, message='已切换本地视觉模型，其他模型保留在磁盘。', catalog=inventory())


def removal_preview(model_id: str) -> dict:
    model_id = _model(model_id)
    with _service()._lifecycle_lock:
        _idle()
        if model_id == effective_model():
            raise ValueError('这是当前使用的视觉模型，请先切换到其他已验证模型再删除。')
        root = _root(); _plain(root)
        if root.resolve() in {Path(root.anchor).resolve(), Path.home().resolve(), (Path.home() / '.ollama').resolve()}:
            raise ValueError('当前模型目录不是独立的 Mio 模型库，不能删除。')
        manifest = manifest_path(model_id)
        if not manifest.is_file():
            raise ValueError('这个模型没有可删除的安装清单。')
        assets = set(_assets(manifest)); shared = set()
        manifests = []
        for other in (root / 'models' / 'manifests').rglob('*'):
            _plain(other)
            if other.is_file():
                manifests.append(other)
                if other != manifest:
                    shared.update(_assets(other))
        # Hardlinked assets can be referenced outside this library; preserve them.
        shared.update(path for path in assets if path.exists() and path.stat().st_nlink > 1)
        deletable = [manifest, *sorted(assets - shared)]
        records = _records(deletable)
        plan = dict(model_id=model_id, paths=[row['path'] for row in records],
                    shared_files=[str(path) for path in sorted(assets & shared)],
                    size_bytes=sum(row['size'] for row in records),
                    impact='只删除所选视觉模型及其独占文件，保留运行环境和其他模型。',
                    preserved='当前选择、共享模型文件、个人音色与系统 Ollama 全部保留。')
        plan['token'] = _digest(dict(plan=plan, records=records, manifests=_records(manifests), state=_read_state()))
        return plan


def remove(model_id: str, token: str) -> dict:
    model_id = _model(model_id)
    with _operation():
        plan = removal_preview(model_id)
        if not token or plan['token'] != token:
            raise ValueError('模型文件、共享引用或选择已变化，请重新预览删除范围。')
        # Stop the owned server before offline manipulation; its GC cannot race our plan.
        _service().stop_server()
        if removal_preview(model_id)['token'] != token:
            raise ValueError('停止服务期间模型文件已变化，未删除；请重新预览。')
        staged = []
        try:
            for raw in plan['paths']:
                path = Path(raw); _plain(path)
                temporary = path.with_name('.mio-model-removal-' + uuid.uuid4().hex)
                path.rename(temporary); staged.append((path, temporary))
            # Finish the fallible state write before deleting any staged bytes.
            # If it fails, both files and prior verification remain recoverable.
            state = _read_state(); verified = dict(state.get('verified') or {}); verified.pop(model_id, None)
            _write_state(dict(state, verified=verified))
        except BaseException:
            for original, temporary in reversed(staged):
                temporary.rename(original)
            raise
        removed_bytes = 0; remaining = []
        manifest = manifest_path(model_id)
        # Keep the manifest until its exclusive assets are gone. A locked blob
        # must leave an incomplete model that the same UI can preview and retry,
        # rather than an unreachable orphan after its manifest disappeared.
        ordered = sorted(staged, key=lambda pair: pair[0] == manifest)
        for original, temporary in ordered:
            if original == manifest and remaining:
                try:
                    temporary.rename(original); remaining.append(str(original))
                except OSError:
                    remaining.append(str(temporary))
                continue
            size = temporary.stat().st_size
            try:
                temporary.unlink(); removed_bytes += size
            except OSError:
                try:
                    temporary.rename(original); remaining.append(str(original))
                except OSError:
                    remaining.append(str(temporary))
        _service().clear_model_probe()
    return dict(ok=not remaining, model_id=model_id, removed_bytes=removed_bytes, remaining_files=remaining,
                message='已删除所选模型；其他模型与音色保留。' if not remaining else '部分文件被占用，未全部删除，请检查后重试。', catalog=inventory())
