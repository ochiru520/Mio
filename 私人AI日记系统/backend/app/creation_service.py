from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import mimetypes
import os
import shutil
import socket
import sqlite3
import subprocess
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

import httpx
import websockets
from PIL import Image

from . import db, maintenance_service
from .config import settings
from .creation_models import CreationJobRequest, CreationPresetRequest
from .creation_workflows import (
    WORKFLOWS,
    build_workflow_prompt,
    inspect_workflow,
    require_workflow,
    workflow_path,
    workflow_catalog,
)
from .model_registry import list_model_profiles
from .provider_compat import auth_headers, normalize_api_base_url


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "timed_out", "unknown"}
ACTIVE_STATUSES = {"submitted", "queued", "running", "cancel_requested"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov"}
PRESET_KINDS = {"character", "style", "project"}
_tasks: dict[str, asyncio.Task[None]] = {}


class RemoteSubmissionUnknown(RuntimeError):
    pass


def _mark_remote_unknown(job_id: str, reason: str) -> None:
    _update_job(job_id, status="unknown", stage="reconciliation_required",
                error="远程请求结果待核对，已停止自动重发。" + reason[:800], finished_at=db.now_iso())
_comfyui_start_lock = asyncio.Lock()
_comfyui_start_process: subprocess.Popen[Any] | None = None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: object, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _clean_name(value: str, fallback: str = "asset") -> str:
    name = Path(str(value or "").strip()).name[:160]
    return name or fallback


def _row_dict(row: object | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None  # type: ignore[arg-type]


def _timestamp(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _job_elapsed_seconds(item: Mapping[str, Any]) -> float:
    started = _timestamp(item.get("started_at")) or _timestamp(item.get("created_at"))
    if started is None:
        return 0.0
    finished = _timestamp(item.get("finished_at"))
    if finished is None:
        finished = datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
    return round(max(0.0, (finished - started).total_seconds()), 3)


def _duration_label(seconds: float) -> str:
    if seconds >= 60:
        minutes = int(seconds // 60)
        remainder = seconds - minutes * 60
        return f"{minutes} 分 {remainder:.1f} 秒"
    return f"{seconds:.1f} 秒"


def _job_public(row: object | None) -> dict[str, Any] | None:
    item = _row_dict(row)
    if item is None:
        return None
    spec = _loads(item.pop("spec_json", "{}"), {})
    snapshot = _loads(item.pop("preset_snapshot_json", "{}"), {})
    raw_outputs = _loads(item.pop("outputs_json", "[]"), [])
    outputs: list[dict[str, Any]] = []
    for index, output in enumerate(raw_outputs if isinstance(raw_outputs, list) else []):
        if not isinstance(output, dict):
            continue
        outputs.append({
            "index": index,
            "artifact_id": str(output.get("artifact_id") or ""),
            "name": str(output.get("name") or "输出"),
            "media_type": str(output.get("media_type") or item.get("media_type") or "image"),
            "mime_type": str(output.get("mime_type") or "application/octet-stream"),
            "size": max(0, int(output.get("size") or 0)),
            "sha256": str(output.get("sha256") or ""),
            "url": f"/api/creation/jobs/{item['id']}/outputs/{index}",
        })
    item.pop("idempotency_key", None)
    item["spec"] = spec if isinstance(spec, dict) else {}
    item["preset_snapshot"] = snapshot if isinstance(snapshot, dict) else {}
    item["outputs"] = outputs
    item["progress"] = round(max(0.0, min(1.0, float(item.get("progress") or 0))), 4)
    item["elapsed_seconds"] = _job_elapsed_seconds(item)
    if str(item.get("backend") or "") == "comfyui":
        item["generation_cost_yuan"] = 0.0
        item["generation_cost_source"] = "local_comfyui"
    else:
        item["generation_cost_yuan"] = None
        item["generation_cost_source"] = "unavailable"
    return item


def _preset_public(row: object | None) -> dict[str, Any] | None:
    item = _row_dict(row)
    if item is None:
        return None
    item["params"] = _loads(item.pop("params_json", "{}"), {})
    item["reference_asset_ids"] = _loads(item.pop("reference_asset_ids_json", "[]"), [])
    item["approved"] = bool(item.get("approved"))
    return item


def _asset_public(row: object | None) -> dict[str, Any] | None:
    item = _row_dict(row)
    if item is None:
        return None
    item.pop("path", None)
    item["url"] = f"/api/creation/assets/{item['id']}/content"
    return item


def get_job(job_id: str) -> dict[str, Any] | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM creation_jobs WHERE id = ?", (job_id,)).fetchone()
    return _job_public(row)


def workflow_source_file(job_id: str) -> tuple[Path, str]:
    """Return the exact approved UI workflow used by a local ComfyUI job."""
    row = _get_job_row(job_id)
    if row is None or str(row.get("backend") or "") != "comfyui":
        raise ValueError("鎵句笉鍒颁娇鐢ㄦ湰鍦板伐浣滄祦鐨勫垱浣滀换鍔°€?")
    definition = require_workflow(str(row.get("workflow_id") or ""), str(row.get("media_type") or ""), include_disabled=True)
    path = workflow_path(_require_comfy_root(), definition)
    if not path.is_file():
        raise ValueError("宸ヤ綔娴佹枃浠跺凡涓嶅彲鐢ㄣ€?")
    return path, definition.filename


def _get_job_row(job_id: str) -> dict[str, Any] | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM creation_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_dict(row)


def list_jobs(limit: int = 40) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM creation_jobs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(200, int(limit))),),
        ).fetchall()
    return [item for row in rows if (item := _job_public(row)) is not None]


def save_job_output_as_asset(job_id: str, index: int = 0) -> dict[str, Any]:
    """Copy a verified generated image into the reusable creation asset store."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT media_type, status, outputs_json FROM creation_jobs WHERE id = ?",
            (str(job_id),),
        ).fetchone()
    if row is None or str(row["status"] or "") != "completed":
        raise ValueError("上一项图片任务尚未完成，不能用于图片转视频。")
    if str(row["media_type"] or "") != "image":
        raise ValueError("只有图片输出可以作为视频参考图。")
    outputs = _loads(row["outputs_json"], [])
    if not isinstance(outputs, list) or index < 0 or index >= len(outputs):
        raise ValueError("上一项任务没有可用的图片输出。")
    output = outputs[index]
    if not isinstance(output, dict):
        raise ValueError("上一项任务的图片输出记录已损坏。")
    path = Path(str(output.get("path") or "")).resolve()
    if output.get("artifact_id"):
        from . import artifact_service
        path, _ = artifact_service.file(str(output["artifact_id"]))
    allowed_roots = [settings.creation_output_dir.resolve()]
    try:
        allowed_roots.append((_require_comfy_root() / "output").resolve())
    except ValueError:
        pass
    if not path.is_file() or (not output.get("artifact_id") and not any(path.is_relative_to(root) for root in allowed_roots)):
        raise ValueError("上一项任务的图片输出不在允许目录内。")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    recorded_digest = str(output.get("sha256") or "")
    if recorded_digest and digest != recorded_digest:
        raise ValueError("上一项任务的图片输出校验失败。")
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM creation_assets WHERE sha256 = ? ORDER BY created_at DESC LIMIT 1",
            (digest,),
        ).fetchone()
    if existing is not None:
        return _asset_public(existing) or {}
    mime_type = str(output.get("mime_type") or mimetypes.guess_type(path.name)[0] or "image/png")
    data_url = f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"
    return save_asset(str(output.get("name") or path.name), mime_type, data_url)


def _update_job(job_id: str, **changes: object) -> None:
    allowed = {
        "status", "stage", "progress", "prompt_id", "client_id", "spec_json",
        "preset_snapshot_json", "outputs_json", "confirmation_reason", "error",
        "started_at", "finished_at",
    }
    selected = {key: value for key, value in changes.items() if key in allowed}
    if not selected:
        return
    selected["updated_at"] = db.now_iso()
    assignment = ", ".join(f"{key} = ?" for key in selected)
    with db.get_conn() as conn:
        conn.execute(
            f"UPDATE creation_jobs SET {assignment} WHERE id = ?",
            (*selected.values(), job_id),
        )


def _require_asset_row(asset_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM creation_assets WHERE id = ?", (asset_id,)).fetchone()
    item = _row_dict(row)
    if item is None:
        raise ValueError(f"找不到创作素材：{asset_id}")
    path = Path(str(item["path"])).resolve()
    root = settings.creation_asset_dir.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("创作素材不在授权目录或文件已经丢失。")
    item["resolved_path"] = path
    return item


def get_asset(asset_id: str) -> dict[str, Any] | None:
    try:
        return _asset_public(_require_asset_row(asset_id))
    except ValueError:
        return None


def asset_file(asset_id: str) -> tuple[Path, str, str]:
    item = _require_asset_row(asset_id)
    return item["resolved_path"], str(item["mime_type"]), str(item["original_name"])


def list_assets(limit: int = 100) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM creation_assets ORDER BY created_at DESC LIMIT ?",
            (max(1, min(500, int(limit))),),
        ).fetchall()
    return [item for row in rows if (item := _asset_public(row)) is not None]


def save_asset(name: str, mime_type: str, data_url: str) -> dict[str, Any]:
    if not data_url.startswith("data:") or ";base64," not in data_url[:200]:
        raise ValueError("素材必须是 base64 data URL。")
    header, encoded = data_url.split(",", 1)
    declared = header[5:].split(";", 1)[0].lower()
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("素材不是有效的 base64。") from exc
    if not content or len(content) > settings.creation_max_asset_bytes:
        raise ValueError(f"素材大小必须在 1 字节到 {settings.creation_max_asset_bytes // 1048576} MB 之间。")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
            image_format = str(image.format or "").lower()
    except Exception as exc:
        raise ValueError("当前创作素材必须是可解码的图片。") from exc
    actual_mime = Image.MIME.get(image_format.upper()) or declared or mime_type.lower()
    if not actual_mime.startswith("image/"):
        raise ValueError("素材类型与图片内容不一致。")
    suffix = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}.get(image_format)
    if not suffix:
        raise ValueError("只支持 PNG、JPEG、WebP 或 GIF 图片素材。")
    asset_id = f"asset_{uuid.uuid4().hex[:24]}"
    path = (settings.creation_asset_dir / f"{asset_id}{suffix}").resolve()
    if not path.is_relative_to(settings.creation_asset_dir.resolve()):
        raise ValueError("素材路径超出授权目录。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    timestamp = db.now_iso()
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO creation_assets(id, original_name, mime_type, path, size, sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (asset_id, _clean_name(name, f"素材{suffix}"), actual_mime, str(path), len(content), digest, timestamp),
        )
    return get_asset(asset_id) or {}


def list_presets(kind: str = "") -> list[dict[str, Any]]:
    clean_kind = str(kind or "").strip()
    if clean_kind and clean_kind not in PRESET_KINDS:
        raise ValueError("不支持这个预设类型。")
    with db.get_conn() as conn:
        if clean_kind:
            rows = conn.execute(
                "SELECT * FROM creation_presets WHERE kind = ? ORDER BY updated_at DESC",
                (clean_kind,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM creation_presets ORDER BY kind, updated_at DESC").fetchall()
    return [item for row in rows if (item := _preset_public(row)) is not None]


def save_preset(payload: CreationPresetRequest, preset_id: str = "") -> dict[str, Any]:
    for asset_id in payload.reference_asset_ids:
        _require_asset_row(asset_id)
    clean_id = str(preset_id or "").strip() or f"preset_{uuid.uuid4().hex[:24]}"
    timestamp = db.now_iso()
    with db.get_conn() as conn:
        existing = conn.execute("SELECT id, created_at FROM creation_presets WHERE id = ?", (clean_id,)).fetchone()
        created_at = str(existing["created_at"]) if existing else timestamp
        conn.execute(
            """
            INSERT INTO creation_presets(
                id, kind, name, content, negative_prompt, params_json,
                reference_asset_ids_json, approved, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind=excluded.kind, name=excluded.name, content=excluded.content,
                negative_prompt=excluded.negative_prompt, params_json=excluded.params_json,
                reference_asset_ids_json=excluded.reference_asset_ids_json,
                approved=1, updated_at=excluded.updated_at
            """,
            (
                clean_id, payload.kind, payload.name, payload.content, payload.negative_prompt,
                _json(payload.params), _json(payload.reference_asset_ids), created_at, timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM creation_presets WHERE id = ?", (clean_id,)).fetchone()
    return _preset_public(row) or {}


def delete_preset(preset_id: str) -> bool:
    with db.get_conn() as conn:
        cursor = conn.execute("DELETE FROM creation_presets WHERE id = ?", (preset_id,))
    return cursor.rowcount > 0


def _preset_snapshot(spec: Mapping[str, Any]) -> dict[str, Any]:
    mapping = {
        "character": str(spec.get("character_preset_id") or ""),
        "style": str(spec.get("style_preset_id") or ""),
        "project": str(spec.get("project_preset_id") or ""),
    }
    result: dict[str, Any] = {}
    with db.get_conn() as conn:
        for kind, preset_id in mapping.items():
            if not preset_id:
                continue
            row = conn.execute("SELECT * FROM creation_presets WHERE id = ?", (preset_id,)).fetchone()
            preset = _preset_public(row)
            if preset is None or preset.get("kind") != kind or not preset.get("approved"):
                raise ValueError(f"{kind} 预设不存在、类型不符或尚未确认。")
            result[kind] = preset
    return result


def _effective_spec(payload: CreationJobRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = payload.model_dump(exclude_none=True)
    snapshot = _preset_snapshot(spec)
    prompt_parts = [str(spec["prompt"]).strip()]
    negative_parts = [str(spec.get("negative_prompt") or "").strip()]
    reference_ids = list(spec.get("reference_asset_ids") or [])
    for kind in ("character", "style", "project"):
        preset = snapshot.get(kind)
        if not isinstance(preset, dict):
            continue
        if preset.get("content"):
            prompt_parts.append(f"[{kind} preset]\n{preset['content']}")
        if preset.get("negative_prompt"):
            negative_parts.append(str(preset["negative_prompt"]))
        reference_ids.extend(str(item) for item in preset.get("reference_asset_ids") or [])
        params = preset.get("params")
        if isinstance(params, dict):
            for key in ("width", "height", "steps", "cfg", "duration_seconds", "fps"):
                if key not in spec and params.get(key) is not None:
                    spec[key] = params[key]
    spec["prompt"] = "\n\n".join(item for item in prompt_parts if item)
    spec["negative_prompt"] = ", ".join(item for item in negative_parts if item)
    spec["reference_asset_ids"] = list(dict.fromkeys(reference_ids))[:8]
    for asset_id in spec["reference_asset_ids"]:
        _require_asset_row(asset_id)
    if spec.get("batch_size", 1) != 1:
        spec["confirmation_reason"] = "批量生成会增加耗时和资源消耗，需要先确认。"
    return spec, snapshot


def _provider_connection(provider_id: str) -> dict[str, str]:
    profiles = [profile for profile in list_model_profiles() if profile.provider_id == provider_id]
    profile = next((item for item in profiles if item.base_urls and item.api_key), None)
    if profile is None:
        raise ValueError("这个供应商没有可用地址或本机密钥，请先在模型与 API 设置中配置。")
    if profile.api_key_error:
        raise ValueError(profile.api_key_error)
    return {
        "provider_id": profile.provider_id,
        "provider_name": profile.provider_name,
        "base_url": profile.base_urls[0],
        "api_key": profile.api_key,
        "auth_scheme": profile.auth_scheme,
    }


def remote_providers() -> list[dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for profile in list_model_profiles():
        if not profile.provider_id or profile.provider_id in result:
            if profile.provider_id in result:
                models = result[profile.provider_id].setdefault("models", [])
                if isinstance(models, list):
                    models.append({
                        "id": profile.model,
                        "name": profile.display_name or profile.model,
                    })
            continue
        result[profile.provider_id] = {
            "id": profile.provider_id,
            "name": profile.provider_name,
            "configured": bool(profile.base_urls and profile.api_key and not profile.api_key_error),
            "requires_key_reentry": bool(profile.api_key_error),
            "models": [{"id": profile.model, "name": profile.display_name or profile.model}],
        }
    return sorted(result.values(), key=lambda item: str(item["name"]).casefold())


def _active_job_exists(excluding: str = "") -> bool:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM creation_jobs WHERE status IN ('created','submitted','queued','running','cancel_requested') AND id != ? LIMIT 1",
            (excluding,),
        ).fetchone()
    return row is not None


def reserve_slot(conn, conversation_id: str = "", excluding: str = "") -> None:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    db.assert_conversation_writable(conversation_id, conn)
    if conn.execute("SELECT 1 FROM creation_jobs WHERE status IN ('created','submitted','queued','running','cancel_requested') AND id!=? LIMIT 1", (excluding,)).fetchone():
        raise ValueError("已有一项创作任务正在运行，请等待完成或先取消。")


def create_job(
    payload: CreationJobRequest,
    *,
    source: str = "creation_page",
    conversation_id: str = "",
    force_confirmation: bool = False,
    parent_job_id: str = "",
) -> tuple[dict[str, Any], bool]:
    spec, snapshot = _effective_spec(payload)
    backend = str(spec.get("backend") or "auto")
    if backend == "auto":
        backend = "comfyui"
        spec["backend"] = backend
    workflow_id = str(spec.get("workflow_id") or "")
    if backend == "comfyui":
        workflow = require_workflow(workflow_id, str(spec["media_type"]))
        workflow_id = workflow.id
        spec["workflow_id"] = workflow.id
    elif spec["media_type"] != "image":
        raise ValueError("远程中转站当前只支持图片生成。")
    if backend == "remote_api":
        _provider_connection(str(spec.get("provider_id") or ""))
    key = str(spec.get("idempotency_key") or "").strip()
    if not key:
        key = f"creation:{uuid.uuid4().hex}"
    with db.get_conn() as conn:
        existing = conn.execute("SELECT * FROM creation_jobs WHERE idempotency_key = ?", (key,)).fetchone()
    if existing is not None:
        return _job_public(existing) or {}, False
    needs_confirmation = bool(
        force_confirmation
        or backend == "remote_api"
        or int(spec.get("batch_size") or 1) > 1
    )
    confirmation_reason = ""
    if force_confirmation:
        confirmation_reason = "重试会再次生成并消耗算力或费用，需要先确认。"
    elif backend == "remote_api":
        confirmation_reason = "远程图片 API 可能产生费用，并会把提示词和参考图发送给第三方，请确认后继续。"
    elif int(spec.get("batch_size") or 1) > 1:
        confirmation_reason = "批量生成会增加耗时和资源消耗，需要先确认。"
    job_id = f"job_{uuid.uuid4().hex[:24]}"
    timestamp = db.now_iso()
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM creation_jobs WHERE idempotency_key=?", (key,)).fetchone()
        if existing is not None:
            return _job_public(existing) or {}, False
        reserve_slot(conn, conversation_id)
        conn.execute(
            """
            INSERT INTO creation_jobs(
                id, conversation_id, source, media_type, backend, workflow_id,
                provider_id, model_id, status, stage, progress, spec_json,
                preset_snapshot_json, confirmation_reason, idempotency_key,
                parent_job_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, conversation_id, source, spec["media_type"], backend, workflow_id,
                str(spec.get("provider_id") or ""), str(spec.get("model_id") or ""),
                "needs_confirmation" if needs_confirmation else "created",
                "needs_confirmation" if needs_confirmation else "created",
                _json(spec), _json(snapshot), confirmation_reason, key, parent_job_id,
                timestamp, timestamp,
            ),
        )
    if not needs_confirmation:
        schedule_job(job_id)
    return get_job(job_id) or {}, True


def retry_job(job_id: str) -> dict[str, Any]:
    row = _get_job_row(job_id)
    if row is None:
        raise ValueError("找不到要重试的任务。")
    if str(row["status"]) not in TERMINAL_STATUSES:
        raise ValueError("只有已结束的任务可以重试。")
    spec = _loads(row.get("spec_json"), {})
    if not isinstance(spec, dict):
        raise ValueError("原任务参数已经损坏。")
    from . import birefnet_service
    if row["workflow_id"] == birefnet_service.WORKFLOW_ID:
        return birefnet_service.retry_cutout(row, spec)
    # A completed local task stores runtime-only fields (selected fallback
    # workflow, effective seed, etc.) back into spec_json for diagnostics.
    # They are not part of CreationJobRequest and must not make a retry fail
    # the strict ``extra=forbid`` validation.
    request_fields = set(CreationJobRequest.model_fields)
    spec = {key: value for key, value in spec.items() if key in request_fields}
    spec["idempotency_key"] = f"retry:{job_id}:{uuid.uuid4().hex}"
    job, _ = create_job(
        CreationJobRequest.model_validate(spec),
        source=str(row.get("source") or "creation_page"),
        conversation_id=str(row.get("conversation_id") or ""),
        force_confirmation=True,
        parent_job_id=job_id,
    )
    if row["status"] == "unknown":
        _update_job(job["id"], confirmation_reason="原请求是否完成或扣费尚未知。请先核对供应商记录；确认重试可能再次生成并产生费用。")
        job = get_job(job["id"]) or job
    return job


def confirm_job(job_id: str) -> dict[str, Any]:
    row = _get_job_row(job_id)
    if row is None:
        raise ValueError("找不到创作任务。")
    if str(row["status"]) != "needs_confirmation":
        raise ValueError("这个任务当前不需要确认。")
    if _active_job_exists(excluding=job_id):
        raise ValueError("已有一项创作任务正在运行。")
    with db.get_conn() as conn:
        reserve_slot(conn, str(row.get("conversation_id") or ""), job_id)
        changed = conn.execute("UPDATE creation_jobs SET status='created',stage='confirmed',confirmation_reason='',updated_at=? WHERE id=? AND status='needs_confirmation'", (db.now_iso(), job_id))
        if not changed.rowcount:
            raise ValueError("任务状态已改变，请刷新。")
    schedule_job(job_id)
    return get_job(job_id) or {}


def schedule_job(job_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise RuntimeError("创作任务需要在正在运行的应用中启动。") from exc
    current = _tasks.get(job_id)
    if current is not None and not current.done():
        return
    task = loop.create_task(_run_job(job_id), name=f"creation:{job_id}")
    _tasks[job_id] = task
    task.add_done_callback(lambda _task, target=job_id: _tasks.pop(target, None))


async def resume_active_jobs() -> None:
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM creation_jobs WHERE status IN ('created','submitted','queued','running','cancel_requested') ORDER BY created_at"
            ).fetchall()
    except sqlite3.OperationalError as exc:
        # The lifespan unit tests intentionally replace initialize_runtime().
        # A missing optional table must not make application cleanup untestable,
        # while any other database error should still surface.
        if "no such table: creation_jobs" not in str(exc):
            raise
        return
    for row in rows:
        if row["backend"] == "remote_api" and row["status"] != "created":
            _mark_remote_unknown(str(row["id"]), "程序中断前已开始提交，请先查看供应商回执或历史记录。")
            continue
        schedule_job(str(row["id"]))


async def shutdown() -> None:
    tasks = [task for task in _tasks.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()


async def _run_job(job_id: str) -> None:
    with maintenance_service.mutation_scope():
        await _execute_job(job_id)


async def _execute_job(job_id: str) -> None:
    row = _get_job_row(job_id)
    if row is None or row["status"] in TERMINAL_STATUSES or db.conversation_deleted(str(row.get("conversation_id") or "")):
        return
    try:
        if str(row.get("backend")) == "comfyui":
            await _run_comfy_job(job_id, row)
        else:
            await _run_remote_job(job_id, row)
    except asyncio.CancelledError:
        current = _get_job_row(job_id) or {}
        if current.get("backend") == "remote_api" and _loads(current.get("spec_json"), {}).get("submission_pending"):
            _mark_remote_unknown(job_id, "本地请求已中止，无法据此断定远程任务被取消。")
        elif str(current.get("status")) == "cancel_requested":
            _update_job(job_id, status="cancelled", stage="cancelled", finished_at=db.now_iso())
        raise
    except asyncio.TimeoutError:
        current = _get_job_row(job_id) or {}
        if current.get("backend") == "remote_api" and _loads(current.get("spec_json"), {}).get("submission_pending"):
            _mark_remote_unknown(job_id, "等待响应超时，请核对原请求。")
            return
        _update_job(
            job_id, status="timed_out", stage="timed_out", error="生成任务超过允许时限。",
            finished_at=db.now_iso(),
        )
        _notify_chat_job_failure(job_id, "生成任务超过允许时限。")
    except Exception as exc:
        current = _get_job_row(job_id) or {}
        if current.get("backend") == "remote_api" and _loads(current.get("spec_json"), {}).get("submission_pending"):
            _mark_remote_unknown(job_id, str(exc))
            return
        _update_job(
            job_id, status="failed", stage="failed", error=str(exc)[:2000],
            finished_at=db.now_iso(),
        )
        _notify_chat_job_failure(job_id, str(exc)[:500])


def _chat_completion_allowed(row: Mapping[str, Any]) -> bool:
    """Only desktop chat channels receive asynchronous creation messages."""
    return bool(
        str(row.get("conversation_id") or "").strip()
        and str(row.get("source") or "") in {"desktop", "desktop_pet", "desktop_pet_call"}
    )


def _creation_output_attachments(job_id: str, outputs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for index, output in enumerate(outputs):
        media_type = str(output.get("media_type") or "image")
        attachments.append(
            {
                "kind": media_type,
                "name": _clean_name(str(output.get("name") or f"{media_type}-{index + 1}")),
                "mime_type": str(output.get("mime_type") or "application/octet-stream"),
                "size": max(0, int(output.get("size") or 0)),
                "sha256": str(output.get("sha256") or ""),
                "url": f"/api/creation/jobs/{job_id}/outputs/{index}",
                "creation_job_id": job_id,
            }
        )
    return attachments


def _notify_chat_job_completion(job_id: str, outputs: Iterable[Mapping[str, Any]]) -> None:
    row = _get_job_row(job_id)
    if row is None or not _chat_completion_allowed(row):
        return
    attachments = _creation_output_attachments(job_id, outputs)
    if not attachments:
        return
    media_label = "视频" if str(row.get("media_type") or "") == "video" else "图片"
    elapsed_seconds = _job_elapsed_seconds(row)
    local_generation = str(row.get("backend") or "") == "comfyui"
    cost_copy = "本地 ComfyUI 生成费用 ¥0.000000" if local_generation else "生成费用以供应商账单为准"
    db.save_message(
        role="assistant",
        content=(
            f"创作完成：已生成 1 份{media_label}，输出已经过文件校验。"
            f"生成耗时 {_duration_label(elapsed_seconds)}，{cost_copy}。"
        ),
        source="creation",
        conversation_id=str(row.get("conversation_id") or ""),
        request_id=f"creation:{job_id}",
        model_id=str(row.get("workflow_id") or row.get("model_id") or ""),
        provider_model=str(row.get("model_id") or ""),
        request_cost_yuan=0.0 if local_generation else None,
        request_cost_source="local_comfyui" if local_generation else "",
        attachments_json=_json(attachments),
        total_latency_ms=elapsed_seconds * 1000,
        delivery_key=f"creation-complete:{job_id}",
    )


def _notify_chat_job_failure(job_id: str, error: str) -> None:
    row = _get_job_row(job_id)
    if row is None or not _chat_completion_allowed(row):
        return
    clean_error = str(error or "创作失败").strip()[:500]
    elapsed_seconds = _job_elapsed_seconds(row)
    local_generation = str(row.get("backend") or "") == "comfyui"
    db.save_message(
        role="assistant",
        content=f"创作没有完成：{clean_error}（已运行 {_duration_label(elapsed_seconds)}）",
        source="creation",
        conversation_id=str(row.get("conversation_id") or ""),
        request_id=f"creation:{job_id}",
        model_id=str(row.get("workflow_id") or row.get("model_id") or ""),
        provider_model=str(row.get("model_id") or ""),
        request_cost_yuan=0.0 if local_generation else None,
        request_cost_source="local_comfyui" if local_generation else "",
        attachments_json="[]",
        total_latency_ms=elapsed_seconds * 1000,
        delivery_key=f"creation-failed:{job_id}",
    )


def _require_comfy_root() -> Path:
    root = settings.comfyui_root.expanduser().resolve()
    if not (root / "main.py").is_file():
        raise ValueError(f"找不到 ComfyUI：{root}")
    return root


def _client_kwargs(timeout: float | httpx.Timeout) -> dict[str, Any]:
    return {"timeout": timeout, "trust_env": False, "follow_redirects": False}


def _comfyui_launch_target() -> tuple[Path, Path, str, int]:
    root = _require_comfy_root()
    parsed = urlsplit(settings.comfyui_base_url)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("ComfyUI API 必须是无账号信息的本机 HTTP 地址。")
    host = parsed.hostname
    if host.casefold() != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("ComfyUI API 只能监听本机回环地址。")
        except ValueError as exc:
            if "只能监听" in str(exc):
                raise
            raise ValueError("ComfyUI API 只能监听本机回环地址。") from exc
    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise ValueError("ComfyUI API 端口无效。") from exc
    from .creation_custom import python_for
    python_executable = python_for(root)
    if python_executable is None:
        raise ValueError("已找到 ComfyUI，但未找到配套 Python；请先从 ComfyUI 启动器启动服务。")
    return root, python_executable, host, port


async def start_comfyui(*, wait_seconds: float = 8.0) -> dict[str, Any]:
    """Start the fixed local ComfyUI install without accepting commands or paths."""
    global _comfyui_start_process
    current = await comfyui_health()
    if current.get("reachable"):
        return {
            "ok": True,
            "started": False,
            "starting": False,
            "already_running": True,
            "message": "ComfyUI 已经连接。",
            "comfyui": current,
        }

    async with _comfyui_start_lock:
        current = await comfyui_health()
        if current.get("reachable"):
            return {
                "ok": True,
                "started": False,
                "starting": False,
                "already_running": True,
                "message": "ComfyUI 已经连接。",
                "comfyui": current,
            }

        if _comfyui_start_process is not None:
            if _comfyui_start_process.poll() is None:
                return {
                    "ok": True,
                    "started": False,
                    "starting": True,
                    "already_running": False,
                    "pid": _comfyui_start_process.pid,
                    "message": "ComfyUI 正在后台启动，页面会继续检查连接。",
                    "comfyui": current,
                }
            _comfyui_start_process = None

        root, python_executable, host, port = _comfyui_launch_target()
        log_dir = (settings.data_dir / "logs").resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "comfyui-startup.log"
        command = [
            str(python_executable),
            "main.py",
            "--listen",
            host,
            "--port",
            str(port),
            "--disable-auto-launch",
        ]
        log_stream = log_path.open("a", encoding="utf-8")
        try:
            process_kwargs: dict[str, Any] = {
                "cwd": str(root),
                "stdin": subprocess.DEVNULL,
                "stdout": log_stream,
                "stderr": subprocess.STDOUT,
                "shell": False,
                "close_fds": True,
            }
            if os.name == "nt":
                process_kwargs["creationflags"] = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            else:
                process_kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **process_kwargs)
            _comfyui_start_process = process
        finally:
            log_stream.close()

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1.0, min(120.0, float(wait_seconds)))
        last_health = current
        while loop.time() < deadline:
            if process.poll() is not None:
                _comfyui_start_process = None
                raise RuntimeError(
                    f"ComfyUI 启动后立即退出（代码 {process.returncode}），请查看日志：{log_path}"
                )
            await asyncio.sleep(0.75)
            last_health = await comfyui_health()
            if last_health.get("reachable"):
                _comfyui_start_process = None
                return {
                    "ok": True,
                    "started": True,
                    "starting": False,
                    "already_running": False,
                    "pid": process.pid,
                    "message": "ComfyUI 已启动并连接。",
                    "log_path": str(log_path),
                    "comfyui": last_health,
                }
        return {
            "ok": True,
            "started": True,
            "starting": True,
            "already_running": False,
            "pid": process.pid,
            "message": "ComfyUI 已在后台启动；加载自定义节点可能需要几分钟，页面会继续检查连接。",
            "log_path": str(log_path),
            "comfyui": last_health,
        }


async def comfyui_health(*, include_nodes: bool = False) -> dict[str, Any]:
    try:
        root = _require_comfy_root()
    except ValueError as exc:
        return {"ok": False, "reachable": False, "error": str(exc), "base_url": settings.comfyui_base_url}
    try:
        async with httpx.AsyncClient(**_client_kwargs(settings.comfyui_request_timeout_seconds)) as client:
            response = await client.get(f"{settings.comfyui_base_url}/system_stats")
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}")
            stats = response.json()
            node_count = 0
            if include_nodes:
                nodes = await client.get(f"{settings.comfyui_base_url}/object_info")
                nodes.raise_for_status()
                payload = nodes.json()
                node_count = len(payload) if isinstance(payload, dict) else 0
        return {
            "ok": True, "reachable": True, "base_url": settings.comfyui_base_url,
            "root": str(root), "node_count": node_count, "system": stats,
        }
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        return {
            "ok": False, "reachable": False, "base_url": settings.comfyui_base_url,
            "root": str(root), "error": f"ComfyUI 未运行或 API 不可用：{exc}",
        }


