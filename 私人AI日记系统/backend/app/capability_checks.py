"""Persistent, explicit capability checks. GET never starts a probe."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

from .config import settings

DEFINITIONS = {
    'chat': ('聊天模型', '发送固定测试短句，可能产生少量模型费用。', 'settings-models'),
    'tts': ('语音合成', '合成固定短句并检查音频；云端音色可能计费，不播放声音。', 'settings-voice'),
    'asr': ('语音识别', '使用 Windows 系统音色生成英文样本，再由本地 Whisper 识别；不录音。', 'settings-voice'),
    'vision': ('本地视觉', '发送内置测试图片到已启动的 Mio 本地视觉服务；不截屏。', 'settings-environment'),
    'comfyui': ('ComfyUI', '检查服务与节点接口；不提交生成任务。', 'settings-creation'),
    'qq': ('QQ 通道', '核对当前 OneBot 连接和账号；不发送消息。', 'settings-qq'),
}
MESSAGES = {
    '': '', 'not_configured': '请先保存对应模型或服务配置。',
    'authentication': '供应商拒绝凭据，请检查模型设置中的 Key。',
    'unavailable': '服务未就绪，请启动对应服务并重新验证。',
    'invalid_output': '返回结果未通过检查，请到对应设置测试和修复。',
    'timeout': '检查超时，已停止本次探针；请检查资源占用后重试。',
    'cancelled': '已取消；不会保存迟到的成功结果。',
    'interrupted': '上次检查随应用退出中断，请重新验证。',
    'configuration_changed': '配置或资源已变化，旧结果失效，请重新验证。',
    'expired': '验证结果已过期，请重新验证。',
    'sample_unavailable': '缺少 Windows 英文系统音色，无法生成 ASR 测试样本；请安装英文语音组件。',
    'runtime_missing': '缺少本地运行环境或模型，请在环境中心安装或修复。',
    'probe_failed': '能力检查失败，请在对应设置检查配置并重试。',
    'state_unreadable': '历史验证记录无法读取，请重新验证。',
}
TIMEOUT_SECONDS = 150
RESULT_TTL_SECONDS = 24 * 60 * 60
_lock = threading.RLock()
_active: dict[str, asyncio.Task] = {}


def _path() -> Path:
    return settings.data_dir / '能力验证.json'


def _read() -> dict:
    try:
        value = json.loads(_path().read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        return {key: {'status': 'failed', 'error_code': 'state_unreadable'} for key in DEFINITIONS}


def _save(capability: str, row: dict) -> None:
    with _lock:
        values = _read()
        values[capability] = row
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def fingerprint() -> str:
    # Include encrypted config bytes and environment-backed settings; never export them.
    digest = hashlib.sha256(json.dumps(vars(settings), default=str, sort_keys=True).encode())
    for name in ('model_profiles_path', 'companion_config_path', 'runtime_config_path'):
        path = getattr(settings, name, None)
        if path:
            try:
                digest.update(Path(path).read_bytes())
            except FileNotFoundError:
                digest.update(b'missing')
    # Changes to installed resources invalidate old results without hashing model weights.
    for root_name in ('voice_training_dir', 'local_vision_dir'):
        root = Path(getattr(settings, root_name))
        for relative in ('.', '.genie-env/Lib/site-packages', 'GenieData', 'models',
                         'models/genie/mio-v1', 'models/blobs', 'cache/faster-whisper'):
            path = root / relative
            try:
                stat = path.stat()
                digest.update(f'{path}:{stat.st_mtime_ns}:{stat.st_size}'.encode())
            except FileNotFoundError:
                digest.update(f'{path}:missing'.encode())
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot() -> dict:
    with _lock:
        stored = _read()
    current = fingerprint()
    results = []
    for key, (label, description, target) in DEFINITIONS.items():
        row = stored.get(key) if isinstance(stored.get(key), dict) else {}
        row = row or {}
        state = row.get('status', 'unchecked')
        code = row.get('error_code', '')
        if state == 'running' and key not in _active:
            state, code = 'interrupted', 'interrupted'
        elif row.get('fingerprint') and row['fingerprint'] != current:
            state, code = 'stale', 'configuration_changed'
        elif state == 'passed':
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row['checked_at'])).total_seconds()
                if age > RESULT_TTL_SECONDS or age < 0:
                    state, code = 'stale', 'expired'
            except (ValueError, KeyError, TypeError):
                state, code = 'stale', 'expired'
        # Strict output allowlist: never return model names, paths, keys or responses.
        results.append({'id': key, 'label': label, 'description': description, 'target': target,
                        'status': state, 'error_code': code if code in MESSAGES else 'probe_failed',
                        'message': MESSAGES.get(code, MESSAGES['probe_failed']),
                        'run_id': row.get('run_id', ''), 'checked_at': row.get('checked_at', ''),
                        'duration_ms': row.get('duration_ms'), 'level': row.get('level', 'unverified'),
                        'human_check_required': key in {'tts', 'asr', 'qq'},
                        'can_cancel': key in _active})
    return {'capabilities': results, 'busy': bool(_active), 'checked_at': _now()}


def _require_allowed() -> None:
    from . import maintenance_service, model_runtime
    if maintenance_service.status()['blocked'] or model_runtime.privacy_blocked():
        raise ValueError('隐私暂停或数据维护期间不能启动能力实测。')


async def start(capability: str, *, consent: bool = False) -> dict:
    if capability not in DEFINITIONS:
        raise KeyError(capability)
    _require_allowed()
    if not consent:
        raise ValueError('请先确认该项检查的执行范围和可能的费用。')
    if _active:
        raise ValueError('已有能力检查正在进行，请完成或取消后再试。')
    from .dependency_installer import _running_installs, _install_running
    if any(_install_running(key) for key in list(_running_installs)):
        raise ValueError('组件正在安装，请等待完成后再做实测。')
    row = {'run_id': uuid.uuid4().hex, 'status': 'running', 'level': 'unverified',
           'checked_at': _now(), 'fingerprint': fingerprint(), 'error_code': ''}
    _save(capability, row)
    _active[capability] = asyncio.create_task(_run(capability, row))
    return snapshot()


async def _run(capability: str, row: dict) -> None:
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(_execute(capability), TIMEOUT_SECONDS)
        _require_allowed()
        if row['fingerprint'] != fingerprint():
            row.update(status='stale', error_code='configuration_changed')
        else:
            code = result.get('error_code', '')
            row.update(status='passed' if result.get('ok') else 'failed',
                       error_code=code if code in MESSAGES else 'probe_failed',
                       level=result.get('level', 'unverified'))
    except asyncio.CancelledError:
        row.update(status='cancelled', error_code='cancelled')
    except asyncio.TimeoutError:
        row.update(status='failed', error_code='timeout')
    except Exception:
        row.update(status='failed', error_code='probe_failed')
    finally:
        row.update(checked_at=_now(), duration_ms=round((time.monotonic() - started) * 1000))
        try:
            _save(capability, row)
        finally:
            _active.pop(capability, None)


async def cancel(capability: str, run_id: str) -> dict:
    if capability not in DEFINITIONS:
        raise KeyError(capability)
    with _lock:
        current = _read().get(capability, {})
    if current.get('run_id') != run_id:
        raise ValueError('检查已变化，请刷新后再取消。')
    task = _active.get(capability)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if _active.get(capability) is task:
            current.update(status='cancelled', error_code='cancelled', checked_at=_now())
            _save(capability, current)
            _active.pop(capability, None)
    return snapshot()


async def cancel_all() -> None:
    for key in list(_active):
        await cancel(key, _read().get(key, {}).get('run_id', ''))


async def _execute(capability: str) -> dict:
    if capability == 'vision':
        from .local_vision_service import _owned_server_url
        from .capability_probe_worker import vision
        try:
            return await vision(_owned_server_url())
        except OSError:
            return {'ok': False, 'error_code': 'unavailable'}
    if capability == 'qq':
        from .routes import onebot
        health = onebot.runtime_health_status()
        expected = str(settings.napcat_account or '').strip()
        accounts = health.get('connected_accounts', [])
        ok = bool(health.get('enabled') and accounts and (not expected or expected in accounts))
        return {'ok': ok, 'level': 'connection', 'error_code': '' if ok else 'unavailable'}
    return await _run_worker(capability)


async def _run_worker(capability: str) -> dict:
    from .probe_process import ProbeProcess
    # A private temporary directory contains only synthetic probe media / safe result codes.
    with tempfile.TemporaryDirectory(prefix='mio-capability-') as temporary:
        output = Path(temporary) / 'result.json'
        if getattr(sys, 'frozen', False):
            command = [sys.executable, '--capability-probe-worker', capability, str(output)]
        else:
            command = [sys.executable, '-m', 'app.capability_probe_worker', capability, str(output)]
        process = ProbeProcess(command, cwd=str(Path(__file__).resolve().parent.parent))
        try:
            while process.poll() is None:
                await asyncio.sleep(.1)
            if not output.is_file() or output.stat().st_size > 8192:
                return {'ok': False, 'error_code': 'probe_failed'}
            value = json.loads(output.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {'ok': False, 'error_code': 'probe_failed'}
        finally:
            process.close()
