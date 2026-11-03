"""Owned Anif installation jobs; activation is separate from download/build.

The worker runs outside the frozen app. Only its job tree and managed install
are removable; imported checkpoint/reference originals are never deleted.
"""
from __future__ import annotations

import atexit
import copy
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid
import wave

from .config import settings
from . import local_voice_engines, model_management_service as resources
from .probe_process import ProbeProcess

_LOCK = threading.RLock()
_OWNER = '.mio-anif-owner.json'
_OWNER_VALUE = {'schema': 1, 'owner': 'mio-aniflive-managed'}
_TERMINAL = {'ready', 'failed', 'cancelled', 'interrupted'}
_CHILD = None
_CHILD_JOB: Path | None = None
_LEASE = None
_TRANSACTION_LEASE = None
_RUN_GUARD = None
_CLOSED = False
_PROFILE_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_JOB_ID = re.compile(r'^[a-f0-9]{32}$')


class _RootLease:
    """Process-wide file lease outside the removable install directory."""
    def __init__(self, root: Path, kind='operation', *, create=True):
        self.root = root
        self.file = None
        path = root.parent / f'.mio-anif-{kind}.lock'
        resources._plain(path)
        if not create and not path.exists():
            return
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open('a+b' if create else 'r+b')
        try:
            stream.seek(0)
            if not stream.read(1):
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.file = stream
        except BaseException:
            stream.close()
            raise

    def close(self):
        if self.file is not None:
            self.file.close()  # The OS also releases the lease on process exit.
            self.file = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _foreign_busy(root: Path) -> bool:
    for kind, owned in (('operation', _TRANSACTION_LEASE), ('run', _RUN_GUARD)):
        if owned is not None and owned.root == root:
            continue
        try:
            with _RootLease(root, kind, create=False):
                pass
        except OSError:
            return True
    return False


def _release_lease():
    global _LEASE
    if _LEASE is not None:
        _LEASE.close()
        _LEASE = None


def _mutation(kind):
    """Serialize backend processes without making the child take our lease.

    Bootstrap owns the distinct run lease throughout environment setup and the
    worker. It must never acquire the operation lease held by its parent.
    """
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            global _LEASE, _TRANSACTION_LEASE, _RUN_GUARD
            from contextlib import ExitStack
            with resources._LOCK, ExitStack() as stack:
                if kind != 'start':
                    stack.enter_context(local_voice_engines.manager.lifecycle_lock)
                stack.enter_context(_LOCK)
                if busy():
                    raise ValueError('Anif 正在安装或操作，请等待完成；其他 Mio 窗口的任务需在原窗口取消。')
                root = _root()
                lease = None
                try:
                    try:
                        lease = _RootLease(root)
                        _TRANSACTION_LEASE = lease
                        # A stale child's bootstrap can outlive a backend crash
                        # briefly; exclude it before reading or deleting files.
                        _RUN_GUARD = _RootLease(root, 'run')
                    except OSError as exc:
                        raise ValueError('另一个 Mio 正在准备 Anif，请等待完成后重试。') from exc
                    if kind == 'start':
                        # Handoff only the run lease. The operation lease stays
                        # owned by this backend until its entire child tree exits.
                        _RUN_GUARD.close()
                        _RUN_GUARD = None
                    result = function(*args, **kwargs)
                    if kind == 'start' and _CHILD is not None:
                        _LEASE = lease
                        lease = None
                    return result
                finally:
                    _TRANSACTION_LEASE = None
                    if _RUN_GUARD is not None:
                        _RUN_GUARD.close()
                        _RUN_GUARD = None
                    if lease is not None:
                        lease.close()
        return invoke
    return decorate


def _root() -> Path:
    return Path(settings.voice_training_dir).absolute() / 'aniflive-managed'


def _read(path: Path, default=None):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else ({} if default is None else default)
    except (OSError, ValueError):
        return {} if default is None else default


def _atomic(path: Path, value) -> None:
    resources._plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _owned(root: Path, create: bool = False) -> bool:
    resources._plain(root)
    if (root / _OWNER).is_file():
        if _read(root / _OWNER) != _OWNER_VALUE:
            raise ValueError('Anif 安装目录的归属标记无效，请使用空的模型目录。')
        return True
    if root.exists() and any(root.iterdir()):
        raise ValueError('Anif 安装目录中存在未登记文件，不能覆盖或删除；请选择其他模型目录。')
    if create:
        _atomic(root / _OWNER, _OWNER_VALUE)
        return True
    return False


