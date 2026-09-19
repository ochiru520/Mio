"""Small desktop/backend adapter; upgrade mechanics live outside launcher.py."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from .manifest import UpdateChannel, UpdateError, read_json_bytes, verify_manifest
from .service import UpdateService
from .transport import atomic_json, ensure_plain_path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_channel() -> UpdateChannel:
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    candidates = [bundle / 'desktop' / 'update_channel.json', bundle / 'update_channel.json']
    for candidate in candidates:
        if candidate.is_file():
            return UpdateChannel.load(candidate)
    return UpdateChannel('development', '0.0.0', '', {}, False)


def create_service(state_root: Path, runtime_root: Path, exit_app) -> UpdateService:
    from app import backup_service, model_runtime, privacy_service
    from app.main import app
    installed = bool(getattr(sys, 'frozen', False))
    install_root = Path(sys.executable).resolve().parent if installed else Path(__file__).resolve().parents[2]
    bundle = Path(getattr(sys, '_MEIPASS', install_root))
    loop = getattr(app.state, 'runtime_event_loop', None)

    def run_async(coroutine):
        if loop is None or not loop.is_running():
            coroutine.close()
            raise UpdateError('本地服务尚未准备好，未执行升级。')
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=50)
        except BaseException:
            future.cancel()
            raise

    def resume():
        run_async(app.state.finish_maintenance('update_preparation_cancelled', resume=True))

    def prepare():
        if model_runtime.live_operations():
            raise UpdateError('仍有聊天、语音或任务正在执行，请完成或取消后再安装更新。')
        run_async(app.state.enter_maintenance('application_update'))
        try:
            backup = backup_service.create_complete_backup(kind='update', reason='安装新版本前备份',
                                                         include_model_weights=False)
            build = read_json_bytes((install_root / '构建清单.json').read_bytes(), limit=2_000_000)
            return {'backup_name': backup.name, 'backup_sha256': sha256(backup),
                    'previous_build_id': build['build_id']}
        except BaseException:
            resume()
            raise

    return UpdateService(load_channel(), state_root=state_root, install_root=install_root,
                         runtime_root=runtime_root, helper_source=bundle / 'MioUpdater.exe',
                         installed=installed, privacy_paused=lambda: bool(privacy_service._load_state().get('paused')),
                         prepare=prepare, resume=resume, exit_app=exit_app)


class UpdateBridgeMixin:
    """Expose fixed commands only. Paths, manifest bytes and shell commands stay native."""
    _update_service: UpdateService | None = None

    def _update_call(self, name: str, *args) -> dict:
        service = self._update_service
        if service is None:
            return {'ok': False, 'error': '更新服务尚未初始化。'}
        try:
            return {'ok': True, 'status': getattr(service, name)(*args)}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)[:700], 'status': service.status()}

    def update_status(self) -> dict:
        return self._update_call('status')

    def configure_updates(self, values: dict) -> dict:
        return self._update_call('configure', values)

    def check_updates(self) -> dict:
        return self._update_call('check')

    def download_update(self) -> dict:
        return self._update_call('download')

    def cancel_update(self) -> dict:
        return self._update_call('cancel')

    def install_update(self, confirmed: bool = False) -> dict:
        if confirmed is not True:
            return {'ok': False, 'error': '安装前需要用户明确确认关闭应用并重启。'}
        return self._update_call('install')


def startup_recovery(state_root: Path) -> bool:
    """Do not open the database while a pending upgrade is replacing/migrating it."""
    lock = state_root / 'updates' / 'install.lock'
    if not lock.exists():
        return False
    # A recovery request is never silently converted into normal startup.
    from .helper import load_ticket, process_alive
    data = read_json_bytes(lock.read_bytes())
    job = ensure_plain_path(Path(str(data.get('job', ''))))
    ticket = load_ticket(job / 'ticket.json')
    if Path(ticket['state_root']).resolve() != state_root.resolve():
        raise UpdateError('升级锁指向了另一份数据，请检查更新恢复记录。')
    if process_alive(int(ticket['parent_pid'])):
        raise UpdateError('上一实例还在执行升级或退出，请稍后重新打开。')
    helper = ensure_plain_path(job / 'MioUpdater.exe')
    # Recovery is a locally pinned executable, not a downloaded arbitrary command.
    helper_hash = str(ticket.get('helper_sha256', ''))
    if not helper.is_file() or not helper_hash or sha256(helper) != helper_hash:
        raise UpdateError('升级恢复助手缺失或校验失败。请保留 updates 目录并使用正式安装包修复。')
    subprocess.Popen([str(helper), '--ticket', str(job / 'ticket.json'), '--recover'],
                     cwd=str(job), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=0x08000000 if os.name == 'nt' else 0)
    return True


def run_update_mode(argument: str, ticket_path: Path, configure_runtime) -> int:
    """Quarantined migration verification/paired restore; no schedulers, models or QQ."""
    from .helper import load_ticket
    ticket = load_ticket(ticket_path)
    job = Path(ticket['job'])
    runtime_root = Path(ticket['runtime_root'])
    install_root = Path(ticket['install_root'])
    expected_exe = install_root / 'Mio.exe'
    if not getattr(sys, 'frozen', False) or Path(sys.executable).resolve() != expected_exe.resolve():
        raise UpdateError('升级验证只能由对应安装版运行。')
    os.environ['MIO_RUNTIME_ROOT'] = str(runtime_root)
    os.environ['MIO_DESKTOP_STATE_DIR'] = ticket['state_root']
    os.environ['MIO_DISABLE_DOTENV'] = '1'
    configure_runtime(runtime_root)
    build = read_json_bytes((install_root / '构建清单.json').read_bytes(), limit=2_000_000)
    if argument == '--update-restore':
        if build.get('build_id') != ticket['previous_build_id']:
            raise UpdateError('旧程序身份不匹配，未恢复数据。')
        from app import backup_service, db
        backup = backup_service.backup_path(ticket['backup_name'])
        if sha256(backup) != ticket['backup_sha256']:
            raise UpdateError('升级前数据备份校验失败。')
        backup_service.restore_backup(ticket['backup_name'])
        with db.get_conn() as conn:
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise UpdateError('恢复后的数据库未通过完整性检查。')
        atomic_json(job / 'restored.json', {'ok': True, 'build_id': build['build_id']})
        return 0
    channel = load_channel()
    # Embedded config in the newly installed app must trust the original signed release.
    release = verify_manifest((job / 'manifest.json').read_bytes(), channel)
    if build.get('build_id') != ticket['target_build_id'] or release.build_id != build['build_id']:
        raise UpdateError('安装文件与已签名版本不一致。')
    artifacts = build.get('artifacts', [])
    if not artifacts:
        raise UpdateError('缺少可核验的构建制品。')
    for item in artifacts:
        path = ensure_plain_path(install_root / str(item.get('path', '')))
        if (not path.is_relative_to(install_root) or not path.is_file()
                or path.stat().st_size != item.get('size')
                or sha256(path) != str(item.get('sha256', '')).lower()):
            raise UpdateError('新程序制品校验失败：' + str(item.get('name', 'unknown')))
    from app import db, migration_service
    db.init_db()
    migration_service.run_migrations()
    with db.get_conn() as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise UpdateError('新版本数据库完整性检查失败。')
    # Import API application without running lifespan/background jobs; validates
    # bundled modules, routes and static resources but makes no provider request.
    from app.main import app
    if not any(getattr(route, 'path', '') == '/health' for route in app.routes):
        raise UpdateError('新版本缺少健康接口。')
    atomic_json(job / 'verified.json', {'ok': True, 'build_id': build['build_id'],
                                      'runtime_root': str(runtime_root), 'mode': 'isolated_startup'})
    return 0
