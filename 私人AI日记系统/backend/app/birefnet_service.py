"""Local BiRefNet jobs with durable ComfyUI receipts and transparent PNG validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import httpx
from PIL import Image

from . import creation_service as creation, db
from .config import settings


WORKFLOW_ID = "birefnet-portrait-png"


async def health() -> dict:
    root = creation._require_comfy_root()
    model = root / "models/BiRefNet/BiRefNet-portrait"
    missing = [name for name in ("config.json", "birefnet.py", "BiRefNet_config.py", "model.safetensors") if not (model / name).is_file()]
    async with httpx.AsyncClient(**creation._client_kwargs(15)) as client:
        response = await client.get(settings.comfyui_base_url + "/object_info/BiRefNet_Hugo")
        response.raise_for_status()
        node_ready = "BiRefNet_Hugo" in response.json()
    return {"available": node_ready and not missing, "missing_files": missing, "node_ready": node_ready,
            "model": "BiRefNet-portrait", "outputs": "RGBA PNG / PNG sequence", "cost_yuan": 0}


def input_file(*, asset_id: str = "", job_id: str = "", output_index: int = 0, path: str = "") -> Path:
    if sum(bool(value) for value in (asset_id, job_id, path)) != 1:
        raise ValueError("必须提供且只提供一个素材 ID、已完成任务 ID 或授权文件路径。")
    if asset_id:
        source = creation.asset_file(asset_id)[0]
    elif job_id:
        source = creation.output_file(job_id, output_index)[0]
    else:
        from .agent_file_service import resolve_read
        source = resolve_read(path)
    if source.suffix.lower() not in creation.IMAGE_SUFFIXES | creation.VIDEO_SUFFIXES:
        raise ValueError("抠图输入必须是图片或视频。")
    return source


def create_cutout(arguments: dict, context) -> dict:
    source = input_file(**{key: arguments.get(key, default) for key, default in
                          (("asset_id", ""), ("job_id", ""), ("output_index", 0), ("path", ""))})
    digest = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()
    key = f"cutout:{context.task_id or context.run_id}:{context.source_message_id}:{digest}"
    with db.get_conn() as conn:
        old = conn.execute("SELECT id FROM creation_jobs WHERE idempotency_key=?", (key,)).fetchone()
    if old:
        return {"job": creation.get_job(old[0]), "created": False}
    job_id = "job_" + uuid.uuid4().hex[:24]
    root = creation._require_comfy_root()
    target = (root / "input" / f"mio-{job_id}{source.suffix.lower()}").resolve()
    shutil.copyfile(source, target)
    video = source.suffix.lower() in creation.VIDEO_SUFFIXES
    spec = {"input": target.name, "input_kind": "video" if video else "image",
            "frame_limit": arguments.get("frame_limit", 240), "batch_frames": 8,
            "skip_frames": arguments.get("skip_frames", 0), "processed_frames": 0,
            "batch_prompt_ids": [], "quality_status": "not_reviewed", "prompt": "BiRefNet 本地透明抠图"}
    now = db.now_iso()
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM creation_jobs WHERE idempotency_key=?", (key,)).fetchone()
        if old:
            return {"job": creation._job_public(old), "created": False}
        creation.reserve_slot(conn, context.conversation_id)
        conn.execute("""INSERT INTO creation_jobs(id,conversation_id,source,media_type,backend,workflow_id,
            provider_id,model_id,status,stage,progress,spec_json,preset_snapshot_json,confirmation_reason,
            idempotency_key,parent_job_id,created_at,updated_at)
            VALUES(?,?,?,?,'comfyui',?,'','BiRefNet-portrait','created','created',0,?,'{}','',?,'',?,?)""",
            (job_id, context.conversation_id, context.source,
             'video' if video else 'image', WORKFLOW_ID,
             json.dumps(spec, ensure_ascii=False), key, now, now))
    creation.schedule_job(job_id)
    return {"job": creation.get_job(job_id), "created": True}


def retry_cutout(row: dict, spec: dict) -> dict:
    root = creation._require_comfy_root()
    source = (root / "input" / str(spec.get("input") or "")).resolve()
    if not source.is_relative_to((root / "input").resolve()) or not source.is_file():
        raise ValueError("原抠图输入不存在，无法重试。")
    job_id = "job_" + uuid.uuid4().hex[:24]
    target = root / "input" / f"mio-{job_id}{source.suffix}"
    shutil.copyfile(source, target)
    clean = {key: value for key, value in spec.items() if key in {"input_kind", "frame_limit", "batch_frames", "skip_frames", "prompt"}}
    clean.update(input=target.name, processed_frames=0, batch_prompt_ids=[], quality_status="not_reviewed")
    with db.get_conn() as conn:
        creation.reserve_slot(conn, row["conversation_id"])
        conn.execute("""INSERT INTO creation_jobs(id,conversation_id,source,media_type,backend,workflow_id,
            provider_id,model_id,status,stage,spec_json,preset_snapshot_json,confirmation_reason,
            idempotency_key,parent_job_id,created_at,updated_at)
            VALUES(?,?,?,?,'comfyui',?,'','BiRefNet-portrait','needs_confirmation','needs_confirmation',?,'{}',?,?,?, ?,?)""",
            (job_id, row["conversation_id"], row["source"], row["media_type"], WORKFLOW_ID,
             json.dumps(clean, ensure_ascii=False), "从原始输入重新抠图，重做全部指定帧，需要确认。",
             f"retry:{row['id']}:{uuid.uuid4().hex}", row["id"], db.now_iso(), db.now_iso()))
    return creation.get_job(job_id)


def _prompt(root: Path, job_id: str, spec: dict, count: int, offset: int) -> dict:
    if spec["input_kind"] == "video":
        source = {"class_type": "VHS_LoadVideo", "inputs": {"video": spec["input"], "force_rate": 0,
            "custom_width": 0, "custom_height": 0, "frame_load_cap": count,
            "skip_first_frames": spec["skip_frames"] + offset, "select_every_nth": 1, "format": "AnimateDiff"}}
    else:
        source = {"class_type": "LoadImage", "inputs": {"image": spec["input"]}}
    return {
        "1": source,
        "2": {"class_type": "BiRefNet_Hugo", "inputs": {"image": ["1", 0],
            "model": "ZhengPeng7/BiRefNet-portrait", "load_local_model": True,
            "local_model_path": str(root / "models/BiRefNet/BiRefNet-portrait"),
            "background_color_name": "transparency", "device": "auto"}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0],
            "filename_prefix": f"MioJobs/{job_id}/frame-{offset:06d}"}},
    }


async def run_job(job_id: str, row: dict) -> None:
    await asyncio.wait_for(_run(job_id, row), timeout=settings.creation_video_timeout_seconds)


async def _run(job_id: str, row: dict) -> None:
    report = await health()
    if not report["available"]:
        raise ValueError("BiRefNet 模型或节点未就绪：" + json.dumps(report, ensure_ascii=False))
    root = creation._require_comfy_root()
    spec = json.loads(row["spec_json"])
    total = int(spec["frame_limit"]) if spec["input_kind"] == "video" else 1
    offset = int(spec.get("processed_frames", 0))
    client_id = str(row.get("client_id") or "") or "mio_" + uuid.uuid4().hex
    outputs = json.loads(row.get("outputs_json") or "[]")
    creation._update_job(job_id, status="running", stage="cutout", client_id=client_id,
                         started_at=row.get("started_at") or db.now_iso())
    async with httpx.AsyncClient(**creation._client_kwargs(30)) as client:
        while offset < total:
            count = min(int(spec["batch_frames"]), total - offset)
            prompt_id = str(row.get("prompt_id") or "")
            if not prompt_id:
                prompt = _prompt(root, job_id, spec, count, offset)
                # Persist a stable client identity before submission. A crash without a prompt receipt
                # is surfaced for inspection rather than blindly submitting a duplicate.
                if spec.get("submission_pending"):
                    raise RuntimeError("上次提交结果未知，请检查 ComfyUI 队列后重试该批次。")
                spec["submission_pending"] = True
                creation._update_job(job_id, spec_json=json.dumps(spec, ensure_ascii=False))
                response = await client.post(settings.comfyui_base_url + "/prompt", json={"prompt": prompt, "client_id": client_id})
                response.raise_for_status()
                prompt_id = response.json().get("prompt_id", "")
                if not prompt_id:
                    raise RuntimeError("ComfyUI 未返回任务回执。")
                spec["submission_pending"] = False
                spec["batch_prompt_ids"].append(prompt_id)
                creation._update_job(job_id, prompt_id=prompt_id, spec_json=json.dumps(spec, ensure_ascii=False))
            history = await creation._monitor_comfy_job(client, job_id, client_id, prompt_id)
            batch = creation._validated_comfy_outputs(root, job_id, "image", history)
            new_files = [item for item in batch if item["path"] not in {out["path"] for out in outputs}]
            if not new_files:
                raise RuntimeError("当前批次没有新增透明帧。")
            for item in new_files:
                with Image.open(item["path"]) as image:
                    if image.mode != "RGBA":
                        raise RuntimeError("抠图结果缺少 Alpha 通道。")
                    extrema = image.getchannel('A').getextrema()
                    if extrema[0] == 255 or extrema[1] == 0:
                        raise RuntimeError("抠图透明度异常，需要人工检查。")
                    item["alpha_range"] = list(extrema)
                    item["quality_status"] = "not_reviewed"
            outputs.extend(new_files)
            offset += len(new_files)
            spec["processed_frames"] = offset
            creation._update_job(job_id, prompt_id="", progress=min(.99, offset / total),
                outputs_json=json.dumps(outputs, ensure_ascii=False), spec_json=json.dumps(spec, ensure_ascii=False))
            row = {**row, "prompt_id": ""}
            if spec["input_kind"] == "image" or len(new_files) < count:
                break
    creation._update_job(job_id, status="completed", stage="completed", progress=1,
        outputs_json=json.dumps(outputs, ensure_ascii=False), finished_at=db.now_iso(), error="")
    creation._notify_chat_job_completion(job_id, outputs)