def _profiles(config: dict) -> list[dict]:
    rows = []
    for identifier, profile in (config.get('voice_profiles') or {}).items():
        if not isinstance(profile, dict):
            continue
        reason = ''
        if not _PROFILE_ID.fullmatch(str(identifier)):
            reason = '音色标识不兼容，请重新导入音色。'
        for key, extensions in (
            ('gpt_sovits_gpt_weights', {'.ckpt'}),
            ('gpt_sovits_sovits_weights', {'.pth'}),
            ('gpt_sovits_ref_audio', {'.wav', '.mp3', '.flac', '.m4a', '.ogg', '.aac', '.wma'}),
        ):
            path = Path(str(profile.get(key) or ''))
            if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in extensions:
                reason = reason or '需要配套 V2 的 GPT/SoVITS 权重和参考音频；请先到声音设置导入。'
        text = profile.get('gpt_sovits_prompt_text')
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            reason = reason or '请在声音设置填写参考音频的准确原文。'
        if profile.get('gpt_sovits_prompt_language', 'ja') not in {'zh', 'ja', 'en', 'all_zh', 'all_ja'}:
            reason = reason or '参考音频语言需为中文、日语或英语。'
        rows.append(dict(id=identifier, name=profile.get('name') or identifier,
                         compatible=not reason, reason=reason,
                         note='需要 GPT-SoVITS V2；版本与权重配套将在编译时检查。'))
    return rows


def _profile(identifier: str, config: dict) -> dict:
    selected = next((row for row in _profiles(config) if row['id'] == identifier), None)
    if not selected or not selected['compatible']:
        raise ValueError(selected['reason'] if selected else '音色不存在，请重新选择。')
    source = config['voice_profiles'][identifier]
    language = {'all_zh': 'zh', 'all_ja': 'ja'}.get(source.get('gpt_sovits_prompt_language'),
                                                         source.get('gpt_sovits_prompt_language') or 'ja')
    result = dict(id=identifier, name=selected['name'], gpt=str(Path(source['gpt_sovits_gpt_weights']).resolve()),
                  sovits=str(Path(source['gpt_sovits_sovits_weights']).resolve()),
                  reference=str(Path(source['gpt_sovits_ref_audio']).resolve()),
                  reference_text=source['gpt_sovits_prompt_text'].strip(), reference_language=language)
    for key in ('gpt', 'sovits', 'reference'):
        resources._plain(Path(result[key]))
    result['weight_sha256'] = {key: resources._digest(Path(result[key])) for key in ('gpt', 'sovits')}
    result['reference_sha256'] = resources._digest(Path(result['reference']))
    return result


def _checks(root: Path) -> tuple[list[dict], dict]:
    hardware = resources.hardware()
    available = hardware.get('ram_available_bytes')
    try:
        free = shutil.disk_usage(resources._nearest_existing(root)).free
    except OSError:
        free = 0
    gpu = hardware.get('gpus') or []
    checks = [
        dict(id='system', label='Windows 安装环境', ok=os.name == 'nt',
             message='目前支持 Windows 64 位和 NVIDIA 显卡。'),
        dict(id='gpu', label='NVIDIA 显卡', ok=bool(gpu),
             message='已检测到 NVIDIA 显卡。' if gpu else '请先安装 NVIDIA 显卡驱动，再重新检查。'),
        dict(id='vram', label='可用显存', ok=any(item.get('free_bytes', 0) >= 384 * 1024**2 for item in gpu),
             message='至少保留 384 MiB 可用显存；实际编译可能需要更多，可先停止其他本地模型。'),
        dict(id='ram', label='可用内存', ok=available is not None and available >= 512 * 1024**2,
             message='至少保留 512 MiB 可用内存；编译时会持续检查资源。'),
        dict(id='disk', label='磁盘空间', ok=free >= 16 * 1024**3,
             message='首次安装建议至少 16 GiB 可用空间；运行时保留 8 GiB。'),
    ]
    return checks, hardware


def _latest_job(root: Path) -> Path | None:
    identifier = _read(root / 'latest-job.json').get('job_id', '')
    return root / 'jobs' / identifier if isinstance(identifier, str) and _JOB_ID.fullmatch(identifier) else None


