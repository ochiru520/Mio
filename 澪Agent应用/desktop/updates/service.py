"""Thread-safe application update state machine, independent from chat/search settings."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .manifest import Release, UpdateChannel, UpdateError, verify_file, verify_manifest, version_tuple
from .transport import UpdateCancelled, UpdateTransport, atomic_json, ensure_plain_path

BUSY = {'checking', 'downloading', 'preparing', 'installing'}


class UpdateService:
    def __init__(self, channel: UpdateChannel, *, state_root: Path, install_root: Path,
                 runtime_root: Path, helper_source: Path, installed: bool,
                 privacy_paused: Callable[[], bool], prepare: Callable[[], dict],
                 resume: Callable[[], None], exit_app: Callable[[], None],
                 transport: UpdateTransport | None = None, spawn: Callable = subprocess.Popen):
        self.channel = channel
        self.state_root = state_root.resolve()
        self.install_root = install_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.cache = ensure_plain_path(self.state_root / 'updates')
        self.helper_source = helper_source
        self.installed = installed
        self._privacy_paused, self._prepare, self._resume, self._exit_app = privacy_paused, prepare, resume, exit_app
        self._transport = transport or UpdateTransport()
        self._spawn = spawn
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._release: Release | None = None
        self._manifest: bytes | None = None
        self._package: Path | None = None
        self._prefs = {'auto_check': True, 'auto_download': False, 'last_checked': 0, 'highest_seen': ''}
        try:
            saved = json.loads((self.cache / 'preferences.json').read_text('utf-8'))
            if not isinstance(saved, dict):
                raise ValueError('invalid preferences')
            self._prefs.update({k: saved[k] for k in self._prefs if k in saved})
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self._prefs.update(auto_check=False, auto_download=False)
        self._status = {'state': 'idle', 'message': '尚未检查更新', 'downloaded': 0, 'total': 0, 'release': None}
        self._read_previous_result()

    def _read_previous_result(self) -> None:
        try:
            payload = json.loads((self.cache / 'last-result.json').read_text('utf-8'))
            self._status['last_result'] = payload
        except (OSError, ValueError):
            pass

    def _set(self, **values) -> None:
        with self._lock:
            self._status.update(values)

    def status(self) -> dict:
        with self._lock:
            return {**self._status, 'current_version': self.channel.version, 'channel': self.channel.channel,
                    'configured': bool(self.channel.enabled and self.channel.feed_url and self.channel.public_keys),
                    'installed': self.installed, 'privacy_paused': self._privacy_paused(),
                    'auto_check': bool(self._prefs['auto_check']), 'auto_download': bool(self._prefs['auto_download'])}

    def configure(self, values: dict) -> dict:
        if not isinstance(values, dict) or set(values) - {'auto_check', 'auto_download'}:
            raise UpdateError('不支持的更新设置。')
        if any(type(v) is not bool for v in values.values()):
            raise UpdateError('更新设置必须为开启或关闭。')
        with self._lock:
            self._prefs.update(values)
            atomic_json(self.cache / 'preferences.json', self._prefs)
        return self.status()

    def _cancelled(self) -> bool:
        return self._cancel.is_set() or self._stop.is_set() or self._privacy_paused()

    def _network_allowed(self) -> None:
        if self._privacy_paused():
            raise UpdateError('隐私总控已暂停外部连接，未发起更新请求。')
        if not self.channel.enabled or not self.channel.feed_url or not self.channel.public_keys:
            raise UpdateError('此构建尚未配置已签名更新源；开发版不会混入公开正式版。')

    def _start_job(self, state: str, function: Callable[[], None]) -> dict:
        with self._lock:
            if self._status['state'] in BUSY:
                return self.status()
            self._cancel.clear()
            self._status.update(state=state, message='正在检查更新' if state == 'checking' else '正在下载安装包')
            def run():
                try:
                    function()
                except UpdateCancelled as exc:
                    self._set(state='cancelled', message=str(exc))
                except Exception as exc:
                    self._set(state='failed', message=str(exc)[:500] or type(exc).__name__)
            self._thread = threading.Thread(target=run, name='mio-update-'+state, daemon=True)
            self._thread.start()
        return self.status()

    def check(self) -> dict:
        self._network_allowed()
        return self._start_job('checking', self._check)

    def _check(self) -> None:
        raw = self._transport.manifest(self.channel.feed_url, self._cancelled)
        release = verify_manifest(raw, self.channel, highest_seen=str(self._prefs.get('highest_seen') or ''))
        if self._cancelled():
            raise UpdateCancelled('已停止检查更新。')
        with self._lock:
            self._prefs.update(last_checked=time.time(), highest_seen=release.version)
            atomic_json(self.cache / 'preferences.json', self._prefs)
            self._release, self._manifest = release, raw
            self._package = None
            newer = version_tuple(release.version) > version_tuple(self.channel.version)
            self._status.update(state='available' if newer else 'up_to_date', release=release.public(),
                                message='发现新版本' if newer else '已验证发布清单，当前已是此通道最新版')
        if newer and self._prefs['auto_download']:
            self._set(state='downloading', message='正在自动下载安装包')
            self._download()

    def download(self) -> dict:
        self._network_allowed()
        if not self._release or not self._manifest:
            raise UpdateError('请先检查更新。')
        if version_tuple(self._release.version) <= version_tuple(self.channel.version):
            raise UpdateError('没有可安装的新版本。')
        return self._start_job('downloading', self._download)

    def _download(self) -> None:
        release = verify_manifest(self._manifest or b'', self.channel,
                                  highest_seen=str(self._prefs.get('highest_seen') or ''))
        destination = self.cache / 'downloads' / (release.sha256 + '.exe')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(destination.parent).free < release.size + 64 * 1024**2:
            raise UpdateError('磁盘空间不足，无法下载安装包。')
        package = self._transport.download(release, destination,
            lambda current, total: self._set(downloaded=current, total=total), self._cancelled)
        if self._cancelled():
            raise UpdateCancelled('下载已停止，未安装。')
        self._package = package
        self._set(state='ready', message='安装包签名与完整性验证通过，可安装并重启')

    def cancel(self) -> dict:
        with self._lock:
            if self._status['state'] in {'preparing', 'installing'}:
                raise UpdateError('已进入升级阶段，请勿强制中断；升级助手会记录结果并处理失败。')
            self._cancel.set()
        return self.status()

    def install(self) -> dict:
        self._network_allowed()
        with self._lock:
            if self._status['state'] != 'ready' or not self._package or not self._manifest:
                raise UpdateError('安装包尚未准备好。')
            if not self.installed or not self.helper_source.is_file():
                raise UpdateError('安装并重启仅适用于包含升级助手的 Windows 安装版。')
            release = verify_manifest(self._manifest, self.channel, highest_seen=self._prefs['highest_seen'])
            verify_file(self._package, release)
            ensure_plain_path(self.install_root)
            if self.state_root == self.install_root or self.state_root.is_relative_to(self.install_root / '_internal'):
                raise UpdateError('用户数据位置与程序文件冲突，不能自动升级。')
            if shutil.disk_usage(self.install_root).free < release.size * 5 + 256 * 1024**2:
                raise UpdateError('安装磁盘空间不足，暂未退出应用。')
            self._status.update(state='preparing', message='正在保存升级前备份')
        job = self.cache / 'jobs' / uuid.uuid4().hex
        lock = self.cache / 'install.lock'
        owns_lock = False
        prepared = False
        try:
            ensure_plain_path(job)
            job.mkdir(parents=True)
            with lock.open('x', encoding='utf-8') as stream:
                json.dump({'job': str(job), 'pid': os.getpid()}, stream)
            owns_lock = True
            helper = job / 'MioUpdater.exe'
            shutil.copy2(self.helper_source, helper)
            snapshot = self._prepare()  # waits for maintenance, then creates a verified SQLite-safe backup
            prepared = True
            if self._cancelled():
                raise UpdateCancelled('隐私暂停，已取消升级准备。')
            (job / 'manifest.json').write_bytes(self._manifest)
            ticket = {'schema_version': 1, 'job': str(job), 'parent_pid': os.getpid(),
                      'install_root': str(self.install_root), 'state_root': str(self.state_root),
                      'runtime_root': str(self.runtime_root), 'package': str(self._package),
                      'backup_name': str(snapshot['backup_name']), 'backup_sha256': str(snapshot['backup_sha256']),
                      'previous_build_id': str(snapshot['previous_build_id']),
                      'target_build_id': release.build_id,
                      'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
            atomic_json(job / 'ticket.json', ticket)
            flags = 0x08000000 if os.name == 'nt' else 0
            process = self._spawn([str(helper), '--ticket', str(job / 'ticket.json')],
                                  cwd=str(job), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=flags)
            # Wait for a verified helper handshake before closing the UI.
            deadline = time.monotonic() + 25
            while not (job / 'helper-ready.json').is_file():
                if process.poll() is not None or time.monotonic() >= deadline:
                    if process.poll() is None:
                        from .helper import terminate_owned_process
                        terminate_owned_process(process)
                    raise UpdateError('升级助手未就绪，已保留当前应用并取消安装。')
                time.sleep(0.1)
            # No file replacement starts until the parent has fully exited.
            self._set(state='installing', message='升级助手已启动，正在关闭 Mio', helper_pid=process.pid)
            threading.Timer(0.8, self._exit_app).start()
            return self.status()
        except Exception as exc:
            if prepared:
                self._resume()
            if owns_lock:
                lock.unlink(missing_ok=True)
            self._set(state='failed', message=str(exc)[:500])
            raise

    def start(self) -> None:
        def watch():
            while not self._stop.wait(30):
                if (self._prefs['auto_check'] and self.channel.enabled and self.channel.feed_url
                        and self.channel.public_keys and not self._privacy_paused()
                        and time.time() - float(self._prefs.get('last_checked') or 0) >= 12 * 3600):
                    try:
                        self.check()
                    except UpdateError:
                        pass
                    # A failure is not 'up to date'. Bound retries without rewriting last successful check.
                    if self._stop.wait(3600):
                        return
        threading.Thread(target=watch, name='mio-update-scheduler', daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        self._cancel.set()