async def comfyui_preflight(workflow_id: str = "") -> dict[str, Any]:
    """Check the two approved workflows against the running ComfyUI install.

    This is intentionally separate from job creation: opening the creation
    page must explain missing models/custom nodes before a user spends time on
    a doomed task, while the actual task still performs the same validation at
    submission time.  No workflow is written or executed by this endpoint.
    """
    clean_id = str(workflow_id or "").strip()
    from .creation_custom import definitions as custom_definitions
    definitions = [*WORKFLOWS.values(), *custom_definitions()] if not clean_id else [require_workflow(clean_id)]
    try:
        root = _require_comfy_root()
    except ValueError as exc:
        root = Path(r"D:\AI\__mio_invalid_comfyui_root__")
        health = {"ok": False, "reachable": False, "error": str(exc), "base_url": settings.comfyui_base_url}
        reports = [inspect_workflow(root, item, object_info=None) for item in definitions]
        return {"ok": False, "checked": False, "comfyui": health, "workflows": reports}

    health = await comfyui_health(include_nodes=True)
    object_info: Mapping[str, Any] | None = None
    fetch_error = ""
    if health.get("reachable"):
        try:
            async with httpx.AsyncClient(**_client_kwargs(settings.comfyui_request_timeout_seconds)) as client:
                object_info = await _object_info(client)
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            fetch_error = f"无法读取 ComfyUI 节点清单：{exc}"
    reports = [inspect_workflow(root, item, object_info=object_info) for item in definitions]
    if fetch_error:
        for report in reports:
            report["status"] = "comfyui_unavailable"
            report.setdefault("errors", []).append(fetch_error)
    ok = bool(object_info is not None and reports and all(item.get("status") == "ready" for item in reports))
    return {
        "ok": ok,
        "checked": object_info is not None,
        "comfyui": health,
        "workflows": reports,
    }
