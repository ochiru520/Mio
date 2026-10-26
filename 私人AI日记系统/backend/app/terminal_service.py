"""Task-owned commands with explicit host confirmation or isolated project copies."""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .repositories import terminal_repository as repository

from . import db, workspace_service as workspace, code_workspace_service as code

_running = {}
_workers = set()
_initialized = set()


class WindowsJob:
    def __init__(self, process_handle):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', w.DWORD),
                        ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t), ('active', w.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(key, ctypes.c_uint64) for key in ('read', 'write', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Extended(); limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close(); raise OSError(ctypes.get_last_error(), '无法创建命令进程树')
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process_handle)):
            self.close(); raise OSError(ctypes.get_last_error(), '无法绑定命令进程树')
        ntdll = ctypes.WinDLL('ntdll')
        ntdll.NtResumeProcess.argtypes = [w.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        if ntdll.NtResumeProcess(int(process_handle)) != 0:
            self.close(); raise OSError('无法启动已绑定的命令进程')

    def stop(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)

    def close(self):
        if getattr(self, 'handle', None):
            self.kernel.CloseHandle(self.handle); self.handle = None


def initialize():
    code.initialize()
    with repository.unit_of_work() as conn:
        conn.ensure_jobs_table()
        identity = str(db.settings.db_path)
        if identity not in _initialized:
            conn.recover_running_jobs()
            _initialized.add(identity)


def shells():
    result = {}
    candidates = {'powershell': ['powershell.exe', 'pwsh'], 'cmd': ['cmd.exe'], 'bash': ['bash']}
    for key, values in candidates.items():
        for value in values:
            found = shutil.which(value)
            if found:
                result[key] = found; break
    if os.name == 'nt' and 'bash' not in result:
        path = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
        if path.is_file(): result['bash'] = str(path)
    return result


def _update(job_id, **values):
    with repository.unit_of_work() as store:
        store.update_job(job_id, values)


def _require(context, job_id):
    initialize()
    config = code.project_context(context)
    with repository.unit_of_work(write=False) as conn:
        row = conn.job_for_context((job_id,))
    if not row or row['project_id'] != config['project_id'] or row['conversation_id'] != context.conversation_id or row['task_id'] != context.task_id:
        raise ValueError('命令不属于当前任务。')
    return dict(row)


def read(context, job_id, offset=0, max_chars=12000, *, deliver=True):
    row = _require(context, job_id)
    result = {key: value for key, value in row.items() if key not in {'stdout_path', 'stderr_path', 'evidence_json'}}
    result['evidence'] = json.loads(row['evidence_json'])
    for stream in ('stdout', 'stderr'):
        path = Path(row[stream + '_path'])
        raw = path.read_bytes() if path.is_file() else b''
        text = raw.decode('utf-8', errors='replace')
        if '\ufffd' in text and os.name == 'nt': text = raw.decode('gb18030', errors='replace')
        result[stream] = text[offset:offset + max_chars]
        result[stream + '_total_chars'] = len(text)
        result[stream + '_sha256'] = hashlib.sha256(raw).hexdigest()
    if result['evidence'].get('files'):
        config = code.configuration(row['project_id'])
        current = []
        for item in result['evidence']['files']:
            try: current.append(hashlib.sha256(code.resolve(config, item['path']).read_bytes()).hexdigest() == item['sha256'])
            except (ValueError, OSError): current.append(False)
        result['evidence']['files_unchanged'] = all(current) and config['revision'] == row['workspace_revision']
    result['has_more'] = max(result['stdout_total_chars'], result['stderr_total_chars']) > offset + max_chars
    result['next_offset'] = offset + max_chars
    running = _running.get(job_id)
    quiet = False
    if running:
        newest = max(Path(row[key]).stat().st_mtime for key in ('stdout_path', 'stderr_path'))
        quiet = time.time() - newest > 3
    result['interaction_state'] = ('unknown' if row['status'] == 'unknown' else 'timeout' if row['status'] == 'timed_out' else 'exited' if row['status'] != 'running'
        else 'waiting_input' if row['waiting_input'] else 'idle_inferred' if quiet else 'running')
    result['idle_is_inference'] = result['interaction_state'] == 'idle_inferred'
    if deliver and row['status'] != 'running':
        with repository.unit_of_work() as conn:
            conn.deliver(job_id, str(getattr(context, 'run_id', '') or 'owner-read'))
    return result


async def _watch(job_id, process, owner, context, revision, timeout_seconds, started):
    status, error = '', ''
    try:
        with repository.unit_of_work(write=False) as conn:
            row=dict(conn.job_for_watch((job_id,)))
        proof=json.loads(row['evidence_json'])
        saved_policy=proof.get('execution_policy_revision')
        while process.poll() is None:
            if time.monotonic() - started >= timeout_seconds:
                status, error = 'timed_out', '命令执行超时。'; break
            try:
                code.project_context(context, 'execute', revision)
                from .execution_policy_service import configuration
                if saved_policy is not None and saved_policy != configuration()['revision']:
                    raise ValueError('执行权限已改变，命令已停止。')
                if context.task_id:
                    from . import agent_task_service as tasks
                    if (tasks.get(context.task_id) or {}).get('status') in {'paused', 'cancelled'}:
                        raise ValueError('任务已停止。')
            except ValueError as exc:
                status, error = 'cancelled', str(exc); break
            if any(Path(row[key]).stat().st_size > 32 * 1024 * 1024 for key in ('stdout_path', 'stderr_path')):
                status, error = 'failed', '命令输出超过 32 MB。'; break
            await asyncio.sleep(0.2)
        if status:
            _terminate(process, owner)
        await asyncio.to_thread(process.wait)
        if not status:
            with repository.unit_of_work(write=False) as conn:
                saved = conn.job_status((job_id,))
            status = 'cancelled' if saved and saved[0] == 'cancelled' else 'completed' if process.returncode == 0 else 'failed'
        if status=='completed':
            from .execution_policy_service import configuration
            if saved_policy is not None and saved_policy!=configuration()['revision']:
                raise ValueError('执行权限已改变，结果未写回。')
            code.project_context(context,'execute',revision)
            from .terminal_sandbox import publish
            published=await asyncio.to_thread(publish,job_id,context,revision)
            if published:
                _update(job_id,evidence_json=workspace.encode({**proof,'sandbox_result':published}))
        _update(job_id, status=status, exit_code=process.returncode, duration=time.monotonic() - started,
                error=error, finished_at=db.now_iso())
    except asyncio.CancelledError:
        _terminate(process, owner)
        _update(job_id, status='cancelled', error='执行已停止。', finished_at=db.now_iso())
        raise
    except Exception as exc:
        _terminate(process, owner)
        _update(job_id, status='failed', error=str(exc)[:500], finished_at=db.now_iso())
    finally:
        from .terminal_sandbox import remove,SandboxCleanupError
        try:
            await asyncio.to_thread(remove,job_id)
        except SandboxCleanupError as exc:
            _update(job_id,status='unknown',error=str(exc)[:500],finished_at=db.now_iso())
        if process.poll() is None:
            _terminate(process,owner)
        await asyncio.to_thread(process.wait)
        if owner: owner.close()
        _running.pop(job_id, None)


def _terminate(process, owner):
    if owner:
        owner.stop()
    elif process.poll() is None:
        if os.name == 'nt': process.terminate()
        else:
            import signal
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass


async def start(context, command, shell='powershell', workspace_revision=0, timeout_seconds=120, evidence=None, interactive=False):
    initialize()
    config = code.project_context(context, 'execute', workspace_revision)
    from . import execution_policy_service as policy, terminal_sandbox as sandbox
    execution_policy=policy.configuration()
    if interactive and execution_policy['terminal_mode'] == 'sandbox':
        raise ValueError('当前隔离终端不支持持续 stdin；请选择本机授权终端或一次命令。')
    if shell not in {'powershell','cmd','bash'}:
        raise ValueError('终端类型无效。')
    available = shells()
    if shell not in available and execution_policy['terminal_mode']!='sandbox':
        raise ValueError('所选终端不可用；当前可用：' + ', '.join(available))
    if not command.strip() or '\x00' in command:
        raise ValueError('请输入有效命令。')
    args = {'powershell': ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command],
            'cmd': ['/d', '/s', '/c', command], 'bash': ['--noprofile', '--norc', '-c', command]}[shell]
    if interactive and shell == 'powershell':
        args.remove('-NonInteractive')
    job_id = 'terminal_' + uuid.uuid4().hex
    folder = db.settings.db_path.parent / '终端回执' / job_id
    folder.mkdir(parents=True, exist_ok=True)
    out, err = folder / 'stdout.log', folder / 'stderr.log'
    started = time.monotonic()
    process, owner = None, None
    proof={**(evidence or {}),'execution_policy_revision':execution_policy['revision'],
           'execution_environment':execution_policy['terminal_mode']}
    invocation=[available[shell],*args] if execution_policy['terminal_mode']!='sandbox' else None
    if execution_policy['terminal_mode']=='sandbox':
        preparation=asyncio.create_task(asyncio.to_thread(sandbox.prepare,job_id,config['root'],folder,execution_policy,shell,command))
        try:
            invocation,_=await asyncio.shield(preparation)
        except asyncio.CancelledError:
            # A copying thread cannot be cancelled. Wait until it relinquishes its
            # owned stage, then remove just this newly-created receipt directory.
            try:
                await preparation
            except Exception:
                pass
            await asyncio.to_thread(sandbox.remove,job_id)
            import shutil
            await asyncio.to_thread(shutil.rmtree,folder)
            raise
        except BaseException:
            # No job has started. Preparation owns this unique empty receipt folder.
            if not any(folder.iterdir()):
                folder.rmdir()
            raise
    try:
        with repository.unit_of_work() as conn:
            conn.insert_job((job_id, config['project_id'], context.task_id, context.conversation_id, command, shell, config['root'],
               config['revision'], 'running', str(out), str(err), workspace.encode(proof), db.now_iso()))
        environment = sandbox.environment()
        with out.open('wb') as stdout, err.open('wb') as stderr:
            process = subprocess.Popen(invocation, cwd=config['root'], stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
                        stdout=stdout, stderr=stderr, env=environment,
                        creationflags=(0x08000000 | 0x00000004) if os.name == 'nt' else 0,
                        start_new_session=os.name != 'nt')
        if os.name == 'nt': owner = WindowsJob(process._handle)
        _update(job_id, pid=process.pid, interactive=int(interactive))
        _running[job_id] = (process, owner, context.task_id, context.conversation_id)
        worker = asyncio.create_task(_watch(job_id, process, owner, context, config['revision'], timeout_seconds, started))
        _workers.add(worker); worker.add_done_callback(_workers.discard)
        if context.task_id:
            from . import agent_task_service as tasks
            task = tasks.get(context.task_id)
            tasks.update(context.task_id, status='waiting_jobs', snapshot={
                'waiting_terminal': [*task['snapshot'].get('waiting_terminal', []), job_id]})
        # Enter the watcher's try/finally before exposing a cancellable job.
        await asyncio.sleep(0)
    except BaseException as exc:
        if process: _terminate(process, owner)
        cleanup_error=''
        try:
            await asyncio.to_thread(sandbox.remove,job_id)
        except sandbox.SandboxCleanupError as cleanup:
            cleanup_error=str(cleanup)
        if process: await asyncio.to_thread(process.wait)
        if owner: owner.close()
        _update(job_id, status='unknown' if cleanup_error else 'cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed',
                error=(cleanup_error or str(exc) or '启动已停止。')[:500], finished_at=db.now_iso())
        raise
    return {'job_id': job_id, 'status': 'running', 'pid': process.pid, 'command': command,
            'interactive': bool(interactive), 'interaction_state': 'running',
            'cwd': config['root'], 'execution_environment': 'Docker 隔离项目副本' if execution_policy['terminal_mode']=='sandbox'
                else '本机执行（无 OS 沙箱）', 'workspace_revision': config['revision']}


