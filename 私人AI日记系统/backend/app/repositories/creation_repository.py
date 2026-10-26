"""Creation jobs, assets and presets; shared locks stay in repository transactions."""
from __future__ import annotations
from contextlib import closing, contextmanager
from .. import db

@contextmanager
def transaction():
    with closing(db.get_conn()) as conn, conn:
        yield conn

def job(conn, parameters):
    return conn.execute("SELECT * FROM creation_jobs WHERE id = ?", parameters)

def list_jobs(conn, parameters):
    return conn.execute("SELECT * FROM creation_jobs ORDER BY created_at DESC LIMIT ?", parameters)

def output_source(conn, parameters):
    return conn.execute("SELECT media_type, status, outputs_json FROM creation_jobs WHERE id = ?", parameters)

def asset_by_hash(conn, parameters):
    return conn.execute("SELECT * FROM creation_assets WHERE sha256 = ? ORDER BY created_at DESC LIMIT 1", parameters)

def update_job(conn, job_id, selected):
    allowed = {"status", "stage", "progress", "prompt_id", "client_id", "spec_json",
               "preset_snapshot_json", "outputs_json", "confirmation_reason", "error",
               "started_at", "finished_at", "updated_at"}
    if not set(selected) <= allowed:
        raise ValueError("Unsupported creation job fields.")
    assignment = ", ".join(f"{key} = ?" for key in selected)
    return conn.execute(f"UPDATE creation_jobs SET {assignment} WHERE id = ?", (*selected.values(), job_id))

def asset(conn, parameters):
    return conn.execute("SELECT * FROM creation_assets WHERE id = ?", parameters)

def list_assets(conn, parameters):
    return conn.execute("SELECT * FROM creation_assets ORDER BY created_at DESC LIMIT ?", parameters)

def insert_asset(conn, parameters):
    return conn.execute("""
            INSERT INTO creation_assets(id, original_name, mime_type, path, size, sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, parameters)

def presets_by_kind(conn, parameters):
    return conn.execute("SELECT * FROM creation_presets WHERE kind = ? ORDER BY updated_at DESC", parameters)

def list_presets(conn):
    return conn.execute("SELECT * FROM creation_presets ORDER BY kind, updated_at DESC")

def preset_identity(conn, parameters):
    return conn.execute("SELECT id, created_at FROM creation_presets WHERE id = ?", parameters)

def upsert_preset(conn, parameters):
    return conn.execute("""
            INSERT INTO creation_presets(
                id, kind, name, content, negative_prompt, params_json,
                reference_asset_ids_json, approved, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind=excluded.kind, name=excluded.name, content=excluded.content,
                negative_prompt=excluded.negative_prompt, params_json=excluded.params_json,
                reference_asset_ids_json=excluded.reference_asset_ids_json,
                approved=1, updated_at=excluded.updated_at
            """, parameters)

def preset(conn, parameters):
    return conn.execute("SELECT * FROM creation_presets WHERE id = ?", parameters)

def delete_preset(conn, parameters):
    return conn.execute("DELETE FROM creation_presets WHERE id = ?", parameters)

def active_job(conn, parameters):
    return conn.execute("SELECT id FROM creation_jobs WHERE status IN ('created','submitted','queued','running','cancel_requested') AND id != ? LIMIT 1", parameters)

def begin_immediate(conn):
    return conn.execute("BEGIN IMMEDIATE")

def creation_active_job(conn, parameters):
    return conn.execute("SELECT 1 FROM creation_jobs WHERE status IN ('created','submitted','queued','running','cancel_requested') AND id!=? LIMIT 1", parameters)

def job_by_key(conn, parameters):
    return conn.execute("SELECT * FROM creation_jobs WHERE idempotency_key = ?", parameters)

def latest_child(conn, parameters):
    return conn.execute("SELECT * FROM creation_jobs WHERE parent_job_id=? AND status!='cancelled' ORDER BY created_at DESC,rowid DESC LIMIT 1", parameters)

def creation_job_by_key(conn, parameters):
    return conn.execute("SELECT * FROM creation_jobs WHERE idempotency_key=?", parameters)

def insert_job(conn, parameters):
    return conn.execute("""
            INSERT INTO creation_jobs(
                id, conversation_id, source, media_type, backend, workflow_id,
                provider_id, model_id, status, stage, progress, spec_json,
                preset_snapshot_json, confirmation_reason, idempotency_key,
                parent_job_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
            """, parameters)

def latest_child_id(conn, parameters):
    return conn.execute("SELECT id FROM creation_jobs WHERE parent_job_id=? AND status!='cancelled' ORDER BY created_at DESC,rowid DESC LIMIT 1", parameters)

def confirm_job(conn, parameters):
    return conn.execute("UPDATE creation_jobs SET status='created',stage='confirmed',confirmation_reason='',updated_at=? WHERE id=? AND status='needs_confirmation'", parameters)

def active_jobs(conn):
    return conn.execute("SELECT * FROM creation_jobs WHERE status IN ('created','submitted','queued','running','cancel_requested') ORDER BY created_at")

def creation_job(conn, parameters):
    return conn.execute('SELECT * FROM creation_jobs WHERE id=?', parameters)

def record_reconciliation(conn, parameters):
    return conn.execute('UPDATE creation_jobs SET status=?,stage=?,spec_json=?,error=?,updated_at=? WHERE id=?', parameters)

def job_id_by_key(conn, parameters):
    return conn.execute("SELECT id FROM creation_jobs WHERE idempotency_key=?", parameters)

def birefnet_job_by_key(conn, parameters):
    return conn.execute("SELECT * FROM creation_jobs WHERE idempotency_key=?", parameters)

def insert_cutout(conn, parameters):
    return conn.execute("""INSERT INTO creation_jobs(id,conversation_id,source,media_type,backend,workflow_id,
            provider_id,model_id,status,stage,progress,spec_json,preset_snapshot_json,confirmation_reason,
            idempotency_key,parent_job_id,created_at,updated_at)
            VALUES(?,?,?,?,'comfyui',?,'','BiRefNet-portrait','created','created',0,?,'{}','',?,'',?,?)""", parameters)

def insert_cutout_retry(conn, parameters):
    return conn.execute("""INSERT INTO creation_jobs(id,conversation_id,source,media_type,backend,workflow_id,
            provider_id,model_id,status,stage,spec_json,preset_snapshot_json,confirmation_reason,
            idempotency_key,parent_job_id,created_at,updated_at)
            VALUES(?,?,?,?,'comfyui',?,'','BiRefNet-portrait','needs_confirmation','needs_confirmation',?,'{}',?,?,?, ?,?)""", parameters)

def message_attachment(conn, parameters):
    return conn.execute('SELECT attachments_json FROM messages WHERE id=? AND conversation_id=?', parameters)

def asset_id_by_hash(conn, parameters):
    return conn.execute('SELECT id FROM creation_assets WHERE sha256=? ORDER BY created_at DESC LIMIT 1', parameters)