async def bootstrap() -> dict[str, Any]:
    from .creation_custom import default_ids
    health = await comfyui_health()
    # ``workflow_catalog`` only needs to inspect files to report availability.
    # An invalid configured installation must not expose arbitrary directory
    # contents while rendering the built-in workflow availability.
    try:
        catalog_root = _require_comfy_root()
    except ValueError:
        catalog_root = Path(r"D:\AI\__mio_invalid_comfyui_root__")
    return {
        "comfyui": health,
        "workflows": workflow_catalog(catalog_root),
        "presets": list_presets(),
        "assets": list_assets(),
        "jobs": list_jobs(),
        "remote_providers": remote_providers(),
        "defaults": {
            **default_ids(),
            "batch_size": 1,
        },
    }


async def _upload_reference(client: httpx.AsyncClient, job_id: str, asset_id: str) -> str:
    asset = _require_asset_row(asset_id)
    path: Path = asset["resolved_path"]
    filename = f"{asset_id}{path.suffix.lower()}"
    with path.open("rb") as stream:
        response = await client.post(
            f"{settings.comfyui_base_url}/upload/image",
            files={"image": (filename, stream, str(asset["mime_type"]))},
            data={"subfolder": f"MioJobs/{job_id}", "type": "input", "overwrite": "false"},
        )
    response.raise_for_status()
    data = response.json()
    name = str(data.get("name") or filename)
    subfolder = str(data.get("subfolder") or f"MioJobs/{job_id}").strip("/\\")
    if ".." in Path(subfolder).parts:
        raise RuntimeError("ComfyUI 返回了非法素材路径。")
    return f"{subfolder}/{name}" if subfolder else name