def send_input(context, job_id, text='', *, close=False, waiting_input=False):
    row = _require(context, job_id)
    code.project_context(context, 'execute', row['workspace_revision'])
    if context.task_id:
        from . import agent_task_service as tasks
        if (tasks.get(context.task_id) or {}).get('status') in {'cancelled', 'paused'}:
            raise ValueError('任务已停止，不能继续写入终端。')
    from .execution_policy_service import configuration
    if json.loads(row['evidence_json']).get('execution_policy_revision') != configuration()['revision']:
        raise ValueError('终端权限已改变，请重新启动。')
    running = _running.get(job_id)
    if not row['interactive'] or not running or running[0].poll() is not None:
        raise ValueError('此命令没有可写入的交互 stdin。')
    if len(text) > 12000 or '\x00' in text:
        raise ValueError('输入最多 12000 字符，不能包含空字符。')
    stream = running[0].stdin
    if not stream or stream.closed:
        raise ValueError('命令输入已关闭。')
    if text:
        stream.write(text.encode('utf-8')); stream.flush()
    if close:
        stream.close()
    _update(job_id, waiting_input=int(waiting_input and not close))
    return {'job_id': job_id, 'input_sent': bool(text), 'stdin_closed': close,
            'interaction_state': 'waiting_input' if waiting_input and not close else 'running'}


