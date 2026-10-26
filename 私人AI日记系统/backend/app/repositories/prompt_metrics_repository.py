"""Additive runtime ledger metrics; no prompts, credentials or request URLs."""
from __future__ import annotations

import json


def initialize(conn) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runtime_model_calls)")}
    additions = {
        "request_category": "TEXT NOT NULL DEFAULT ''",
        "is_streaming": "INTEGER NOT NULL DEFAULT 0",
        "estimated_input_json": "TEXT NOT NULL DEFAULT '{}'",
        "cached_prompt_tokens": "INTEGER",
        "cached_tokens_reporting": "TEXT NOT NULL DEFAULT ''",
        "protocol_mode": "TEXT NOT NULL DEFAULT ''",
        "input_tokens_reported": "INTEGER",
        "cache_write_tokens": "INTEGER",
        "attempts_tracked": 'INTEGER',
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE runtime_model_calls ADD COLUMN {name} {definition}")
    conn.execute('''CREATE TABLE IF NOT EXISTS runtime_model_attempts (
        id TEXT PRIMARY KEY, call_id TEXT NOT NULL, attempt_index INTEGER NOT NULL,
        started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
        actual_model_id TEXT NOT NULL, provider_id TEXT NOT NULL, protocol_mode TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running', http_status INTEGER NOT NULL DEFAULT 0,
        duration_ms REAL, prompt_tokens INTEGER, completion_tokens INTEGER,
        cached_prompt_tokens INTEGER, cache_write_tokens INTEGER, cost_yuan REAL,
        cost_source TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT '',
        route TEXT NOT NULL DEFAULT '')''')


def record_estimate(conn, call_id: str, *, category: str, streaming: bool, metrics: dict) -> None:
    # Library/tests can use an existing database before runtime.initialize.
    # Additional diagnostics must not break the older ledger or migrate it
    # implicitly during a model request.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runtime_model_calls)")}
    if not {"request_category", "is_streaming", "estimated_input_json"}.issubset(columns):
        return
    conn.execute("UPDATE runtime_model_calls SET request_category=?,is_streaming=?,estimated_input_json=? WHERE id=?",
                 (category, int(streaming), json.dumps(metrics, ensure_ascii=False, separators=(",", ":")), call_id))
    if 'attempts_tracked' in columns:
        conn.execute('UPDATE runtime_model_calls SET attempts_tracked=1 WHERE id=?', (call_id,))


def record_usage(conn, call_id: str, result) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runtime_model_calls)")}
    if not {"cached_prompt_tokens", "cached_tokens_reporting", "protocol_mode"}.issubset(columns):
        return
    cached = max(0, min(int(getattr(result, "cached_prompt_tokens", 0) or 0), int(result.prompt_tokens or 0)))
    reported = getattr(result, 'cached_tokens_reporting', 'unknown') in {'reported', 'reported_positive', 'reported_zero'} or cached > 0
    protocol = getattr(result, "protocol_mode", "")
    protocol = protocol if protocol in {"responses", "chat_completions"} else ""
    conn.execute("UPDATE runtime_model_calls SET cached_prompt_tokens=?,cached_tokens_reporting=?,protocol_mode=? WHERE id=?",
                 (cached if reported else None, ('reported_positive' if cached else 'reported_zero') if reported else 'unknown', protocol, call_id))
    if {'input_tokens_reported', 'cache_write_tokens'}.issubset(columns):
        conn.execute('UPDATE runtime_model_calls SET input_tokens_reported=?,cache_write_tokens=? WHERE id=?',
            (int(bool(getattr(result, 'input_tokens_reported', False) or result.prompt_tokens > 0)), getattr(result, 'cache_write_tokens', None), call_id))


def start_attempt(conn, call_id, identifier, *, model_id, provider_id, protocol_mode, route, now):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_model_attempts'").fetchone():
        return False
    index = conn.execute('SELECT COUNT(*) FROM runtime_model_attempts WHERE call_id=?', (call_id,)).fetchone()[0] + 1
    conn.execute('INSERT INTO runtime_model_attempts(id,call_id,attempt_index,started_at,actual_model_id,provider_id,protocol_mode,route) VALUES(?,?,?,?,?,?,?,?)',
        (identifier, call_id, index, now, model_id, provider_id, protocol_mode, route))
    return True


def finish_attempt(conn, identifier, *, now, status, http_status=0, duration_ms=None, error_code=''):
    conn.execute('UPDATE runtime_model_attempts SET finished_at=?,status=?,http_status=?,duration_ms=?,error_code=? WHERE id=?',
                 (now, status, http_status, duration_ms, error_code, identifier))


def attempt_usage(conn, identifier, result):
    reported = getattr(result, 'input_tokens_reported', False) or result.prompt_tokens > 0
    cache_reported = getattr(result, 'cached_tokens_reporting', 'unknown') != 'unknown' or result.cached_prompt_tokens > 0
    conn.execute("UPDATE runtime_model_attempts SET status='completed',prompt_tokens=?,completion_tokens=?,cached_prompt_tokens=?,cache_write_tokens=?,cost_yuan=?,cost_source=?,actual_model_id=?,provider_id=? WHERE id=?",
        (result.prompt_tokens if reported else None, result.completion_tokens if reported else None,
         result.cached_prompt_tokens if cache_reported else None, getattr(result, 'cache_write_tokens', None), result.cost_yuan, result.cost_source,
         getattr(result, 'provider_model', '') or result.model, result.provider_id, identifier))


def interrupt_attempts(conn, now):
    conn.execute("UPDATE runtime_model_attempts SET status='unknown',finished_at=?,error_code='interrupted' WHERE status IN ('running','received')", (now,))