async def _object_info(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get(f"{settings.comfyui_base_url}/object_info")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("ComfyUI object_info 不是对象。")
    return payload


async def _run_comfy_job(job_id: str, row: Mapping[str, Any]) -> None:
    if row.get("workflow_id") == "birefnet-portrait-png":
        from . import birefnet_service
        await birefnet_service.run_job(job_id, dict(row))
        return
    root = _require_comfy_root()
    spec = _loads(row.get("spec_json"), {})
    if not isinstance(spec, dict):
        raise ValueError("任务参数已经损坏。")
    workflow = require_workflow(str(row.get("workflow_id") or ""), str(row.get("media_type") or ""), include_disabled=True)
    client_id = str(row.get("client_id") or "") or f"mio_{uuid.uuid4().hex}"
    _update_job(job_id, status="cancel_requested" if row.get("status") == "cancel_requested" else "submitted", stage="validating", progress=0.01, client_id=client_id, started_at=str(row.get("started_at") or db.now_iso()))
    timeout_seconds = settings.creation_video_timeout_seconds if workflow.media_type == "video" else settings.creation_image_timeout_seconds
    await asyncio.wait_for(
        _run_comfy_job_inner(job_id, row, root, spec, workflow, client_id),
        timeout=timeout_seconds,
    )


async def _run_comfy_job_inner(
    job_id: str,
    row: Mapping[str, Any],
    root: Path,
    spec: dict[str, Any],
    workflow: Any,
    client_id: str,
) -> None:
        async with httpx.AsyncClient(**_client_kwargs(httpx.Timeout(settings.comfyui_request_timeout_seconds))) as client:
            prompt_id = str(row.get("prompt_id") or "")
            if not prompt_id and spec.get("submission_pending"):
                for endpoint in ("queue", "history"):
                    response = await client.get(f"{settings.comfyui_base_url}/{endpoint}")
                    response.raise_for_status()
                    payload = response.json()
                    records = [*payload.get("queue_running", []), *payload.get("queue_pending", [])] if endpoint == "queue" else [item.get("prompt", []) for item in payload.values() if isinstance(item, dict)]
                    matches = [record for record in records if len(record) > 3 and isinstance(record[3], dict) and record[3].get("client_id") == client_id]
                    if matches:
                        prompt_id = str(matches[0][1])
                        _update_job(job_id, prompt_id=prompt_id)
                        break
                if not prompt_id:
                    raise RuntimeError("上次提交结果未知，未找到 ComfyUI 回执。为避免重复生成已停止，请检查 ComfyUI 历史后明确重试。")
            if prompt_id:
                history = await _monitor_comfy_job(client, job_id, client_id, prompt_id)
                outputs = _validated_comfy_outputs(root, job_id, workflow.media_type, history)
                _update_job(job_id, status="completed", stage="completed", progress=1.0, outputs_json=_json(outputs), error="", finished_at=db.now_iso())
                _notify_chat_job_completion(job_id, outputs)
                return
            if row.get("status") == "cancel_requested":
                _update_job(job_id, status="cancelled", stage="cancelled", finished_at=db.now_iso())
                return
            object_info = await _object_info(client)
            uploaded_reference = ""
            reference_ids = list(spec.get("reference_asset_ids") or [])
            if workflow.requires_reference:
                if not reference_ids:
                    raise ValueError("此工作流需要先选择一张参考图。")
                _update_job(job_id, stage="uploading_reference", progress=0.03)
                uploaded_reference = await _upload_reference(client, job_id, str(reference_ids[0]))
            prompt, effective_spec = build_workflow_prompt(
                root, workflow, spec, job_id=job_id, object_info=object_info,
                uploaded_reference=uploaded_reference,
            )
            effective_spec["submission_pending"] = True
            _update_job(job_id, stage="submitting", progress=0.05, client_id=client_id, spec_json=_json(effective_spec))
            response = await client.post(
                f"{settings.comfyui_base_url}/prompt",
                json={"prompt": prompt, "client_id": client_id},
            )
            if response.status_code >= 400:
                raise RuntimeError(f"ComfyUI 拒绝任务（HTTP {response.status_code}）：{response.text[:800]}")
            result = response.json()
            prompt_id = str(result.get("prompt_id") or "")
            if not prompt_id:
                raise RuntimeError("ComfyUI 没有返回 prompt_id。")
            _update_job(job_id, status="queued", stage="queued", progress=0.08, prompt_id=prompt_id)
            history = await _monitor_comfy_job(client, job_id, client_id, prompt_id)
            outputs = _validated_comfy_outputs(root, job_id, workflow.media_type, history)
            _update_job(
                job_id, status="completed", stage="completed", progress=1.0,
                outputs_json=_json(outputs), error="", finished_at=db.now_iso(),
            )
            _notify_chat_job_completion(job_id, outputs)


async def _history_record(client: httpx.AsyncClient, prompt_id: str) -> dict[str, Any] | None:
    response = await client.get(f"{settings.comfyui_base_url}/history/{prompt_id}")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    record = payload.get(prompt_id)
    return record if isinstance(record, dict) else None


def _history_error(record: Mapping[str, Any]) -> str:
    status = record.get("status")
    if isinstance(status, Mapping) and status.get("status_str") == "error":
        messages = status.get("messages")
        return f"ComfyUI 执行失败：{_json(messages)[:1200]}"
    return ""


async def _monitor_comfy_job(
    client: httpx.AsyncClient,
    job_id: str,
    client_id: str,
    prompt_id: str,
) -> dict[str, Any]:
    ws_url = settings.comfyui_base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/ws?clientId={client_id}"
    with suppress(Exception):
        async with websockets.connect(ws_url, open_timeout=5, close_timeout=2, proxy=None) as socket:
            while True:
                current = _get_job_row(job_id) or {}
                if str(current.get("status")) == "cancel_requested":
                    await _cancel_comfy_prompt(client, prompt_id)
                    _update_job(job_id, status="cancelled", stage="cancelled", finished_at=db.now_iso())
                    raise asyncio.CancelledError
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    record = await _history_record(client, prompt_id)
                    if record is not None:
                        error = _history_error(record)
                        if error:
                            raise RuntimeError(error)
                        return record
                    continue
                if not isinstance(raw, str):
                    continue
                event = _loads(raw, {})
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "")
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                event_prompt = str(data.get("prompt_id") or "")
                if event_prompt and event_prompt != prompt_id:
                    continue
                if event_type in {"execution_start", "executing"}:
                    _update_job(job_id, status="running", stage="running", progress=max(0.1, float(current.get("progress") or 0)))
                    if event_type == "executing" and data.get("node") is None:
                        record = await _history_record(client, prompt_id)
                        if record is not None:
                            error = _history_error(record)
                            if error:
                                raise RuntimeError(error)
                            return record
                elif event_type == "progress":
                    value = float(data.get("value") or 0)
                    maximum = max(1.0, float(data.get("max") or 1))
                    _update_job(job_id, status="running", stage="sampling", progress=min(0.96, 0.1 + 0.85 * value / maximum))
                elif event_type in {"execution_error", "execution_interrupted"}:
                    raise RuntimeError(f"ComfyUI 执行中断：{_json(data)[:1200]}")

    while True:
        row = _get_job_row(job_id) or {}
        if str(row.get("status")) == "cancel_requested":
            await _cancel_comfy_prompt(client, prompt_id)
            _update_job(job_id, status="cancelled", stage="cancelled", finished_at=db.now_iso())
            raise asyncio.CancelledError
        record = await _history_record(client, prompt_id)
        if record is not None:
            error = _history_error(record)
            if error:
                raise RuntimeError(error)
            return record
        _update_job(job_id, status="running", stage="running", progress=max(0.1, float(row.get("progress") or 0)))
        await asyncio.sleep(2)


