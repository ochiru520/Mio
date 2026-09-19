"""Allowlisted diagnostics: no user content, paths, provider addresses or errors."""
from __future__ import annotations
from datetime import datetime
from . import db, dependency_installer
from . import local_vision_service


def snapshot() -> dict:
    allowed = {'ready', 'configured', 'unconfigured', 'missing', 'installed', 'unverified', 'degraded'}
    dependencies = []
    for item in dependency_installer.list_dependencies():
        verification = item.get('verification') or {}
        checked_at = ''
        try:
            checked_at = datetime.fromisoformat(str(verification.get('checked_at', ''))).isoformat()
        except ValueError:
            pass
        code = verification.get('error_code', '')
        safe_codes = {'missing_resources', 'missing_runtime_dependency', 'encoding_error', 'model_load_failed', 'timeout', 'process_start_failed'}
        dependencies.append({'id': str(item['id']),
                             'status': item.get('status') if item.get('status') in allowed else 'unknown',
                             'installing': bool(item.get('installing')),
                             'verified': bool(item.get('verified') or verification.get('ok')),
                             'checked_at': checked_at,
                             'verification_level': 'model_load' if verification.get('verification_level') == 'model_load' else 'unverified',
                             'error_code': code if code in safe_codes else '',
                             'has_error': bool(item.get('last_error') or verification.get('ok') is False or item.get('status') == 'degraded')})
    with db.get_conn() as conn:
        jobs = {r[0]: r[1] for r in conn.execute('SELECT status,COUNT(*) FROM creation_jobs GROUP BY status')}
    vision = local_vision_service.passive_status()
    return {'schema_version': 1, 'checked_at': db.now_iso(), 'dependencies': dependencies,
            'local_vision': {'inference_ready': bool(vision.get('inference_ready')),
                             'inference_state': vision.get('inference_state', 'unverified'),
                             'probe_stale': bool(vision.get('probe_stale', True))},
            'creation_job_counts': {key: jobs.get(key, 0) for key in ('created', 'running', 'unknown', 'failed', 'completed', 'cancelled')},
            'scope': '状态快照；不主动推理，不包含聊天、日记、路径、密钥、供应商地址或原始错误。'}