def _process_status() -> None:
    global _CHILD, _CHILD_JOB
    if _CHILD is None:
        return
    if _CHILD.poll() is None:
        return
    job = _CHILD_JOB
    _CHILD.close()
    _CHILD = None
    _CHILD_JOB = None
    if job:
        state = _read(job / 'status.json')
        if state.get('stage') not in _TERMINAL:
            _atomic(job / 'status.json', dict(state, stage='failed', ready=False,
                message='安装进程已退出，请检查网络、驱动和磁盘空间后重试。'))
    _release_lease()


def busy() -> bool:
    # Resource operations may already own the voice lifecycle lock. Never wait
    # here for an installation/activation thread that needs that same lock.
    # A contended setup transaction is conservatively considered busy.
    if not _LOCK.acquire(blocking=False):
        return True
    try:
        _process_status()
        return _CHILD is not None or _foreign_busy(_root())
    finally:
        _LOCK.release()


def _state(root: Path) -> tuple[dict | None, Path | None]:
    _process_status()
    job = _latest_job(root)
    if job is None:
        return None, None
    resources._plain(job)
    value = _read(job / 'status.json')
    identifier = job.name
    if not value:
        value = dict(stage='interrupted', message='上次安装未完成，可重新安装继续。')
    owned = _CHILD is not None and _CHILD_JOB == job
    foreign = not owned and _foreign_busy(root)
    stage = value.get('stage') or 'interrupted'
    running = owned or (foreign and stage not in _TERMINAL)
    if not running and stage not in _TERMINAL:
        stage = 'interrupted'
    result = dict(id=identifier, job_id=identifier, stage=stage,
                  message=str(value.get('message') or ''), percent=max(0, min(100, float(value.get('percent') or 0))),
                  running=running, can_cancel=owned, ready=stage == 'ready' and bool(value.get('ready')),
                  error=str(value.get('error') or '') if stage == 'failed' else '', preview_urls={})
    for field in ('downloaded_bytes', 'total_bytes'):
        count = value.get(field)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            result[field] = count
    profile = _read(job / 'job.json').get('profile') or {}
    result.update(voice_id=profile.get('id'), voice_name=profile.get('name') or profile.get('id'))
    if result['ready']:
        for language in ('zh', 'ja'):
            if (job / f'preview-{language}.wav').is_file():
                result['preview_urls'][language] = f'/api/model-management/anif/setup/audio/{language}?job_id={identifier}'
    return result, job


def status() -> dict:
    from . import companion_service
    with _LOCK:
        root = _root()
        _owned(root)
        config = companion_service.load_config()
        checks, hardware = _checks(root)
        rows = _profiles(config)
        job, _ = _state(root)
        transaction_busy = busy()
        installation = _read(root / 'installed.json') or None
        installed_entry = installation.get('registration') if installation else None
        current = _manifest().get('engines', {}).get('aniflive')
        registered = bool(installed_entry and current == installed_entry)
        selected = bool(registered and config.get('voice_engine') == 'aniflive'
                        and config.get('aniflive_voice_profile') == installation.get('voice_id')
                        and config.get('aniflive_model') == 'mio-v2-aniflive'
                        and str(config.get('aniflive_url') or '').rstrip('/') == installed_entry.get('url'))
        blockers = [item['message'] for item in checks if not item['ok']]
        if not any(row['compatible'] for row in rows):
            blockers.append('请先在声音设置导入一套 V2 音色。')
        if transaction_busy:
            blockers.append('正在准备 Anif，请等待完成或取消后重试。')
        return dict(voices=rows, checks=checks, hardware=hardware, root=str(root), job=job,
                    installed=dict(voice_id=installation.get('voice_id'), job_id=installation.get('job_id'), active=selected,
                                   registered=registered, experimental=True) if installation else None,
                    selected=selected, can_start=not blockers, blockers=blockers,
                    can_activate=bool(job and job['ready'] and not transaction_busy),
                    can_uninstall=root.exists() and (root / _OWNER).is_file() and not transaction_busy,
                    message='安装、编译完成并试听后，点击使用才切换当前声音。')