async def _cancel_comfy_prompt(client: httpx.AsyncClient, prompt_id: str) -> None:
    with suppress(httpx.HTTPError):
        await client.post(f"{settings.comfyui_base_url}/queue", json={"delete": [prompt_id]})
    response = await client.get(f"{settings.comfyui_base_url}/queue")
    response.raise_for_status()
    if any(len(item) > 1 and str(item[1]) == prompt_id for item in response.json().get("queue_running", [])):
        await client.post(f"{settings.comfyui_base_url}/interrupt", json={})


def _job_output_root(comfy_root: Path, job_id: str) -> Path:
    root = (comfy_root / "output" / "MioJobs" / job_id).resolve()
    output_root = (comfy_root / "output").resolve()
    if not root.is_relative_to(output_root):
        raise ValueError("任务输出目录超出 ComfyUI output。")
    return root


def _validate_image(path: Path) -> str:
    try:
        with Image.open(path) as image:
            image.verify()
            image_format = str(image.format or "").upper()
    except Exception as exc:
        raise RuntimeError(f"输出图片无法解码：{path.name}") from exc
    mime = Image.MIME.get(image_format) or mimetypes.guess_type(path.name)[0]
    if not mime or not mime.startswith("image/"):
        raise RuntimeError(f"输出图片格式无法识别：{path.name}")
    return mime


