from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import re
import secrets
import threading
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .config import save_runtime_settings, settings


PARAMETERS = {
    "prompt", "negative_prompt", "width", "height", "steps", "cfg", "seed",
    "batch_size", "duration_seconds", "fps", "reference_image",
}
_lock = threading.RLock()


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1, max_length=100)
    input: str = Field(min_length=1, max_length=200)


class WorkflowImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=100)
    media_type: Literal["image", "video"] = "image"
    prompt: dict[str, Any]
    bindings: dict[str, list[Binding]] = Field(default_factory=dict)


class WorkflowPreview(BaseModel):
    prompt: dict[str, Any]


def prepare_import(graph: dict, object_info: dict | None = None) -> dict:
    from .creation_workflows import ui_workflow_to_api_prompt
    if len(json.dumps(graph, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 2_000_000:
        raise ValueError("工作流不能超过 2 MB。")
    canvas = isinstance(graph.get("nodes"), list)
    if canvas and object_info is None:
        raise ValueError("普通画布工作流需要连接 ComfyUI 读取节点定义，请先启动并连接后重新选择文件。")
    prompt = ui_workflow_to_api_prompt(graph, object_info=object_info) if canvas else copy.deepcopy(graph)
    if not prompt or any(not isinstance(n, dict) or not isinstance(n.get("inputs"), dict) or not isinstance(n.get("class_type"), str) for n in prompt.values()):
        raise ValueError("请选择 ComfyUI 工作流 JSON 或 API JSON。")
    notes = []
    source_inputs = []
    cutout = any(node["class_type"] == "BiRefNet_Hugo" for node in prompt.values())
    for node_id, node in prompt.items():
        if node["class_type"] == "PreviewImage" or (cutout and node["class_type"] == "VHS_VideoCombine"):
            node["class_type"] = "SaveImage"
            node["inputs"] = {"images": node["inputs"]["images"], "filename_prefix": "Mio"}
            notes.append("预览/抠图视频输出将保存为 PNG；多帧输出为 PNG 序列，保留透明通道。")
        if node["class_type"] == "VHS_LoadVideo":
            descriptor = (object_info or {}).get("VHS_LoadVideo", {}).get("input", {}).get("required", {}).get("video", [])
            options = descriptor[0] if descriptor and isinstance(descriptor[0], list) else []
            source_inputs.append({"node_id":node_id, "input":"video", "label":"源视频", "options":options})
            notes.append("选择 ComfyUI 中已有的源视频。若列表中没有目标文件，请先用 ComfyUI 上传视频，再重新选择此工作流。")
    media_type = "video" if any(n["class_type"] in {"SaveVideo", "VHS_VideoCombine"} for n in prompt.values()) else "image"
    bindings = {}
    def bind(parameter, node_id, name):
        bindings.setdefault(parameter, []).append({"node_id": node_id, "input": name})
    def ancestors(node_id, seen=None):
        seen = set() if seen is None else seen
        if node_id not in prompt or node_id in seen:
            return seen
        seen.add(node_id)
        for value in prompt[node_id]["inputs"].values():
            if isinstance(value, list) and len(value) == 2:
                ancestors(str(value[0]), seen)
        return seen
    positive, negative = set(), set()
    for node in prompt.values():
        for name, value in node["inputs"].items():
            if name in {"positive", "negative", "prompt"} and isinstance(value, list):
                (negative if name == "negative" else positive).update(ancestors(str(value[0])))
    for node_id, node in prompt.items():
        for name, value in node["inputs"].items():
            if isinstance(value, str):
                if node["class_type"] == "LoadImage" and name == "image":
                    bind("reference_image", node_id, name)
                elif (name in {"text", "value", "string", "prompt", "positive_prompt", "negative_prompt"} and node_id in positive | negative) or name in {"prompt", "positive_prompt", "negative_prompt"}:
                    if node_id in negative and node_id not in positive or name == "negative_prompt":
                        bind("negative_prompt", node_id, name)
                    elif node_id not in negative:
                        bind("prompt", node_id, name)
            elif type(value) in {int, float}:
                parameter = {"noise_seed": "seed", "frame_rate": "fps"}.get(name, name)
                if parameter in PARAMETERS - {"prompt", "negative_prompt", "reference_image"}:
                    bind(parameter, node_id, name)
    validate_prompt(prompt, bindings, media_type)
    return {"prompt": prompt, "bindings": bindings, "media_type": media_type, "format": "canvas" if canvas else "api", "notes": list(dict.fromkeys(notes)), "source_inputs":source_inputs}


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comfyui_root: str = Field(min_length=1, max_length=1000)
    comfyui_base_url: str = Field(min_length=1, max_length=500)


class WorkflowDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_workflow_id: str = Field(min_length=1, max_length=100)
    video_workflow_id: str = Field(min_length=1, max_length=100)


def storage_root() -> Path:
    return (settings.data_dir / "creation_workflows").resolve()


def _path(workflow_id: str, suffix: str = ".json") -> Path:
    if not re.fullmatch(r"custom-[a-f0-9]{32}", workflow_id):
        raise ValueError("自定义工作流 ID 无效。")
    base = storage_root()
    path = (base / (workflow_id + suffix)).resolve()
    if not path.is_relative_to(base):
        raise ValueError("工作流路径超出数据目录。")
    return path


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def definitions(*, include_disabled: bool = False) -> list:
    from .creation_workflows import WorkflowDefinition

    result = []
    for path in sorted(storage_root().glob("custom-*.meta.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.pop("enabled", True) or include_disabled:
                result.append(WorkflowDefinition(**record))
        except (OSError, ValueError, TypeError):
            continue
    return result


def default_ids() -> dict[str, str]:
    return {
        "image_workflow_id": settings.creation_image_workflow_id,
        "video_workflow_id": settings.creation_video_workflow_id,
    }


def save_defaults(payload: WorkflowDefaults) -> dict[str, str]:
    from .creation_workflows import require_workflow

    with _lock:
        for media in ("image", "video"):
            require_workflow(getattr(payload, f"{media}_workflow_id"), media)
        save_runtime_settings({f"creation_{key}": value for key, value in payload.model_dump().items()})
    return default_ids()


def validate_prompt(prompt: dict, bindings: dict, media_type: str) -> None:
    if "nodes" in prompt or not prompt or len(prompt) > 1000:
        raise ValueError("请选择 ComfyUI 导出的 API 格式 JSON（节点包含 class_type 和 inputs）。")
    if len(json.dumps(prompt, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 2_000_000:
        raise ValueError("工作流不能超过 2 MB。")
    for node_id, node in prompt.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise ValueError(f"节点 {node_id} 缺少 class_type 或 inputs。")
        for value in node["inputs"].values():
            if isinstance(value, list) and (len(value) != 2 or str(value[0]) not in prompt or type(value[1]) is not int or value[1] < 0):
                raise ValueError(f"节点 {node_id} 包含无效连接。")
    outputs = {"SaveImage"} if media_type == "image" else {"SaveVideo", "VHS_VideoCombine"}
    if not any(n["class_type"] in outputs for n in prompt.values()):
        raise ValueError("图片工作流需要 SaveImage；视频工作流需要 SaveVideo 或 VHS_VideoCombine。")
    reachable = set()
    pending = [key for key, node in prompt.items() if node["class_type"] in outputs]
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(str(value[0]) for value in prompt[node_id]["inputs"].values() if isinstance(value, list))
    occupied = set()
    for parameter, targets in bindings.items():
        if parameter not in PARAMETERS or len(targets) > 50:
            raise ValueError(f"不支持的参数绑定：{parameter}")
        for target in targets:
            node_id, name = target["node_id"], target["input"]
            if node_id not in reachable:
                raise ValueError(f"参数 {parameter} 绑定的节点未连接到输出。")
            inputs = prompt.get(node_id, {}).get("inputs", {})
            if name not in inputs or isinstance(inputs[name], (list, dict)):
                raise ValueError(f"参数 {parameter} 必须绑定到现有的直接输入：{node_id}.{name}")
            if (node_id, name) in occupied:
                raise ValueError("同一个节点输入不能绑定多个参数。")
            occupied.add((node_id, name))
            value = inputs[name]
            numeric = parameter not in {"prompt", "negative_prompt", "reference_image"}
            if (numeric and (type(value) not in {int, float})) or (not numeric and not isinstance(value, str)):
                raise ValueError(f"参数 {parameter} 与 {node_id}.{name} 的数据类型不匹配。")
            if name in {"filename_prefix", "save_output"}:
                raise ValueError("输出目录由 Mio 按任务隔离，不允许绑定。")


def import_workflow(payload: WorkflowImport) -> dict:
    from .creation_workflows import WorkflowDefinition

    bindings = {key: [item.model_dump() for item in value] for key, value in payload.bindings.items() if value}
    validate_prompt(payload.prompt, bindings, payload.media_type)
    with _lock:
        if len(definitions()) >= 100:
            raise ValueError("最多启用 100 个自定义工作流，请先移除不用的工作流。")
        workflow_id = f"custom-{uuid.uuid4().hex}"
        path = _path(workflow_id)
        _write_json(path, payload.prompt)
        values = {}
        ranges = {"width": (256, 4096), "height": (256, 4096), "steps": (1, 150), "cfg": (0, 30), "fps": (1, 60), "duration_seconds": (1, 30)}
        for name, (minimum, maximum) in ranges.items():
            if bindings.get(name):
                target = bindings[name][0]
                value = payload.prompt[target["node_id"]]["inputs"][target["input"]]
                if minimum <= value <= maximum:
                    values[f"default_{name}"] = value
        definition = WorkflowDefinition(
            id=workflow_id, label=payload.label.strip(), media_type=payload.media_type,
            filename=path.name, description="自定义 API 工作流", custom=True, bindings=bindings,
            requires_reference=bool(bindings.get("reference_image")),
            expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), **values,
        )
        from dataclasses import asdict
        _write_json(_path(workflow_id, ".meta.json"), {**asdict(definition), "enabled": True})
    return definition.public_dict(settings.comfyui_root)


def remove_workflow(workflow_id: str) -> dict:
    with _lock:
        path = _path(workflow_id, ".meta.json")
        if workflow_id in default_ids().values():
            raise ValueError("请先切换默认工作流，再移除这一项。")
        if not path.is_file():
            raise ValueError("找不到自定义工作流。")
        record = json.loads(path.read_text(encoding="utf-8"))
        # Archive the source so queued jobs and their download receipts stay valid.
        record["enabled"] = False
        _write_json(path, record)
    return {"ok": True}


def inspect_custom(root: Path, definition: Any, object_info: dict | None) -> dict:
    from .creation_workflows import _workflow_file_metadata

    metadata = _workflow_file_metadata(root, definition)
    errors, missing = [], []
    try:
        prompt = read_prompt(definition)
        validate_prompt(prompt, definition.bindings, definition.media_type)
        if object_info is not None:
            missing = sorted({node["class_type"] for node in prompt.values()} - object_info.keys())
            for node_id, node in prompt.items():
                if object_info.get(node["class_type"], {}).get("api_node"):
                    errors.append(f"节点 {node_id} 是远程 API 节点，不能通过本地免费生成入口执行。")
                schema = object_info.get(node["class_type"], {}).get("input", {})
                for name, spec in schema.get("required", {}).items():
                    from .creation_workflows import _input_present
                    if not _input_present(node["inputs"], name):
                        errors.append(f"节点 {node_id} 缺少必填输入 {name}")
                for group in ("required", "optional"):
                    for name, spec in schema.get(group, {}).items():
                        if any(target["node_id"] == node_id and target["input"] == name for target in definition.bindings.get("reference_image", [])):
                            continue
                        value = node["inputs"].get(name)
                        options = spec[0] if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], list) else (spec[1].get("options") if isinstance(spec, (list, tuple)) and len(spec) > 1 and spec[0] == "COMBO" and isinstance(spec[1], dict) else None)
                        if value is not None and not isinstance(value, list) and isinstance(options, list) and value not in options:
                            errors.append(f"节点 {node_id} 的 {name} 不可用：{value}")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    return {
        "id": definition.id, "label": definition.label, "filename": definition.filename,
        "media_type": definition.media_type, "requires_reference": definition.requires_reference,
        "file": metadata, "errors": errors, "models": [],
        "nodes": {"missing": missing, "checked": object_info is not None},
        "status": "needs_setup" if errors or missing else "comfyui_unavailable" if object_info is None else "ready",
    }


def read_prompt(definition: Any) -> dict:
    content = _path(definition.id).read_bytes()
    if hashlib.sha256(content).hexdigest() != definition.expected_sha256:
        raise ValueError("自定义工作流文件已变化，请重新导入。")
    return json.loads(content)


def build_custom(root: Path, definition: Any, spec: dict, *, job_id: str, object_info: dict | None, uploaded_reference: str) -> tuple[dict, dict]:
    report = inspect_custom(root, definition, object_info)
    if report["errors"] or report["nodes"]["missing"]:
        raise ValueError("；".join(report["errors"] + [f"缺少节点：{name}" for name in report["nodes"]["missing"]]))
    prompt = read_prompt(definition)
    effective = copy.deepcopy(dict(spec))
    for name, value in definition.public_dict(root)["defaults"].items():
        effective.setdefault(name, value)
    effective.setdefault("batch_size", 1)
    effective.setdefault("negative_prompt", "")
    if int(effective.get("seed", -1)) < 0:
        effective["seed"] = secrets.randbelow(0x7FFFFFFFFFFFFFFF)
    for parameter, targets in definition.bindings.items():
        value = uploaded_reference if parameter == "reference_image" else effective.get(parameter)
        if parameter == "reference_image" and not value:
            raise ValueError("此工作流需要一张参考图。")
        for target in targets:
            prompt[target["node_id"]]["inputs"][target["input"]] = value
    for node in prompt.values():
        if "filename_prefix" in node["inputs"] or node["class_type"] in {"SaveImage", "SaveVideo", "VHS_VideoCombine"}:
            node["inputs"]["filename_prefix"] = f"MioJobs/{job_id}/{definition.media_type}"
        if node["class_type"] == "VHS_VideoCombine":
            node["inputs"]["save_output"] = True
    from .creation_workflows import workflow_execution_snapshot
    effective["workflow_snapshot"] = workflow_execution_snapshot(root, definition, prompt, runtime_workflow_filename=definition.filename)
    return prompt, effective


def validate_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
        port = parsed.port if parsed.port is not None else 80
    except ValueError:
        loopback, port = False, 0
    if parsed.scheme != "http" or not loopback or not port or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("请输入本机 ComfyUI HTTP 地址，例如 http://127.0.0.1:8188。")
    return value.strip().rstrip("/")


def normalize_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not (root / "main.py").is_file() and (root / "ComfyUI" / "main.py").is_file():
        root = root / "ComfyUI"
    if not (root / "main.py").is_file() or not (root / "folder_paths.py").is_file():
        raise ValueError(f"未找到完整的 ComfyUI 程序目录：{root}")
    return root


def python_for(root: Path) -> Path | None:
    candidates = [root / "python/python.exe", root / ".venv/Scripts/python.exe", root / "venv/Scripts/python.exe", root / ".venv/bin/python", root.parent / "python_embeded/python.exe", root.parent / "python_embedded/python.exe"]
    return next((path.resolve() for path in candidates if path.is_file()), None)


def environment_config() -> dict:
    return {"comfyui_root": str(settings.comfyui_root), "comfyui_base_url": settings.comfyui_base_url}


def save_environment(payload: EnvironmentConfig) -> dict:
    root = normalize_root(payload.comfyui_root)
    base_url = validate_url(payload.comfyui_base_url)
    save_runtime_settings({"comfyui_root": str(root), "comfyui_base_url": base_url})
    return environment_config()


def discover_comfyui() -> dict:
    candidates = [settings.comfyui_root]
    home = Path.home()
    parents = [home / name for name in ("Desktop", "Downloads", "Documents")]
    parents += [Path(os.environ.get("LOCALAPPDATA", str(home))) / "Programs", home / "AppData/Local/ComfyUI"]
    for drive in "CDEFG":
        base = Path(f"{drive}:/")
        parents.extend([base, base / "AI", base / "实用工具", base / "Tools"])
    # Inspect only common installation parents, never recurse through model trees.
    for parent in parents:
        try:
            for index, child in enumerate(parent.iterdir()):
                if index >= 300:
                    break
                if "comfy" in child.name.casefold() and child.is_dir():
                    candidates.append(child)
        except OSError:
            continue
    found, seen = [], set()
    for candidate in candidates:
        try:
            root = normalize_root(candidate)
            if str(root).casefold() in seen:
                continue
            seen.add(str(root).casefold())
            python = python_for(root)
            found.append({"root": str(root), "installed": True, "launchable": bool(python), "python": str(python or "")})
        except (OSError, ValueError):
            continue
    return {"candidates": found, "scope": "configured_and_common_directories"}