def _manifest() -> dict:
    path = local_voice_engines.manifest_path()
    if not path.exists():
        return {'schema': 1, 'engines': {}}
    resources._plain(path)
    payload = _read(path)
    if payload.get('schema') != 1 or not isinstance(payload.get('engines'), dict):
        raise ValueError('本机语音注册清单无效，请先修复；不会覆盖原有配置。')
    return payload


@_mutation('start')
def start(voice_id: str) -> dict:
    global _CHILD, _CHILD_JOB, _CLOSED
    from . import companion_service
    # Serialize the choice of storage root with model migration/removal.
    with resources._LOCK, _LOCK:
        if busy():
            raise ValueError('Anif 正在安装，请等待完成或先取消。')
        resources._idle('voice')
        root = _root()
        _owned(root)
        checks, _ = _checks(root)
        failures = [row['message'] for row in checks if not row['ok']]
        if failures:
            raise ValueError(' '.join(failures))
        config = companion_service.load_config()
        profile = _profile(voice_id, config)
        scripts = Path(settings.agent_control_scripts_dir).resolve()
        bootstrap = scripts / 'deps' / 'install-aniflive-runtime.ps1'
        worker = scripts / 'aniflive_setup_worker.py'
        if not bootstrap.is_file() or not worker.is_file():
            raise ValueError('此版本缺少 Anif 安装组件，请更新完整的 Mio 程序。')
        powershell = shutil.which('powershell.exe')
        if not powershell:
            raise ValueError('未找到 Windows PowerShell，暂时无法启动安装。')
        _owned(root, create=True)
        identifier = uuid.uuid4().hex
        job = root / 'jobs' / identifier
        job.mkdir(parents=True)
        payload = dict(schema=1, job_id=identifier, root=str(root), job_dir=str(job),
                       state_path=str(job / 'status.json'), cancel_path=str(job / 'cancel.request'),
                       scripts_dir=str(scripts), profile=profile,
                       weight_sha256=profile['weight_sha256'], reference_sha256=profile['reference_sha256'])
        _atomic(job / 'job.json', payload)
        _atomic(job / 'status.json', dict(schema=1, job_id=identifier, stage='starting',
                    message='正在准备独立 Anif 环境；首次安装需要下载并编译。', percent=0, ready=False))
        _atomic(root / 'latest-job.json', {'job_id': identifier})
        try:
            _CHILD = ProbeProcess([powershell, '-NoLogo', '-NoProfile', '-NonInteractive',
                                  '-ExecutionPolicy', 'Bypass', '-File', str(bootstrap), '-JobFile', str(job / 'job.json')], cwd=str(scripts))
            _CHILD_JOB = job
            _CLOSED = False
        except BaseException:
            _atomic(job / 'status.json', dict(stage='failed', message='无法启动 Anif 安装，请检查程序权限后重试。', ready=False))
            raise
        return dict(ok=True, job_id=identifier, job=dict(id=identifier, job_id=identifier,
                    running=True, can_cancel=True, stage='starting', percent=0, voice_id=profile['id'], voice_name=profile['name'],
                    message='正在准备独立 Anif 环境。', ready=False, preview_urls={}),
                    message='Anif 安装已开始；可以收起此页，稍后回来查看。')


def cancel() -> dict:
    global _CHILD, _CHILD_JOB
    with _LOCK:
        _process_status()
        if _CHILD is None:
            if _foreign_busy(_root()):
                raise ValueError('另一个 Mio 正在准备 Anif，请回到启动安装的窗口取消。')
            return dict(ok=True, message='当前没有正在运行的 Anif 安装。')
        job = _CHILD_JOB
        (job / 'cancel.request').write_text('cancel', encoding='ascii')
        _CHILD.close()
        _CHILD = None
        _CHILD_JOB = None
        state = _read(job / 'status.json')
        _atomic(job / 'status.json', dict(state, stage='cancelled', ready=False,
                    message='已取消安装；已校验的下载文件保留，重新安装时可复用。'))
        _release_lease()
        return dict(ok=True, message='已取消 Anif 安装；原来的声音设置未改变。')