def _find_ffprobe() -> str:
    configured = str(settings.ffprobe_path or "").strip()
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("ffprobe") or ""


def _validate_video(path: Path) -> str:
    header = path.read_bytes()[:32]
    suffix = path.suffix.lower()
    valid_magic = (
        (suffix in {".mp4", ".mov"} and b"ftyp" in header)
        or (suffix == ".webm" and header.startswith(b"\x1aE\xdf\xa3"))
        or (suffix == ".mkv" and header.startswith(b"\x1aE\xdf\xa3"))
    )
    if not valid_magic:
        raise RuntimeError(f"输出视频容器与扩展名不一致：{path.name}")
    ffprobe = _find_ffprobe()
    if ffprobe:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(f"输出视频无法读取：{path.name}")
        parsed = _loads(result.stdout, {})
        try:
            duration = float(parsed["format"]["duration"])
        except (KeyError, TypeError, ValueError):
            duration = 0
        if duration <= 0:
            raise RuntimeError(f"输出视频时长无效：{path.name}")
    return mimetypes.guess_type(path.name)[0] or "video/mp4"


def _validated_comfy_outputs(
    comfy_root: Path,
    job_id: str,
    media_type: str,
    history: Mapping[str, Any],
) -> list[dict[str, Any]]:
    job_root = _job_output_root(comfy_root, job_id)
    output_root = (comfy_root / "output").resolve()
    discovered: list[Path] = []
    outputs = history.get("outputs")
    if isinstance(outputs, Mapping):
        for node_output in outputs.values():
            if not isinstance(node_output, Mapping):
                continue
            for records in node_output.values():
                if not isinstance(records, list):
                    continue
                for record in records:
                    if not isinstance(record, Mapping) or not record.get("filename"):
                        continue
                    if str(record.get("type") or "output") != "output":
                        continue
                    subfolder = str(record.get("subfolder") or "").strip("/\\")
                    candidate = (output_root / subfolder / _clean_name(str(record["filename"]))).resolve()
                    if candidate.is_relative_to(job_root) and candidate.is_file():
                        discovered.append(candidate)
    if not discovered and job_root.is_dir():
        discovered = [item.resolve() for item in job_root.rglob("*") if item.is_file()]
    selected: list[Path] = []
    expected_suffixes = IMAGE_SUFFIXES if media_type == "image" else VIDEO_SUFFIXES
    for path in discovered:
        if path.is_relative_to(job_root) and path.suffix.lower() in expected_suffixes and path not in selected:
            selected.append(path)
    if not selected:
        raise RuntimeError("ComfyUI 报告完成，但没有在本任务目录找到可验证输出。")
    result: list[dict[str, Any]] = []
    for path in selected:
        mime = _validate_image(path) if media_type == "image" else _validate_video(path)
        content = path.read_bytes()
        result.append({
            "path": str(path), "name": path.name, "media_type": media_type,
            "mime_type": mime, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        })
    return archive_outputs(job_id, result, job_root)


