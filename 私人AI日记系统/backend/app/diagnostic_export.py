"""Allowlisted diagnostics: no user content, paths, provider addresses or errors."""
from __future__ import annotations
from datetime import datetime
from .repositories import diagnostic_export_repository as repository
from . import db, dependency_installer
from . import local_vision_service


def snapshot() -> dict:
    from .capability_checks import snapshot as capability_snapshot
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
    with repository.transaction() as conn:
        jobs = {r[0]: r[1] for r in conn.snapshot_find_creation_jobs()}
    vision = local_vision_service.passive_status()
    return {'schema_version': 1, 'checked_at': db.now_iso(), 'dependencies': dependencies,
            'capabilities': capability_snapshot()['capabilities'],
            'local_vision': {'inference_ready': bool(vision.get('inference_ready')),
                             'inference_state': vision.get('inference_state', 'unverified'),
                             'probe_stale': bool(vision.get('probe_stale', True))},
            'creation_job_counts': {key: jobs.get(key, 0) for key in ('created', 'running', 'unknown', 'failed', 'completed', 'cancelled')},
            'scope': '状态快照；不主动推理，不包含聊天、日记、路径、密钥、供应商地址或原始错误。'}


def behavior_baseline() -> dict:
    """Return a repeatable allowlisted behavior contract for local diagnostics."""
    from .backup_service import CURRENT_APP_VERSION
    from .capability_checks import snapshot as capability_snapshot

    tables = (
        'messages', 'agent_tasks', 'agent_task_runs', 'agent_run_steps',
        'knowledge_documents', 'knowledge_chunks', 'knowledge_ocr_state',
        'learning_sessions', 'learning_items',
    )
    counts = {}
    with repository.transaction() as conn:
        for table in tables:
            exists = conn.behavior_baseline_find_sqlite_master((table,))
            counts[table] = int(conn.behavior_baseline_f_schema(table)[0]) if exists else 0
    capabilities = capability_snapshot().get('capabilities') or []
    return {
        'schema_version': 1,
        'app_version': CURRENT_APP_VERSION,
        'checked_at': db.now_iso(),
        'capabilities': [
            {'id': str(item.get('id') or ''), 'status': str(item.get('status') or item.get('health') or 'unknown')}
            for item in capabilities if isinstance(item, dict) and item.get('id')
        ],
        'table_counts': counts,
        'contracts': {
            'workspace': ['/api/workspace', '/api/workspace/projects', '/api/workspace/tasks'],
            'sources': ['/api/workspace/projects/{project_id}/search', '/api/agent/work/{task_id}/citations'],
            'audio': ['/api/companion/call/start', '/api/companion/voice/audio'],
        },
        'scope': '可重复行为基线；仅包含版本、能力状态、允许的表计数和接口契约，不包含用户内容、路径、密钥或供应商地址。',
    }