def _registration(root: Path, job: Path, state: dict) -> dict:
    registration = state.get('registration')
    if not isinstance(registration, dict):
        raise ValueError('安装缺少运行注册信息，请重新安装。')
    registration = copy.deepcopy(registration)
    for key, directory in (('python', False), ('script', False), ('cwd', True)):
        path = Path(str(registration.get(key) or ''))
        resources._plain(path)
        if not path.is_absolute() or not path.resolve().is_relative_to(root.resolve()) or not (path.is_dir() if directory else path.is_file()):
            raise ValueError('安装运行文件缺失或超出受管范围，请重新安装。')
    args = registration.get('args')
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError('Anif 运行参数无效。')
    for flag in ('--source', '--engines', '--bert', '--gpt', '--sovits', '--reference'):
        if args.count(flag) != 1 or args.index(flag) + 1 >= len(args):
            raise ValueError('Anif 运行参数缺失。')
        path = Path(args[args.index(flag) + 1])
        resources._plain(path)
        if not path.is_absolute() or not path.resolve().is_relative_to(root.resolve()) or not path.exists():
            raise ValueError('安装模型文件缺失或超出受管范围，请重新安装。')
    if registration.get('url') != 'http://127.0.0.1:9882':
        raise ValueError('Anif 安装启动地址无效。')
    environment = registration.get('env', {})
    if not isinstance(environment, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
        raise ValueError('Anif 安装环境无效。')
    registration.update(model_version='v2', experimental=True, numerical_verified=False, managed_install_id=job.name)
    return registration


def _artifact(record: dict, base: Path, root: Path) -> Path:
    if not isinstance(record, dict) or not isinstance(record.get('path'), str):
        raise ValueError('Anif 引擎清单缺少文件。')
    raw = Path(record['path'])
    path = raw if raw.is_absolute() else base / raw
    resources._plain(path)
    digest = record.get('sha256')
    if (not isinstance(digest, str) or not re.fullmatch('[a-fA-F0-9]{64}', digest)
            or not path.resolve().is_relative_to(root.resolve()) or not path.is_file()
            or resources._digest(path) != digest.lower()):
        raise ValueError('Anif 引擎文件已变化或不在受管目录，请重新编译验收。')
    return path


def _validate_acceptance(root: Path, job: Path, registration: dict, profile: dict) -> None:
    acceptance_path = job / 'acceptance.json'
    resources._plain(acceptance_path)
    acceptance = _read(acceptance_path)
    expected = profile.get('weight_sha256')
    if (acceptance.get('schema') != 1 or acceptance.get('job_id') != job.name
            or acceptance.get('ok') is not True or acceptance.get('weight_sha256') != expected
            or not isinstance(expected, dict) or set(expected) != {'gpt', 'sovits'}):
        raise ValueError('缺少当前音色的中日语音验收记录，请重新安装验收。')
    for language in ('zh', 'ja'):
        path = audio_path(language, job.name)
        result = (acceptance.get('audio') or {}).get(language)
        if not isinstance(result, dict) or result.get('sha256') != resources._digest(path):
            raise ValueError('试听音频与验收记录不符，请重新生成验收。')
        try:
            with wave.open(str(path), 'rb') as stream:
                frames = stream.getnframes()
                valid = (stream.getnchannels() == 1 and stream.getsampwidth() == 2
                         and stream.getframerate() == 32000 and 1600 <= frames <= 32000 * 180
                         and len(stream.readframes(frames)) == frames * 2)
        except (OSError, EOFError, wave.Error) as exc:
            raise ValueError('试听音频内容无效，请重新生成验收。') from exc
        if not valid:
            raise ValueError('试听音频不符合 32kHz 单声道验收要求。')
    args = registration['args']
    for flag, expected_value in (('--voice-profile', profile.get('id')), ('--reference-text', profile.get('reference_text')),
                                 ('--reference-language', profile.get('reference_language'))):
        if args.count(flag) != 1 or args.index(flag) + 1 >= len(args) or args[args.index(flag) + 1] != expected_value:
            raise ValueError('Anif 启动音色与已验收的参考配置不符。')
    engine_dir = Path(args[args.index('--engines') + 1])
    manifest_path = engine_dir / 'v2-manifest.json'
    resources._plain(manifest_path)
    manifest = _read(manifest_path)
    if manifest.get('kind') != 'mio-aniflive-v2-experimental' or manifest.get('model_family') != 'GPT-SoVITS V2':
        raise ValueError('不是受支持的 GPT-SoVITS V2 Anif 引擎。')
    weights = manifest.get('source_weights')
    if not isinstance(weights, dict) or set(weights) != {'gpt', 'sovits'}:
        raise ValueError('Anif 清单缺少双权重来源。')
    for name in ('gpt', 'sovits'):
        if not isinstance(weights[name], dict) or weights[name].get('sha256') != expected.get(name):
            raise ValueError('Anif 编译权重与所选音色不符。')
        path = _artifact(weights[name], engine_dir, root)
        registered_weight = Path(args[args.index('--' + name) + 1])
        if path.resolve() != registered_weight.resolve():
            raise ValueError('Anif 注册权重与编译来源不符。')
    stages = manifest.get('stages')
    required = {'ssl', 'bert', 'vq_encoder', 'gpt_encoder', 'gpt_step', 'spectrogram', 'sovits', 'sovits_stream'}
    if not isinstance(stages, dict) or set(stages) != required:
        raise ValueError('Anif 安装必须包含完整的八个 V2 阶段。')
    for record in stages.values():
        _artifact(record, engine_dir, root)
    config_path = _artifact({'path': 'config.json', 'sha256': manifest.get('config_sha256')}, engine_dir, root)
    config = _read(config_path)
    model, data = config.get('model') or {}, config.get('data') or {}
    if (not isinstance(model, dict) or not isinstance(data, dict)
            or model.get('version') != 'v2' or model.get('semantic_frame_rate') != '25hz'
            or data.get('sampling_rate') != 32000 or data.get('hop_length') != 640):
        raise ValueError('Anif 模型配置与 V2 32kHz/25Hz 不匹配。')
    reference = Path(args[args.index('--reference') + 1])
    if resources._digest(reference) != profile.get('reference_sha256'):
        raise ValueError('Anif 参考音频已变化，请重新生成验收。')


@_mutation('activate')
def activate() -> dict:
    from . import companion_service
    with resources._LOCK, local_voice_engines.manager.lifecycle_lock, _LOCK:
        if busy():
            raise ValueError('Anif 尚在安装，请等待完成。')
        resources._idle('voice')
        root = _root()
        _owned(root)
        job_info, job = _state(root)
        if not job_info or not job_info['ready']:
            raise ValueError('请先完成编译和中日试听验收，再使用 Anif。')
        for language in ('zh', 'ja'):
            audio_path(language, job.name)
        state = _read(job / 'status.json')
        registration = _registration(root, job, state)
        profile = _read(job / 'job.json').get('profile') or {}
        identifier = profile.get('id')
        if not isinstance(identifier, str) or not _PROFILE_ID.fullmatch(identifier):
            raise ValueError('安装音色标识无效。')
        _validate_acceptance(root, job, registration, profile)
        config = companion_service.load_config()
        original = _manifest()
        payload = copy.deepcopy(original)
        prior = _read(root / 'installed.json')
        previous_entry = prior.get('previous_entry') if prior else original['engines'].get('aniflive')
        if prior and original['engines'].get('aniflive') != prior.get('registration'):
            previous_entry = original['engines'].get('aniflive')
        payload['engines']['aniflive'] = registration
        changes = dict(voice_engine='aniflive', aniflive_model='mio-v2-aniflive',
                       aniflive_voice_profile=identifier, aniflive_url=registration['url'])
        previous_config = {key: config.get(key) for key in changes}
        replacing_own_registration = bool(prior and original['engines'].get('aniflive') == prior.get('registration'))
        companion_service.cancel_speech_playback()
        local_voice_engines.manager.stop('aniflive')
        try:
            _atomic(local_voice_engines.manifest_path(), payload)
            companion_service.save_config(changes)
            _atomic(root / 'installed.json', dict(schema=1, job_id=job.name, voice_id=identifier,
                registration=registration, previous_entry=previous_entry,
                previous_config=prior.get('previous_config', previous_config) if replacing_own_registration else previous_config))
        except BaseException:
            _atomic(local_voice_engines.manifest_path(), original)
            companion_service.save_config(previous_config)
            raise
        return dict(ok=True, selected=True, message='已使用这个 Anif 音色；下一次播放将按需启动，原生 V2 回退保留。')


def audio_path(language: str, job_id: str) -> Path:
    with _LOCK:
        if language not in {'zh', 'ja'} or not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise ValueError('试听音频不存在。')
        root = _root()
        _owned(root)
        job = root / 'jobs' / job_id
        resources._plain(job)
        state = _read(job / 'status.json')
        if state.get('stage') != 'ready' or not state.get('ready'):
            raise ValueError('请等待中日语音验收完成后试听。')
        path = job / f'preview-{language}.wav'
        resources._plain(path)
        if not path.is_file() or path.stat().st_size < 44:
            raise ValueError('试听音频尚未生成，请重新安装验收。')
        with path.open('rb') as stream:
            header = stream.read(12)
        if header[:4] != b'RIFF' or header[8:12] != b'WAVE':
            raise ValueError('试听音频格式无效，请重新安装验收。')
        return path


def uninstall_preview() -> dict:
    with _LOCK:
        if busy():
            raise ValueError('请先取消正在进行的 Anif 安装。')
        root = _root()
        if not _owned(root):
            raise ValueError('没有可卸载的 Mio 管理 Anif 安装。')
        records = resources._tree(root)
        from . import companion_service
        config = companion_service.load_config()
        impact = '删除本功能下载的 Anif 环境、编译产物与试听；正在使用时会恢复安装前的声音选择。'
        preserved = '个人原始权重、参考音频、训练素材、外部 Anif 安装与其他语音引擎保留。'
        identity = dict(records=records, manifest=_manifest(), selected={key: config.get(key) for key in
            ('voice_engine', 'aniflive_model', 'aniflive_voice_profile', 'aniflive_url')})
        return dict(token=resources._token(identity), paths=[str(root)], size_bytes=sum(row['size'] for row in records),
                    impact=impact, preserved=preserved)


@_mutation('uninstall')
def uninstall(token: str) -> dict:
    from . import companion_service
    with resources._LOCK, local_voice_engines.manager.lifecycle_lock, _LOCK:
        resources._idle('voice')
        plan = uninstall_preview()
        if not token or token != plan['token']:
            raise ValueError('Anif 文件或配置已变化，请重新检查卸载范围。')
        root = _root()
        installed = _read(root / 'installed.json')
        current = _manifest()
        config = companion_service.load_config()
        entry = current['engines'].get('aniflive')
        is_registered = bool(installed and entry == installed.get('registration'))
        selected = is_registered and config.get('voice_engine') == 'aniflive'
        companion_service.cancel_speech_playback()
        local_voice_engines.manager.stop('aniflive')
        updated = copy.deepcopy(current)
        if is_registered:
            if installed.get('previous_entry'):
                updated['engines']['aniflive'] = installed['previous_entry']
            else:
                updated['engines'].pop('aniflive', None)
        before_config = {key: config.get(key) for key in ('voice_engine', 'aniflive_model', 'aniflive_voice_profile', 'aniflive_url')}
        restore = installed.get('previous_config') or {}
        if restore.get('voice_engine') == 'aniflive' and not updated['engines'].get('aniflive'):
            restore = dict(restore, voice_engine='gpt_sovits')
        staged = root.with_name('.mio-anif-removal-' + uuid.uuid4().hex)
        resources._plain(staged)
        root.rename(staged)
        try:
            if selected:
                companion_service.save_config(restore)
            if is_registered:
                _atomic(local_voice_engines.manifest_path(), updated)
        except BaseException:
            staged.rename(root)
            _atomic(local_voice_engines.manifest_path(), current)
            if selected:
                companion_service.save_config(before_config)
            raise
        # Once deletion starts, do not re-register a partly deleted runtime.
        # On a file lock, put the remainder back with its ownership marker so
        # a later uninstall can safely retry, while keeping the fallback active.
        try:
            shutil.rmtree(staged)
        except OSError:
            staged.rename(root)
            _atomic(root / _OWNER, _OWNER_VALUE)
            remaining = sum(row['size'] for row in resources._tree(root))
            return dict(ok=False, removed_bytes=max(0, plan['size_bytes'] - remaining),
                        remaining_files=[str(root)],
                        message='Anif 已停用，但部分文件被占用；请关闭相关程序后再次卸载。个人原始音色保留。')
        return dict(ok=True, removed_bytes=plan['size_bytes'], message='已卸载 Mio 管理的 Anif；个人原始音色文件保留。')


def shutdown() -> None:
    global _CLOSED
    with _LOCK:
        if _CLOSED:
            return
        _CLOSED = True
        if _CHILD is not None:
            cancel()
        _release_lease()


atexit.register(shutdown)