def archive_outputs(job_id: str, outputs: list[dict], source_root: Path) -> list[dict]:
    from . import artifact_service
    row = _get_job_row(job_id) or {}
    for output in outputs:
        if not output.get("artifact_id"):
            artifact = artifact_service.import_file(Path(output["path"]), source_root=source_root,
                producer="creation", expected_sha256=output["sha256"], job_id=job_id,
                conversation_id=str(row.get("conversation_id") or ""),
                name=output.get("name", ""), mime_type=output.get("mime_type", ""))
            output["artifact_id"] = artifact["id"]
    return outputs


def _image_urls_from_payload(value: Any) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            result_is_image = str(value.get("type") or "").casefold() in {
                "image_generation_call",
                "image_generation",
            }
            if key in {"b64_json", "base64", "image_base64"} and isinstance(item, str) and item:
                result.append(("base64", item))
            elif key == "result" and result_is_image and isinstance(item, str) and item:
                result.append(("base64", item))
            elif key in {"url", "image_url"} and isinstance(item, str) and item.startswith(("http://", "https://", "data:image/")):
                result.append(("url", item))
            else:
                result.extend(_image_urls_from_payload(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_image_urls_from_payload(item))
    return result


def _assert_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("远程图片返回了不安全的下载地址。")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))}
    except OSError as exc:
        raise ValueError("远程图片地址无法解析。") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("拒绝从本机、私网或保留地址下载远程图片。")


async def _remote_image_bytes(client: httpx.AsyncClient, kind: str, value: str) -> bytes:
    if kind == "base64":
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise RuntimeError("远程 API 返回了损坏的 base64 图片。") from exc
    if value.startswith("data:image/"):
        try:
            return base64.b64decode(value.split(",", 1)[1], validate=True)
        except (IndexError, ValueError, base64.binascii.Error) as exc:
            raise RuntimeError("远程 API 返回了损坏的 data URL 图片。") from exc
    _assert_public_url(value)
    response = await client.get(value)
    response.raise_for_status()
    if len(response.content) > settings.creation_remote_max_bytes:
        raise RuntimeError("远程图片超过允许大小。")
    return response.content


async def _run_remote_job(job_id: str, row: Mapping[str, Any]) -> None:
    spec = _loads(row.get("spec_json"), {})
    if not isinstance(spec, dict):
        raise ValueError("任务参数已经损坏。")
    if spec.get("submission_pending") or row.get("status") in {"submitted", "queued", "running", "cancel_requested"}:
        _mark_remote_unknown(job_id, "已有提交记录，不能再次自动请求生成。")
        return
    connection = _provider_connection(str(spec.get("provider_id") or ""))
    base_url = normalize_api_base_url(connection["base_url"])
    mode = str(spec.get("remote_api_mode") or "auto")
    candidates = [mode] if mode in {"images", "responses"} else ["images", "responses"]
    reference_ids = list(spec.get("reference_asset_ids") or [])
    reference_data_urls: list[str] = []
    for asset_id in reference_ids:
        asset = _require_asset_row(str(asset_id))
        reference_data_urls.append(
            f"data:{asset['mime_type']};base64,{base64.b64encode(asset['resolved_path'].read_bytes()).decode('ascii')}"
        )
    spec.update(submission_pending=True, submission_key=f"mio-creation-{job_id}")
    _update_job(job_id, status="submitted", stage="requesting_remote", progress=0.1,
                spec_json=_json(spec), client_id=spec["submission_key"], started_at=str(row.get("started_at") or db.now_iso()))
    errors: list[str] = []
    content = await asyncio.wait_for(
        _request_remote_image(job_id, connection, candidates, reference_data_urls, spec, errors),
        timeout=settings.creation_image_timeout_seconds,
    )
    if len(content) > settings.creation_remote_max_bytes:
        raise RuntimeError("远程图片超过允许大小。")
    output_root = (settings.creation_output_dir / job_id).resolve()
    if not output_root.is_relative_to(settings.creation_output_dir.resolve()):
        raise ValueError("远程输出路径超出授权目录。")
    output_root.mkdir(parents=True, exist_ok=True)
    temporary = output_root / "remote-image.bin"
    temporary.write_bytes(content)
    mime = _validate_image(temporary)
    suffix = mimetypes.guess_extension(mime) or ".png"
    if suffix == ".jpe":
        suffix = ".jpg"
    output = output_root / f"image{suffix}"
    temporary.replace(output)
    result = [{
        "path": str(output), "name": output.name, "media_type": "image", "mime_type": mime,
        "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
    }]
    archive_outputs(job_id, result, output_root)
    _update_job(job_id, status="completed", stage="completed", progress=1.0, outputs_json=_json(result), error="", finished_at=db.now_iso())
    _notify_chat_job_completion(job_id, result)


