"""Standalone upgrade transaction. Wait, verify, journal, install, validate, recover.

Only managed program files are copied/replaced. User data and model directories never move.
The signed manifest is checked again in this separate process immediately before use.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import uuid
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from .manifest import UpdateChannel, UpdateError, read_json_bytes, verify_file, verify_manifest
from .transport import atomic_json, ensure_plain_path

MANAGED_NAMES = ('Mio.exe', 'MioUpdater.exe', '_internal', '构建清单.json',
                 '数据目录.txt', 'unins000.exe', 'unins000.dat')


def channel_path() -> Path:
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    for path in (bundle / 'desktop' / 'update_channel.json', bundle / 'update_channel.json'):
        if path.is_file():
            return path
    raise UpdateError('升级助手缺少内置更新配置。')


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_owned_process(process: subprocess.Popen) -> None:
    """Stop only the child tree we spawned; never kill processes by global name."""
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=0x08000000, timeout=20, check=False)
    else:
        process.terminate()
    process.wait(timeout=20)


def run_owned(arguments, *, timeout, **kwargs):
    process = subprocess.Popen(arguments, **kwargs)
    try:
        return subprocess.CompletedProcess(arguments, process.wait(timeout=timeout))
    except subprocess.TimeoutExpired:
        terminate_owned_process(process)
        raise


@contextmanager
def runner_lock(job: Path):
    """OS-held file lock: a crashed helper cannot leave a permanently held lock."""
    with (job / 'runner.lock').open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateError('另一升级助手正在处理此更新。') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def load_ticket(path: Path) -> dict:
    path = ensure_plain_path(path)
    ticket = read_json_bytes(path.read_bytes())
    job = ensure_plain_path(Path(ticket.get('job', '')))
    state = ensure_plain_path(Path(ticket.get('state_root', '')))
    install = ensure_plain_path(Path(ticket.get('install_root', '')))
    runtime = ensure_plain_path(Path(ticket.get('runtime_root', '')))
    if (ticket.get('schema_version') != 1 or path != job / 'ticket.json'
            or job.parent != state / 'updates' / 'jobs' or len(job.name) != 32
            or install == install.parent or state == install or state.is_relative_to(install / '_internal')
            or not runtime.is_relative_to(state)):
        raise UpdateError('升级任务路径或格式无效。')
    lock = read_json_bytes((state / 'updates' / 'install.lock').read_bytes())
    if Path(lock.get('job', '')).resolve() != job.resolve():
        raise UpdateError('升级任务没有本机安装锁。')
    if not isinstance(ticket.get('parent_pid'), int) or ticket['parent_pid'] <= 0:
        raise UpdateError('升级任务缺少有效父进程。')
    return ticket


def _remove(path: Path) -> None:
    ensure_plain_path(path)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def copy_managed_entry(source: Path, destination: Path) -> None:
    """Copy a managed entry across volumes, publishing only a complete snapshot.

    Keep the source until the whole transaction succeeds. A crash while copying
    leaves an ignored temporary entry, not a partially valid rollback snapshot.
    Never follow linked model or data directories embedded in a program tree.
    """
    source, destination = ensure_plain_path(source), ensure_plain_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.mio-copy-' + uuid.uuid4().hex)
    try:
        if source.is_dir():
            for directory, folders, files in os.walk(source, followlinks=False):
                ensure_plain_path(Path(directory))
                for name in folders + files:
                    ensure_plain_path(Path(directory) / name)
            shutil.copytree(source, temporary, symlinks=False)
        else:
            shutil.copy2(source, temporary)
        # The complete staged copy and its destination are on the same volume.
        # Recovery can repeat this step because the saved source is retained.
        if destination.exists():
            _remove(destination)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            _remove(temporary)


class UpgradeTransaction:
    def __init__(self, ticket_path: Path, channel: UpdateChannel, *,
                 run: Callable = run_owned, spawn: Callable = subprocess.Popen,
                 is_alive: Callable = process_alive):
        self.ticket_path = ticket_path
        self.ticket = load_ticket(ticket_path)
        self.job = Path(self.ticket['job'])
        self.install = Path(self.ticket['install_root'])
        self.state = Path(self.ticket['state_root'])
        self.channel = channel
        self._run, self._spawn, self._alive = run, spawn, is_alive
        self.backup = self.job / 'previous-program'
        self.journal = {'phase': 'waiting_parent', 'original_names': [], 'data_touched': False}
        if (self.job / 'journal.json').exists():
            self.journal = read_json_bytes((self.job / 'journal.json').read_bytes())
        self.environment = dict(os.environ, MIO_DESKTOP_STATE_DIR=str(self.state),
                                MIO_RUNTIME_ROOT=self.ticket['runtime_root'], MIO_DISABLE_DOTENV='1')
        self.flags = 0x08000000 if os.name == 'nt' else 0

    def _phase(self, value: str, **extra) -> None:
        self.journal.update(phase=value, **extra)
        atomic_json(self.job / 'journal.json', self.journal)

    def _result(self, state: str, message: str, **extra) -> None:
        result = {'state': state, 'message': message, 'job': str(self.job),
                  'time': time.time(), 'target_build_id': self.ticket['target_build_id'], **extra}
        atomic_json(self.job / 'result.json', result)
        atomic_json(self.state / 'updates' / 'last-result.json', result)

    def _wait_parent(self, seconds: float = 120) -> None:
        deadline = time.monotonic() + seconds
        while self._alive(self.ticket['parent_pid']):
            if time.monotonic() >= deadline:
                raise UpdateError('Mio 尚未完全退出，本次未替换程序文件。')
            time.sleep(0.25)

    def _command(self, arguments: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
        with (self.job / 'process-output.log').open('ab') as log:
            return self._run(arguments, cwd=str(self.job), env=self.environment,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             timeout=timeout, creationflags=self.flags)

    def _restore_program(self) -> None:
        self._phase('restoring_program')
        originals = set(self.journal.get('original_names', []))
        if not originals.issubset(MANAGED_NAMES):
            raise UpdateError('程序回退清单包含未授权路径。')
        for name in MANAGED_NAMES:
            saved = ensure_plain_path(self.backup / name)
            target = ensure_plain_path(self.install / name)
            if saved.exists():
                copy_managed_entry(saved, target)
            elif name not in originals:
                _remove(target)
        # Inno's uninstall registration can retain a new display version after a
        # program-file rollback. Restore only our existing per-user display value.
        if os.name == 'nt':
            try:
                import winreg
                manifest = json.loads((self.install / '构建清单.json').read_text('utf-8-sig'))
                subkey = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6D807D33-DAA4-4A61-A79F-37A78E32C029}_is1'
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
                    location = winreg.QueryValueEx(key, 'InstallLocation')[0]
                    if Path(location).resolve() == self.install.resolve():
                        winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, str(manifest['app_version']))
            except (OSError, ValueError, KeyError):
                pass

    def recover(self, error: str) -> None:
        if not self.backup.exists():
            self._result('failed', error)
            (self.state / 'updates' / 'install.lock').unlink(missing_ok=True)
            return
        self._restore_program()
        if self.journal.get('data_touched'):
            self._phase('restoring_data')
            restored = self._command([str(self.install / 'Mio.exe'), '--update-restore', str(self.ticket_path)], 300)
            if restored.returncode != 0:
                self._phase('recovery_failed')
                self._result('recovery_failed', '旧程序已保留，但数据回退未完成。已阻止正常启动，请保留升级目录。', error=error)
                raise UpdateError('数据恢复未完成，升级锁保留，不能假装回退成功。')
        self._phase('recovered')
        self._result('recovered', '更新未完成，已恢复旧程序与兼容数据。', error=error)
        (self.state / 'updates' / 'install.lock').unlink(missing_ok=True)
        self._spawn([str(self.install / 'Mio.exe')], cwd=str(self.install), env=self.environment)

    def execute(self, *, recovery_only: bool = False) -> None:
        with runner_lock(self.job):
            atomic_json(self.job / 'helper-ready.json', {'ok': True, 'pid': os.getpid()})
            self._wait_parent()
            if self.journal['phase'] in {'completed', 'recovered'}:
                (self.state / 'updates' / 'install.lock').unlink(missing_ok=True)
                self._spawn([str(self.install / 'Mio.exe')], cwd=str(self.install), env=self.environment)
                return
            if recovery_only or self.journal['phase'] != 'waiting_parent':
                self.recover('检测到上次升级中断。')
                return
            release = verify_manifest((self.job / 'manifest.json').read_bytes(), self.channel)
            package = ensure_plain_path(Path(self.ticket['package']))
            if package.parent != self.state / 'updates' / 'downloads' or package.name != release.sha256 + '.exe':
                raise UpdateError('安装包不在已验证的更新缓存中。')
            verify_file(package, release)
            if release.build_id != self.ticket['target_build_id']:
                raise UpdateError('目标版本身份不一致。')
            originals = [name for name in MANAGED_NAMES if (self.install / name).exists()]
            if 'Mio.exe' not in originals or '_internal' not in originals:
                raise UpdateError('原安装目录不完整，未执行自动覆盖。')
            self._phase('backing_up_program', original_names=originals)
            self.backup.mkdir()
            try:
                for name in originals:
                    ensure_plain_path(self.install / name)
                    copy_managed_entry(self.install / name, self.backup / name)
                self._phase('installing')
                result = self._command([str(package), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-',
                    '/NORESTART', '/NORESTARTAPPLICATIONS', '/NOCLOSEAPPLICATIONS', '/UPDATE=1',
                    '/DIR='+str(self.install), '/DataDir='+str(self.state),
                    '/LOG='+str(self.job / 'installer.log')], 600)
                if result.returncode != 0:
                    raise UpdateError(f'安装器退出码 {result.returncode}，正在回退。')
                marker = (self.install / '数据目录.txt').read_text('utf-8-sig').strip()
                if Path(marker).resolve() != self.state:
                    raise UpdateError('升级改变了数据目录，已阻止启动。')
                self._phase('validating', data_touched=True)
                result = self._command([str(self.install / 'Mio.exe'), '--update-verify', str(self.ticket_path)], 240)
                if result.returncode != 0:
                    raise UpdateError('新版本启动/迁移验证失败。')
                verified = read_json_bytes((self.job / 'verified.json').read_bytes())
                if not verified.get('ok') or verified.get('build_id') != release.build_id:
                    raise UpdateError('新版本没有返回正确的启动验证回执。')
                self._phase('completed')
                self._result('completed', '更新安装并验证成功，已保留升级前备份。', version=release.version)
                (self.state / 'updates' / 'install.lock').unlink(missing_ok=True)
                self._spawn([str(self.install / 'Mio.exe')], cwd=str(self.install), env=self.environment)
            except Exception as exc:
                self.recover(str(exc))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Mio 独立升级助手')
    parser.add_argument('--ticket', type=Path, required=True)
    parser.add_argument('--recover', action='store_true')
    args = parser.parse_args(argv)
    transaction = None
    try:
        channel = UpdateChannel.load(channel_path())
        transaction = UpgradeTransaction(args.ticket, channel)
        transaction.execute(recovery_only=args.recover)
        return 0
    except Exception as exc:
        if transaction is not None:
            if transaction.journal.get('phase') != 'recovery_failed':
                transaction._result('failed', str(exc)[:700])
            # Keep a pending recovery lock if any program/data replacement began.
            if not transaction.backup.exists():
                (transaction.state / 'updates' / 'install.lock').unlink(missing_ok=True)
        return 1