def stop(context, job_id):
    row = _require(context, job_id)
    running = _running.get(job_id)
    if running:
        _terminate(running[0], running[1])
        from .terminal_sandbox import remove,SandboxCleanupError
        try:
            remove(job_id)
        except SandboxCleanupError as exc:
            _update(job_id,status='unknown',error=str(exc)[:500],finished_at=db.now_iso())
            return {'job_id':job_id,'status':'unknown','error':str(exc)}
        _update(job_id, status='cancelled', error='用户停止命令。', finished_at=db.now_iso())
    return {'job_id': job_id, 'status': 'cancelled' if running else row['status']}


def stop_task(task_id):
    for job_id, (process, owner, task, _) in list(_running.items()):
        if task == task_id:
            _terminate(process, owner)
            from .terminal_sandbox import remove,SandboxCleanupError
            try:
                remove(job_id)
            except SandboxCleanupError as exc:
                _update(job_id,status='unknown',error=str(exc)[:500],finished_at=db.now_iso())
            else:
                _update(job_id, status='cancelled', error='父任务已停止。', finished_at=db.now_iso())


def task_jobs(task_id):
    initialize()
    with repository.unit_of_work(write=False) as conn:
        return [dict(row) for row in conn.task_jobs((task_id,))]


async def verify(context, kind, workspace_revision, change_id='', timeout_seconds=300):
    config = code.project_context(context, 'execute', workspace_revision)
    command = config['verification'].get(kind, '').strip()
    if not command: raise ValueError('请先在项目代码设置填写此类验证命令。')
    evidence = {'kind': kind, 'workspace_revision': workspace_revision, 'files': []}
    if change_id:
        change = code.change_detail(context, change_id)
        if change['status'] != 'applied': raise ValueError('只能验证当前已应用的修改。')
        for item in change['files']:
            file = code.resolve(config, item['path'])
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            if digest != item['after_sha256']: raise ValueError('文件已变化，请重新检查 diff。')
            evidence['files'].append({'path': item['path'], 'sha256': digest})
        evidence['change_id'] = change_id
    from .execution_policy_service import configuration
    selected='bash' if configuration()['terminal_mode']=='sandbox' else 'powershell' if 'powershell' in shells() else 'bash'
    return await start(context, command, selected, workspace_revision, timeout_seconds, evidence)