def finalize_attempts(conn, call_id, error_code='invalid_response'):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_model_attempts'").fetchone():
        return
    conn.execute("UPDATE runtime_model_attempts SET status='failed',error_code=? WHERE call_id=? AND status='received'", (error_code, call_id))


def usage_summary(start, modes):
    from contextlib import closing
    from .. import db
    import math
    with closing(db.get_conn()) as conn:
        logical = [dict(row) for row in conn.execute('SELECT * FROM runtime_model_calls WHERE started_at>=?', (start,))]
        available = conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_model_attempts'").fetchone()
        attempts = [dict(row) for row in conn.execute('SELECT a.*,c.mode,c.request_category FROM runtime_model_attempts a JOIN runtime_model_calls c ON c.id=a.call_id WHERE a.started_at>=?', (start,))] if available else []
    tracked = {row['call_id'] for row in attempts}
    # Old logical records are counted once. Never add their usage to child attempts.
    rows = attempts + [{**row, 'call_id': row['id'], 'attempt_index': 1,
        'prompt_tokens': row['prompt_tokens'] if row.get('input_tokens_reported') or row['prompt_tokens'] > 0 else None,
        'cached_prompt_tokens': row.get('cached_prompt_tokens') if row.get('cached_tokens_reporting') in {'reported_positive','reported_zero','reported'} else None}
        for row in logical if row['id'] not in tracked and not row.get('attempts_tracked')]
    not_sent = [row for row in logical if row['id'] not in tracked and row.get('attempts_tracked')]
    for row in rows:
        row['request_category'] = row.get('request_category') or 'legacy'
        for field in ('actual_model_id', 'provider_id', 'protocol_mode', 'route'):
            row[field] = row.get(field) or ''
    def aggregate(selected):
        measured = [row for row in selected if row.get('prompt_tokens') is not None]
        cached = [row for row in measured if row.get('cached_prompt_tokens') is not None]
        cache_writes = [row for row in measured if row.get('cache_write_tokens') is not None]
        known = [row for row in selected if row.get('cost_yuan') is not None]
        durations = sorted(row['duration_ms'] for row in selected if row.get('duration_ms') is not None)
        denominator = sum(row['prompt_tokens'] for row in cached)
        tokens = sum(row['prompt_tokens'] for row in measured)
        return {'calls': len(selected), 'failed': sum(row['status'] in {'failed','cancelled','unknown'} for row in selected),
            'retries': sum(row.get('attempt_index', 1) > 1 for row in selected),
            'prompt_tokens': tokens, 'completion_tokens': sum(row.get('completion_tokens') or 0 for row in selected),
            'cached_prompt_tokens': sum(row['cached_prompt_tokens'] for row in cached),
            'cache_write_tokens': sum(row['cache_write_tokens'] for row in cache_writes) if cache_writes else None,
            'cache_write_reported_calls': len(cache_writes),
            'cache_write_reporting_coverage': len(cache_writes)/len(measured) if measured else None,
            'cache_reported_prompt_tokens': denominator,
            'cache_hit_ratio': sum(row['cached_prompt_tokens'] for row in cached) / denominator if denominator else None,
            'input_usage_calls': len(measured), 'cache_reported_calls': len(cached),
            'input_usage_coverage': len(measured)/len(selected) if selected else None,
            'cache_reporting_coverage': len(cached)/len(measured) if measured else None,
            'cache_input_token_coverage': denominator/tokens if tokens else None,
            'known_cost_yuan': sum(row['cost_yuan'] for row in known),
            'provider_reported_cost_yuan': sum(row['cost_yuan'] for row in known if row.get('cost_source') == 'provider_reported'),
            'estimated_cost_yuan': sum(row['cost_yuan'] for row in known if row.get('cost_source') != 'provider_reported'),
            'unknown_cost_calls': len(selected)-len(known),
            'p95_duration_ms': durations[math.ceil(len(durations)*.95)-1] if durations else None}
    return {'modes': [{'mode': mode, 'not_sent_calls': sum(row['mode'] == mode for row in not_sent), **aggregate([row for row in rows if row['mode'] == mode])} for mode in modes],
        'categories': [{'category': category, **aggregate([row for row in rows if row.get('request_category') == category])} for category in sorted({row.get('request_category') or 'legacy' for row in rows})],
        'routes': [{'model_id': model, 'provider_id': provider, 'protocol_mode': protocol, 'route': route, **aggregate([row for row in rows if (row.get('actual_model_id'), row.get('provider_id'), row.get('protocol_mode'), row.get('route')) == (model,provider,protocol,route)])} for model,provider,protocol,route in sorted({(row.get('actual_model_id') or '',row.get('provider_id') or '',row.get('protocol_mode') or '',row.get('route') or '') for row in rows})],
        'totals': aggregate(rows), 'logical_calls': len(logical), 'not_sent_calls': len(not_sent), 'scope': '真实HTTP尝试；发送前拒绝单列，旧记录按逻辑调用计数，缺失用量与费用保持未知'}


def latest_request_metrics(conversation_id):
    from contextlib import closing
    from .. import db
    if not db.settings.db_path.is_file():
        return None
    with closing(db.get_conn()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_model_calls'").fetchone():
            return None
        columns = {row[1] for row in conn.execute('PRAGMA table_info(runtime_model_calls)')}
        if 'estimated_input_json' not in columns:
            return None
        row = conn.execute("SELECT estimated_input_json FROM runtime_model_calls WHERE conversation_id=? AND status='completed' AND request_category NOT IN ('context_compression','memory','background') ORDER BY started_at DESC,rowid DESC LIMIT 1", (conversation_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None
