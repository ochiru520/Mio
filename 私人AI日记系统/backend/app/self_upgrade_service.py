"""Persistent source-to-candidate coordinator for the existing Agent, no model calls.

Working copies isolate file edits, not OS permissions. Native terminals and build
scripts run under the current account and are explicitly reported as unsandboxed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

from .config import settings
from . import self_upgrade_preview_worker  # Static import keeps the trusted runner in frozen builds.

_lock = threading.RLock()
_processes: dict[str, subprocess.Popen] = {}
_threads: dict[str, threading.Thread] = {}
_cancel: dict[str, threading.Event] = {}
_installer = None
_process_jobs = {}
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'recovered'}
EXCLUDED = {'.git', '.svn', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache',
            '.desktop-cache', 'build', 'dist', 'release', 'generated', '数据', '运行数据', '备份',
            'logs', 'log', 'models', '模型', 'Mio开发', 'uploads', '.ssh', '.aws', '.azure'}
PRIVATE = {'.env', 'credentials', 'credentials.json', 'secrets.json', 'id_rsa', 'id_ed25519',
           'runtime_settings.json', 'model_profiles.json', 'companion_config.json', 'mio_profile.json'}
IDENTITY_EXCLUDES = {'.desktop-cache', '.git', '.pytest_cache', '.venv', '__pycache__',
                     'build', 'dist', 'node_modules', 'release', 'generated'}


def _state_root() -> Path:
    root = settings.data_dir.resolve() / 'self-upgrade'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _atomic(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text('utf-8'))
    except FileNotFoundError:
        return default


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _plain(path: Path):
    from .code_workspace_service import is_link
    for part in (path, *path.parents):
        if is_link(part):
            raise ValueError('自身改造目录不能经过链接或 junction。')
    return path.resolve()


def installed_build():
    from .runtime_identity import runtime_identity
    identity = runtime_identity()
    manifest = _read(Path(identity['manifest_path'])) if identity.get('manifest_path') else None
    return {'build_id': identity['build_id'], 'source_mode': identity['source_mode'],
            'sources': (manifest or {}).get('sources', {}), 'program_root': identity['program_root']}


def configuration():
    path = _state_root() / 'configuration.json'
    saved = _read(path)
    if saved is not None:
        return saved
    default = {'revision': 0, 'backend_root': '', 'desktop_root': '',
               'allow_auto_apply': False, 'development_root': ''}
    # Local release identity supplies convenience bindings, never execution or
    # automatic install authorization. A copied release with missing/mismatched
    # source keeps the explicit unconfigured state.
    from .runtime_identity import runtime_identity
    identity = runtime_identity()
    manifest = _read(Path(identity['manifest_path']), {}) if identity.get('manifest_path') else {}
    roots = manifest.get('source_paths', {})
    if roots.get('backend') and roots.get('desktop'):
        proposed = dict(default, backend_root=roots['backend'], desktop_root=roots['desktop'])
        try:
            backend, desktop = _plain(Path(roots['backend'])), _plain(Path(roots['desktop']))
            if (backend / 'backend/app/main.py').is_file() and (desktop / 'desktop/build_manifest.py').is_file() and _source_identity(proposed) == manifest.get('sources'):
                proposed.update(revision=1, development_root=str(desktop.parent / 'Mio开发'),
                                python_executable=str(backend / 'backend/.venv/Scripts/python.exe'))
                with _lock:
                    if not path.exists():
                        _atomic(path, proposed)
                    return _read(path)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
    return default


def configure(*, backend_root: str, desktop_root: str, expected_revision: int,
              allow_auto_apply: bool = False, development_root: str = '', python_executable: str = ''):
    """User-only API. No model tool exposes this operation or its authorization flag."""
    if type(allow_auto_apply) is not bool:
        raise ValueError('自动本地应用必须为明确的布尔设置。')
    backend, desktop = _plain(Path(backend_root)), _plain(Path(desktop_root))
    if not (backend / 'backend/app/main.py').is_file() or not all((desktop / name).is_file() for name in
            ('package-lock.json', 'desktop/build_manifest.py', '构建Windows应用.ps1')):
        raise ValueError('需要完整后端/桌面源码、依赖锁定及真实构建入口。')
    if backend == desktop or backend.is_relative_to(desktop) or desktop.is_relative_to(backend):
        raise ValueError('前后端源码必须是两个独立目录。')
    dev = _plain(Path(development_root) if development_root else desktop.parent / 'Mio开发')
    if dev == dev.parent or any(dev == root or dev.is_relative_to(root) or root.is_relative_to(dev) for root in (backend, desktop)):
        raise ValueError('开发副本目录必须独立于原源码。')
    from .agent_file_service import _private_path
    if _private_path(dev, grant=True):
        raise ValueError('开发源码不能位于私人数据目录。')
    with _lock:
        current = configuration()
        if current['revision'] != expected_revision:
            raise ValueError('源码设置已在另一窗口改变，请刷新。')
        if _active_job():
            raise ValueError('已有自身改造任务，先停止再修改绑定。')
        value = {'revision': expected_revision + 1, 'backend_root': str(backend), 'desktop_root': str(desktop),
                 'development_root': str(dev), 'allow_auto_apply': allow_auto_apply,
                 'python_executable': python_executable or str(backend / 'backend/.venv/Scripts/python.exe')}
        _atomic(_state_root() / 'configuration.json', value)
        return value


def _job_path(job_id):
    if not isinstance(job_id, str) or not job_id.startswith('upgrade_') or len(job_id) != 40 or not all(c in '0123456789abcdef' for c in job_id[8:]):
        raise ValueError('无效改造任务身份。')
    return _state_root() / 'jobs' / (job_id + '.json')


def _save(job, **changes):
    with _lock:
        job.update(changes, revision=job.get('revision', 0) + 1, updated_at=time.time())
        _atomic(_job_path(job['id']), job)
    return job


def get(job_id):
    job = _read(_job_path(job_id))
    if not job:
        raise ValueError('改造任务不存在。')
    if job['status'] in {'validating', 'building', 'previewing', 'awaiting_idle'} and job_id not in _threads:
        _save(job, status='interrupted', error='上次应用中断；记录和开发副本保留，请重新启动验证。')
        _release_lock(job)
    preview_process = _processes.get(job_id)
    if job['status'] == 'preview_ready' and (not preview_process or getattr(preview_process, 'poll', lambda: None)() is not None):
        _save(job, status='built', preview=None, error='预览进程已经退出；应用前请重新启动候选预览。')
    if job['status'] == 'installing':
        # Independent updater owns installation/recovery; never infer success from disappearance.
        state = settings.data_dir.resolve().parent
        from .runtime_identity import runtime_identity
        state = Path(runtime_identity().get('state_root') or state)
        result = _read(state / 'updates/last-result.json', {})
        if result.get('target_build_id') == (job.get('candidate') or {}).get('build_id'):
            status = result.get('state')
            if status in {'completed', 'recovered', 'failed', 'recovery_failed'}:
                _save(job, status=status, installation=result)
                _release_lock(job)
                if status == 'completed' and not job.get('rollback'):
                    # The installed candidate's own source becomes the next source baseline.
                    # Original dirty worktrees remain untouched and available to the user.
                    config = configuration()
                    if config['revision'] == job['configuration_revision']:
                        config.update(backend_root=str(Path(job['work_root']) / '私人AI日记系统'),
                                      desktop_root=str(Path(job['work_root']) / '澪Agent应用'), revision=config['revision'] + 1)
                        _atomic(_state_root() / 'configuration.json', config)
                elif status == 'completed' and job.get('rollback'):
                    config = configuration()
                    previous_config = job.get('rollback_source_binding')
                    if config['revision'] == job['configuration_revision'] and previous_config:
                        config.update(previous_config, revision=config['revision'] + 1)
                        _atomic(_state_root() / 'configuration.json', config)
    return job


def _jobs():
    paths = (_state_root() / 'jobs').glob('upgrade_*.json')
    return sorted((get(path.stem) for path in paths), key=lambda item: item['created_at'], reverse=True)


def _active_job():
    return next((job for job in _jobs() if job['status'] not in TERMINAL), None)


def status():
    jobs = _jobs()
    rollback_info = _rollback_info()
    return {'configuration': configuration(), 'installed_build': installed_build(),
            'active_job': next((job for job in jobs if job['status'] not in TERMINAL), None), 'jobs': jobs[:20],
            'installer_available': _installer is not None,
            'rollback_available': rollback_info['available'], 'rollback_reason': rollback_info['reason'],
            'execution_environment': '本机执行；开发副本不是 OS 沙箱，终端/构建不能保证仅访问该目录'}


def _rollback_info():
    from .runtime_identity import runtime_identity
    state = Path(runtime_identity().get('state_root') or settings.data_dir.parent)
    result = _read(state / 'updates/last-result.json', {})
    if result.get('state') != 'completed' or not result.get('job'):
        return {'available': False, 'reason': '没有成功安装的本地升级回退快照。'}
    saved = _plain(Path(result['job']) / 'previous-program')
    if not saved.is_relative_to(state / 'updates/jobs'):
        return {'available': False, 'reason': '回退快照路径无效。'}
    previous = _read(saved / '构建清单.json', {})
    current = _read(Path(installed_build()['program_root']) / '构建清单.json', {})
    if not previous or previous.get('local_upgrade_protocol') != 1:
        return {'available': False, 'reason': '旧版本不支持本地候选验核，不能绕过旧版签名验证强行回退。'}
    if not current.get('data_schema_compatibility') or current['data_schema_compatibility'] != previous.get('data_schema_compatibility'):
        return {'available': False, 'reason': '前后版本的数据结构兼容身份不同；为保留当前新数据，不能自动回退。'}
    return {'available': _installer is not None, 'reason': '' if _installer else '独立更新器未连接。', 'root': str(saved), 'build': previous}


def rollback(*, confirmed=False):
    """Prepare a previous compatible program. Current user data is never restored."""
    if confirmed is not True:
        raise ValueError('回退前需要用户明确确认。')
    with _lock:
        info = _rollback_info()
        if not info['available']:
            raise ValueError(info['reason'])
        if _active_job():
            raise ValueError('先停止当前改造/预览任务，再准备回退。')
        config = configuration()
        if not config['development_root']:
            raise ValueError('请先配置开发目录。')
        job_id = 'upgrade_' + uuid.uuid4().hex
        root = _plain(Path(config['development_root']) / job_id)
        source = root / 'source'; source.mkdir(parents=True)
        candidate = root / 'candidate'; candidate.mkdir()
        helper = _desktop_module('helper')
        for name in ('Mio.exe', 'MioUpdater.exe', '_internal', '构建清单.json'):
            entry = Path(info['root']) / name
            if entry.exists():
                helper.copy_managed_entry(entry, candidate / name)
        job = {'id': job_id, 'status': 'built', 'rollback': True, 'goal': '恢复上一兼容程序版本，保留当前用户数据',
               'acceptance': ['旧程序隔离启动', '程序身份与回退快照一致', '保留当前数据库和新数据'],
               'created_at': time.time(), 'revision': 0, 'work_root': str(source), 'baseline': _source_identity(config),
               'base_build_id': installed_build()['build_id'], 'configuration_revision': config['revision'],
               'conversation_id': '', 'task_id': '', 'checks': [{'name': 'data_schema_compatibility', 'ok': True,
                  'compatibility': info['build']['data_schema_compatibility']}],
               'candidate': {'root': str(candidate), 'build_id': info['build']['build_id'], 'sources': info['build']['sources']},
               'preview': None, 'needs_confirmation': True, 'error': ''}
        previous_job = next((item for item in _jobs() if (item.get('candidate') or {}).get('build_id') == info['build']['build_id'] and not item.get('rollback')), None)
        if previous_job:
            job['rollback_source_binding'] = {'backend_root': str(Path(previous_job['work_root']) / '私人AI日记系统'),
                                              'desktop_root': str(Path(previous_job['work_root']) / '澪Agent应用')}
        with (_state_root() / 'development.lock').open('x', encoding='utf-8') as stream:
            json.dump({'job_id': job_id, 'pid': os.getpid()}, stream)
        _save(job)
    return preview(job_id)


def attach_installer(function):
    global _installer
    _installer = function


def _source_identity(config):
    # Same byte algorithm as build_manifest.repository_state, held by the runtime.
    # Never import/run a model-edited build script inside the trusted coordinator.
    def repository(root):
        digest = hashlib.sha256()
        if (root / '.git').exists():
            def git(*arguments):
                return subprocess.run(['git', '-C', str(root), *arguments], check=True,
                                      capture_output=True, timeout=30).stdout
            commit = git('rev-parse', 'HEAD').decode('ascii').strip()
            status = git('status', '--porcelain=v1', '-z')
            digest.update(status); digest.update(git('diff', '--binary', 'HEAD', '--'))
            for raw in filter(None, git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0')):
                path = root / raw.decode('utf-8', errors='surrogateescape')
                digest.update(raw)
                if path.is_file():
                    digest.update(bytes.fromhex(_hash(path)))
            return {'commit': commit, 'dirty': bool(status), 'dirty_hash': digest.hexdigest().upper() if status else ''}
        for folder, dirs, files in os.walk(root, topdown=True, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in IDENTITY_EXCLUDES)
            for name in sorted(files):
                path = _plain(Path(folder) / name)
                digest.update(path.relative_to(root).as_posix().encode('utf-8'))
                digest.update(b'\0'); digest.update(bytes.fromhex(_hash(path)))
        value = digest.hexdigest().upper()
        return {'commit': 'source-' + value[:12].lower(), 'dirty': False, 'dirty_hash': value}
    return {name: repository(Path(config[name + '_root'])) for name in ('backend', 'desktop')}


def _allowed(relative: Path) -> bool:
    for part in relative.parts:
        name = part.lower().rstrip(' .')
        if (name in {item.lower() for item in EXCLUDED | PRIVATE} or name.startswith('.env')
                or name.endswith(('.pem', '.key', '.pfx', '.p12', '.db', '.sqlite', '.sqlite3', '.log', '.dpapi'))
                or 'signing-key' in name):
            return False
    return True


def snapshot(source: Path, target: Path) -> dict:
    """Current working files, not git checkout: keeps pre-existing uncommitted edits."""
    records, total = [], 0
    for folder, dirs, files in os.walk(source, topdown=True, followlinks=False):
        current = Path(folder)
        dirs[:] = sorted(name for name in dirs if _allowed((current / name).relative_to(source)))
        for name in dirs + files:
            if _allowed((current / name).relative_to(source)):
                _plain(current / name)
        for name in sorted(files):
            path = current / name; relative = path.relative_to(source)
            if not _allowed(relative):
                continue
            size = path.stat().st_size
            total += size
            if size > 32 * 1024**2 or total > 1024**3 or len(records) >= 50000:
                raise ValueError('源码副本包含大资源；请移出生成/依赖数据后再创建任务。')
            before = _hash(path)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            if _hash(destination) != before or _hash(path) != before:
                raise ValueError('创建副本时源码改变，请停止并重试。')
            records.append({'path': relative.as_posix(), 'size': size, 'sha256': before})
    digest = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    return {'sha256': digest, 'files': records, 'total_bytes': total}


def _release_lock(job):
    path = _state_root() / 'development.lock'
    with _lock:
        if (_read(path, {}) or {}).get('job_id') == job['id']:
            path.unlink(missing_ok=True)


def create(*, goal: str, acceptance: list[str], expected_revision: int, conversation_id: str = '', task_id: str = ''):
    from . import workspace_service as workspace, code_workspace_service as code
    if not goal.strip() or not acceptance or any(not item.strip() for item in acceptance):
        raise ValueError('改造需要用户目标和可验收条件。')
    with _lock:
        config = configuration()
        if not config['backend_root'] or config['revision'] != expected_revision:
            raise ValueError('请先配置版本匹配的完整源码，并刷新设置版本。')
        if _active_job():
            raise ValueError('已有自身改造任务；同一源码和正式版本只能串行改造。')
        if not conversation_id:
            from . import db
            conversation_id = 'desktop_agent_self_upgrade_' + uuid.uuid4().hex
            db.create_agent_conversation(conversation_id)
        identity = _source_identity(config); installed = installed_build()
        if not installed['source_mode'] and identity != installed['sources']:
            raise ValueError('绑定源码与当前安装版本不匹配；请使用该版本的完整源码与未提交内容。')
        job_id = 'upgrade_' + uuid.uuid4().hex
        lock_path = _state_root() / 'development.lock'
        if lock_path.exists():
            owner = _read(lock_path, {})
            previous = _read(_job_path(owner.get('job_id', ''))) if owner.get('job_id') else None
            if previous and previous.get('status') not in TERMINAL:
                raise ValueError('源码开发锁尚未释放。')
            lock_path.unlink()
        with lock_path.open('x', encoding='utf-8') as stream:
            json.dump({'job_id': job_id, 'pid': os.getpid(), 'baseline': identity}, stream)
        root = _plain(Path(config['development_root']) / job_id)
        source = root / 'source'
        job = {'id': job_id, 'status': 'developing', 'goal': goal, 'acceptance': acceptance,
               'created_at': time.time(), 'revision': 0, 'work_root': str(source),
               'baseline': identity, 'base_build_id': installed['build_id'], 'configuration_revision': config['revision'],
               'conversation_id': conversation_id, 'task_id': task_id, 'checks': [], 'candidate': None, 'preview': None,
               'needs_confirmation': not config['allow_auto_apply'], 'error': '', 'execution_environment': '本机执行，无 OS 沙箱'}
        try:
            root.mkdir(parents=True)
            snapshots = {}
            for name, folder in [('backend', '私人AI日记系统'), ('desktop', '澪Agent应用')]:
                snapshots[name] = snapshot(Path(config[name + '_root']), source / folder)
            if _source_identity(config) != identity:
                raise ValueError('创建开发副本期间原源码改变；未绑定不一致的基线。')
            _atomic(root / 'baseline.json', {'sources': identity, 'snapshots': snapshots})
            project = workspace.save_project('改造 Mio：' + goal[:60], instructions='仅修改当前开发副本；不包含私人数据或签名私钥。验收：' + '；'.join(acceptance), directory=str(source))
            code.configure(project['id'], str(source), read_enabled=True, write_enabled=True, execute_enabled=False)
            workspace.bind_conversation(project['id'], conversation_id)
            _save(job, project_id=project['id'], snapshots={key: {field: value for field, value in record.items() if field != 'files'} for key, record in snapshots.items()})
        except BaseException:
            _release_lock(job)
            raise
        return job


def _python(config):
    python = Path(config.get('python_executable') or str(Path(config['backend_root']) / 'backend/.venv/Scripts/python.exe'))
    if not python.is_file():
        if getattr(sys, 'frozen', False):
            raise ValueError('缺少源码 Python 编译环境；安装版 EXE 不能冒充开发解释器。')
        python = Path(sys.executable)
    return str(python)


def _environment(job):
    # Providers, dotenv and publisher secrets are never inherited by candidate processes.
    safe = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
            'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE'}
    env = {key: value for key, value in os.environ.items() if key.upper() in safe}
    synthetic = Path(job['work_root']).parent / 'synthetic-runtime'
    synthetic.mkdir(exist_ok=True)
    env.update(MIO_RUNTIME_ROOT=str(synthetic), MIO_DISABLE_DOTENV='1', PYTHONUTF8='1',
               MIO_BACKEND_PROJECT_ROOT=str(Path(job['work_root']) / '私人AI日记系统'),
               MIO_BACKEND_PYTHON=_python(configuration()))
    return env


def _terminate(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                       creationflags=0x08000000, timeout=20, check=False)
    else:
        process.terminate()
    process.wait(timeout=20)


def _own_process(process):
    """Kernel kill-on-close lifetime, so an application crash kills build descendants."""
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(key, ctypes.c_ulonglong) for key in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
    class Extended(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO), ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    info = Extended(); info.BasicLimitInformation.LimitFlags = 0x2000
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
        if handle:
            kernel.CloseHandle(handle)
        _terminate(process)
        raise ValueError('无法建立所属进程生命周期约束，未继续运行构建。')
    _process_jobs[process.pid] = (handle, kernel)


def _close_process_job(process):
    owned = _process_jobs.pop(process.pid, None)
    if owned:
        handle, kernel = owned; kernel.CloseHandle(handle)


def _command(job, label, arguments, cwd, timeout=300):
    log = Path(job['work_root']).parent / (label + '.log')
    started = time.time()
    event = _cancel.setdefault(job['id'], threading.Event())
    if event.is_set():
        raise ValueError('改造任务已停止。')
    with log.open('wb') as stream:
        process = subprocess.Popen(arguments, cwd=str(cwd), env=_environment(job), stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.STDOUT, creationflags=0x08000000 if os.name == 'nt' else 0)
        _own_process(process)
        _processes[job['id']] = process
        try:
            while process.poll() is None:
                if event.wait(0.1) or time.time() - started > timeout:
                    _terminate(process)
                    raise ValueError('任务已停止或命令超过时间预算。')
            code = process.returncode
        finally:
            _processes.pop(job['id'], None)
            _close_process_job(process)
    record = {'name': label, 'ok': code == 0, 'exit_code': code, 'duration_seconds': round(time.time() - started, 3),
              'log_path': str(log), 'output_tail': log.read_text('utf-8', errors='replace')[-5000:]}
    job['checks'].append(record); _save(job)
    if code:
        raise ValueError(label + '失败，退出码 ' + str(code) + '；实际日志已保存。')
    return record


def _start(job_id, phase, function):
    with _lock:
        job = get(job_id)
        if job_id in _threads or job['status'] == 'installing':
            raise ValueError('任务正在运行，不能重复启动。')
        if job['status'] in TERMINAL:
            if phase != 'validating' or job['status'] not in {'failed', 'interrupted', 'cancelled'}:
                raise ValueError('任务已结束；失败开发任务可修改副本后重新验证。')
            config = configuration()
            if job['configuration_revision'] != config['revision'] or job['base_build_id'] != installed_build()['build_id']:
                raise ValueError('基线或绑定设置已改变，不能恢复旧开发任务。')
            active = _active_job()
            if active and active['id'] != job_id:
                raise ValueError('另一开发任务占有源码锁。')
            lock_path = _state_root() / 'development.lock'
            with lock_path.open('x', encoding='utf-8') as stream:
                json.dump({'job_id': job_id, 'pid': os.getpid(), 'baseline': job['baseline']}, stream)
        old_preview = _processes.pop(job_id, None)
        if old_preview:
            _terminate(old_preview); _close_process_job(old_preview)
        _cancel[job_id] = threading.Event()
        def run():
            try:
                function(job)
            except Exception as exc:
                _save(job, status='cancelled' if _cancel[job_id].is_set() else 'failed', error=str(exc)[:1500])
                _release_lock(job)
            finally:
                _threads.pop(job_id, None)
        _save(job, status=phase, error='')
        thread = threading.Thread(target=run, name='mio-self-'+phase, daemon=True)
        _threads[job_id] = thread; thread.start()
        return dict(job)


def _working_hash(job):
    digest = hashlib.sha256()
    source = Path(job['work_root'])
    for folder, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(name for name in dirs if _allowed((Path(folder) / name).relative_to(source)))
        for name in sorted(files):
            path = Path(folder) / name; relative = path.relative_to(source)
            if _allowed(relative):
                _plain(path); digest.update(relative.as_posix().encode()); digest.update(bytes.fromhex(_hash(path)))
    return digest.hexdigest()


def validate(job_id, backend_tests=None):
    def run(job):
        source = Path(job['work_root']); backend = source / '私人AI日记系统/backend'; desktop = source / '澪Agent应用'
        job['checks'] = []; job['candidate'] = None; job['preview'] = None
        digest = _working_hash(job)
        _frontend_dependencies(job)
        compile_script = "from pathlib import Path; files=list(Path('app').rglob('*.py')); [compile(p.read_text('utf-8-sig'),str(p),'exec') for p in files]; print('compiled',len(files),'source modules')"
        _command(job, 'backend-syntax', [_python(configuration()), '-c', compile_script], backend)
        tests = backend_tests or ['tests/test_self_upgrade.py']
        for name in tests:
            path = Path(name)
            if path.is_absolute() or '..' in path.parts or path.parts[0] != 'tests' or path.suffix != '.py' or not (backend / path).is_file():
                raise ValueError('后端检查必须指向副本内现有 tests/*.py 文件。')
        _command(job, 'backend-targeted-tests', [_python(configuration()), '-m', 'pytest', '-q', *tests], backend, 600)
        node = shutil.which('node')
        if not node:
            raise ValueError('缺少 Node.js 构建环境。')
        _command(job, 'frontend-tests', [node, '--test', *[str(path) for path in sorted((desktop / 'src').glob('*.test.js'))]], desktop)
        if _working_hash(job) != digest:
            raise ValueError('验证期间源码改变，请重新验证。')
        _save(job, status='validated', validated_source_hash=digest)
    return _start(job_id, 'validating', run)


def _frontend_dependencies(job):
    """One clean install per package/lock identity, before tests import Vue."""
    desktop = Path(job['work_root']) / '澪Agent应用'
    lockfile, package = desktop / 'package-lock.json', desktop / 'package.json'
    if not lockfile.is_file() or not package.is_file():
        raise ValueError('前端缺少 package.json 或 package-lock.json。')
    identity = {'package_sha256': _hash(package), 'lock_sha256': _hash(lockfile)}
    record = Path(job['work_root']).parent / 'frontend-dependencies.json'
    if (desktop / 'node_modules/vue/package.json').is_file() and _read(record) == identity:
        return identity
    npm = shutil.which('npm.cmd') or shutil.which('npm')
    if not npm:
        raise ValueError('缺少 npm，不能安装副本锁定依赖。')
    arguments = ['cmd.exe', '/d', '/c', npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'] if os.name == 'nt' else [npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund']
    _command(job, 'frontend-dependencies', arguments, desktop, 900)
    if identity != {'package_sha256': _hash(package), 'lock_sha256': _hash(lockfile)}:
        raise ValueError('安装依赖期间前端锁定文件改变，请重新验证。')
    _atomic(record, identity)
    return identity


def build(job_id):
    job = get(job_id)
    if job['status'] != 'validated' or _working_hash(job) != job.get('validated_source_hash'):
        raise ValueError('先验证当前源码；后续修改会使验证失效。')
    def run(job):
        desktop = Path(job['work_root']) / '澪Agent应用'
        _frontend_dependencies(job)
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if not shell:
            raise ValueError('缺少 PowerShell 构建环境。')
        _command(job, 'desktop-build', [shell, '-NoProfile', '-File', str(desktop / '构建Windows应用.ps1'),
                 '-SkipInstaller', '-SkipDependencyInstall'], desktop, 3600)
        if _working_hash(job) != job.get('validated_source_hash'):
            raise ValueError('构建期间源码改变，候选不能应用。')
        root = desktop / 'release/Mio'; manifest = _read(root / '构建清单.json')
        if not manifest or not (root / 'Mio.exe').is_file():
            raise ValueError('完整程序构建不存在；不能把前端 dist 当作核心候选。')
        _save(job, status='built', candidate={'build_id': manifest['build_id'], 'root': str(root), 'sources': manifest['sources']})
    return _start(job_id, 'building', run)


def _desktop_module(name):
    # Installed updater modules are trusted implementation, development copies are only build inputs.
    try:
        return __import__('desktop.updates.' + name, fromlist=[name])
    except ModuleNotFoundError:
        try:
            return __import__('updates.' + name, fromlist=[name])
        except ModuleNotFoundError:
            sys.path.insert(0, str(Path(configuration()['desktop_root'])))
            return __import__('desktop.updates.' + name, fromlist=[name])


def preview(job_id):
    job = get(job_id)
    if job['status'] != 'built' or not job.get('candidate'):
        raise ValueError('请先完成核心候选构建。')
    def run(job):
        # One child, no desktop process and no background scheduler/model lifespan.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        root = Path(job['candidate']['root'])
        log = Path(job['work_root']).parent / 'preview.log'
        worker = Path(__file__).with_name('self_upgrade_preview_worker.py')
        if not worker.exists():
            worker = worker.with_suffix('.pyc')
        with log.open('wb') as stream:
            process = subprocess.Popen([_python(configuration()), str(worker), '--program-root', str(root),
                '--port', str(port), '--parent-pid', str(os.getpid())], cwd=str(root), env=_environment(job), stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT, creationflags=0x08000000 if os.name == 'nt' else 0)
        _processes[job['id']] = process
        _own_process(process)
        url = 'http://127.0.0.1:' + str(port)
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if _cancel[job['id']].wait(0.2) or process.poll() is not None:
                    raise ValueError('候选预览启动失败；日志已保存。')
                try:
                    with urlopen(url + '/health', timeout=1) as response:
                        health = json.load(response)
                    if health.get('build_id') != job['candidate']['build_id']:
                        raise ValueError('预览构建身份错误。')
                    break
                except OSError:
                    continue
            else:
                raise ValueError('候选预览未在预算内启动。')
            result = {'url': url + '/agent-app/?workspace=agent', 'verified': True, 'build_id': health['build_id'],
                      'mode': 'isolated_synthetic_read_preview', 'pid': process.pid, 'log_path': str(log),
                      'limitation': '合成空数据；模型/外部连接/后台任务与写操作关闭。仅证明模块、迁移及界面启动。'}
            module = _desktop_module('local_candidate')
            manifest = _read(root / '构建清单.json')
            local = {'kind': module.LOCAL_KIND, 'product_id': module.PRODUCT_ID, 'source_job_id': job['id'],
                     'base_build_id': job['base_build_id'], 'build_id': manifest['build_id'], 'app_version': manifest['app_version'],
                     'sources': manifest['sources'], 'baseline': job['baseline'], 'checks': job['checks'],
                     'preview': result, 'files': module.inventory(root)}
            path = Path(job['work_root']).parent / module.LOCAL_MANIFEST
            _atomic(path, local)
            candidate = dict(job['candidate'], manifest_path=str(path), manifest_sha256=_hash(path))
            module.verify_candidate(root, path, candidate['manifest_sha256'], base_build_id=job['base_build_id'])
            _save(job, status='preview_ready', preview=result, candidate=candidate)
        except Exception:
            _terminate(process); _close_process_job(process); _processes.pop(job['id'], None)
            raise
    return _start(job_id, 'previewing', run)


def stop(job_id):
    with _lock:
        job = get(job_id)
        if job['status'] == 'installing':
            raise ValueError('独立更新器已接管；不能取消文件替换，请使用中断恢复记录。')
        _cancel.setdefault(job_id, threading.Event()).set()
        process = _processes.get(job_id)
        if process:
            _terminate(process); _close_process_job(process); _processes.pop(job_id, None)
        _save(job, status='cancelled', error='用户已停止；开发副本和证据保留。')
        _release_lock(job)
        return job


def stop_task(task_id):
    """Sync adapter for the existing task cancellation/maintenance boundary."""
    stopped, errors = [], []
    for job in _jobs():
        if job.get('task_id') == task_id and job['status'] not in TERMINAL:
            try:
                stopped.append(stop(job['id'])['id'])
            except ValueError as exc:
                errors.append({'job_id': job['id'], 'error': str(exc)})
    return {'stopped': stopped, 'errors': errors}


def stop_all():
    stopped, errors = [], []
    for job in _jobs():
        if job['status'] not in TERMINAL:
            try:
                stopped.append(stop(job['id'])['id'])
            except ValueError as exc:
                errors.append({'job_id': job['id'], 'error': str(exc)})
    return {'stopped': stopped, 'errors': errors}


def apply(job_id, *, confirmed=False):
    with _lock:
        job = get(job_id); config = configuration()
        if job['status'] != 'preview_ready' or not (job.get('preview') or {}).get('verified'):
            raise ValueError('候选尚未通过真实预览启动核对。')
        if job['configuration_revision'] != config['revision']:
            raise ValueError('安装授权版本改变，请重新检查。')
        if not confirmed and not config['allow_auto_apply']:
            _save(job, needs_confirmation=True)
            return dict(job, application_requested=True, message='候选已准备好，等待用户应用并重启。')
        if _installer is None:
            raise ValueError('当前环境未连接独立更新器，不能安装核心候选。')
        from . import model_runtime
        if model_runtime.live_operations():
            _save(job, needs_confirmation=False)
            def wait_idle(job):
                deadline = time.monotonic() + 600
                while model_runtime.live_operations():
                    if _cancel[job['id']].wait(0.2):
                        raise ValueError('等待安装已停止。')
                    if time.monotonic() > deadline:
                        raise ValueError('等待所有模型操作空闲超过预算，未执行安装。')
                _install_job(job)
            result = _start(job_id, 'awaiting_idle', wait_idle)
            return dict(result, application_queued=True, pending=True, message='已排队；当前及其他模型操作结束后再交独立更新器。')
        return _install_job(job)


def _install_job(job):
    """Native preparation still rechecks live operations; this never bypasses it."""
    with _lock:
        config = configuration()
        if config['revision'] != job['configuration_revision']:
            raise ValueError('等待期间安装授权版本改变，未安装。')
        if _cancel.get(job['id']) and _cancel[job['id']].is_set():
            raise ValueError('应用任务已经停止。')
        if job.get('validated_source_hash') and _working_hash(job) != job['validated_source_hash']:
            raise ValueError('验证后开发源码改变，请重新构建。')
        candidate = job['candidate']; module = _desktop_module('local_candidate')
        module.verify_candidate(Path(candidate['root']), Path(candidate['manifest_path']),
            candidate['manifest_sha256'], base_build_id=job['base_build_id'])
        process = _processes.pop(job['id'], None)
        if process:
            _terminate(process); _close_process_job(process)
        _save(job, status='installing', needs_confirmation=False)
        try:
            result = _installer(Path(candidate['root']), Path(candidate['manifest_path']), candidate['manifest_sha256'], job['base_build_id'])
            return _save(job, installation=result)
        except Exception as exc:
            _save(job, status='failed', error=str(exc)); _release_lock(job)
            raise