async def _request_remote_image(
    job_id: str,
    connection: Mapping[str, str],
    candidates: list[str],
    reference_data_urls: list[str],
    spec: Mapping[str, Any],
    errors: list[str],
) -> bytes:
    base_url = normalize_api_base_url(str(connection.get("base_url") or ""))
    if not base_url:
        raise ValueError("远程图片 API 未配置有效地址。")
    async with httpx.AsyncClient(**_client_kwargs(httpx.Timeout(settings.openai_timeout_seconds))) as client:
        data: dict[str, Any] | None = None
        for candidate in candidates:
            endpoint = f"{base_url}/{ 'images/generations' if candidate == 'images' else 'responses' }"
            if candidate == "images":
                payload: dict[str, Any] = {
                    "model": str(spec.get("model_id") or ""),
                    "prompt": str(spec.get("prompt") or ""),
                    "n": 1,
                    "response_format": "b64_json",
                }
                if spec.get("width") and spec.get("height"):
                    payload["size"] = f"{int(spec['width'])}x{int(spec['height'])}"
                payload_variants = [
                    payload,
                    {key: value for key, value in payload.items() if key != "response_format"},
                    {key: value for key, value in payload.items() if key not in {"response_format", "size"}},
                ]
            else:
                content: list[dict[str, Any]] = [{"type": "input_text", "text": str(spec.get("prompt") or "")}]
                content.extend({"type": "input_image", "image_url": item} for item in reference_data_urls)
                payload_variants = [{
                    "model": str(spec.get("model_id") or ""),
                    "input": [{"role": "user", "content": content}],
                    "tools": [{"type": "image_generation"}],
                }]
            for payload_variant in payload_variants:
                pending_spec = {**spec, "submission_pending": True}
                _update_job(job_id, spec_json=_json(pending_spec))
                response = await client.post(
                    endpoint,
                    headers={**auth_headers(connection["api_key"], connection["auth_scheme"]), "Content-Type": "application/json",
                             "Idempotency-Key": str(spec.get("submission_key") or f"mio-creation-{job_id}")},
                    json=payload_variant,
                )
                request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
                if request_id:
                    pending_spec["provider_request_id"] = request_id[:240]
                    _update_job(job_id, spec_json=_json(pending_spec))
                if response.status_code >= 500 or response.status_code in {408, 409}:
                    raise RemoteSubmissionUnknown(f"供应商返回 HTTP {response.status_code}，未取得确定结果。")
                if response.status_code >= 400:
                    _update_job(job_id, spec_json=_json({**pending_spec, "submission_pending": False}))
                    errors.append(f"{candidate} HTTP {response.status_code}: {response.text[:500]}")
                    continue
                content_type = response.headers.get("content-type", "").casefold()
                if content_type.startswith("image/"):
                    if len(response.content) > settings.creation_remote_max_bytes:
                        raise RuntimeError("远程图片超过允许大小。")
                    return response.content
                try:
                    parsed = response.json()
                except ValueError:
                    raise RemoteSubmissionUnknown("供应商返回成功状态，但响应不是可验证的图片或 JSON。") from None
                images = _image_urls_from_payload(parsed)
                if images:
                    data = parsed
                    break
                raise RemoteSubmissionUnknown("供应商返回成功状态，但没有提供可验证的图片结果。")
            if data is not None:
                break
        if data is None:
            raise RuntimeError("远程图片生成失败：" + "；".join(errors))
        _update_job(job_id, status="running", stage="downloading_output", progress=0.8)
        image_refs = _image_urls_from_payload(data)
        return await _remote_image_bytes(client, *image_refs[0])


async def cancel_job(job_id: str) -> dict[str, Any]:
    row = _get_job_row(job_id)
    if row is None:
        raise ValueError("找不到创作任务。")
    status = str(row.get("status") or "")
    if status == "needs_confirmation" or status == "created":
        _update_job(job_id, status="cancelled", stage="cancelled", finished_at=db.now_iso())
        task = _tasks.get(job_id)
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return get_job(job_id) or {}
    if status in TERMINAL_STATUSES:
        return get_job(job_id) or {}
    _update_job(job_id, status="cancel_requested", stage="cancel_requested")
    if str(row.get("backend")) == "comfyui" and row.get("prompt_id"):
        async with httpx.AsyncClient(**_client_kwargs(settings.comfyui_request_timeout_seconds)) as client:
            await _cancel_comfy_prompt(client, str(row["prompt_id"]))
    task = _tasks.get(job_id)
    if task is not None and not task.done():
        task.cancel()
        if task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
    if row.get("backend") == "remote_api" and _loads(row.get("spec_json"), {}).get("submission_pending"):
        _mark_remote_unknown(job_id, "已停止本地等待，请核对远程请求是否执行。")
    else:
        _update_job(job_id, status="cancelled", stage="cancelled", finished_at=db.now_iso())
    return get_job(job_id) or {}


def output_file(job_id: str, index: int) -> tuple[Path, str, str]:
    row = _get_job_row(job_id)
    if row is None or str(row.get("status")) != "completed":
        raise ValueError("任务尚未完成或不存在。")
    outputs = _loads(row.get("outputs_json"), [])
    if not isinstance(outputs, list) or index < 0 or index >= len(outputs) or not isinstance(outputs[index], dict):
        raise ValueError("找不到这个输出。")
    output = outputs[index]
    if output.get("artifact_id"):
        from . import artifact_service
        path, item = artifact_service.file(str(output["artifact_id"]))
        return path, item["mime_type"], item["name"]
    path = Path(str(output.get("path") or "")).resolve()
    allowed_roots = [
        (settings.comfyui_root / "output" / "MioJobs" / job_id).resolve(),
        (settings.creation_output_dir / job_id).resolve(),
    ]
    # Legacy output metadata is trusted local history, bound to this job and hash.
    # It can be adopted even after the configured ComfyUI installation changes.
    for parent in path.parents:
        if parent.name == job_id and ((parent.parent.name == "MioJobs" and parent.parent.parent.name == "output")
                                      or row.get("backend") == "remote_api"):
            allowed_roots.append(parent)
            break
    if not any(path.is_relative_to(root) for root in allowed_roots) or not path.is_file():
        raise ValueError("输出文件不在本任务的授权目录。")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != str(output.get("sha256") or ""):
        raise ValueError("输出文件校验失败，文件可能已被替换。")
    try:
        with maintenance_service.mutation_scope():
            archive_outputs(job_id, [output], next(root for root in allowed_roots if path.is_relative_to(root)))
            _update_job(job_id, outputs_json=_json(outputs))
    except maintenance_service.MaintenanceModeError:
        return path, str(output.get("mime_type") or "application/octet-stream"), str(output.get("name") or path.name)
    from . import artifact_service
    path, item = artifact_service.file(output["artifact_id"])
    return path, item["mime_type"], item["name"]


__all__ = [
    "asset_file", "bootstrap", "cancel_job", "comfyui_health", "comfyui_preflight", "confirm_job",
    "create_job", "delete_preset", "get_asset", "get_job", "list_assets", "list_jobs",
    "list_presets", "output_file", "remote_providers", "resume_active_jobs", "retry_job",
    "save_asset", "save_preset", "shutdown",
]
