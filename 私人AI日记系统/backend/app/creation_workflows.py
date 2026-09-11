from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .creation_lora_catalog import IMAGE_OPTIONAL_LORAS, IMAGE_REQUIRED_LORAS


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    label: str
    media_type: str
    filename: str
    description: str
    requires_reference: bool = False
    default_width: int = 768
    default_height: int = 1152
    default_steps: int = 28
    default_cfg: float = 4.0
    default_duration_seconds: float = 5.0
    default_fps: int = 24
    expected_sha256: str = ""
    custom: bool = False
    bindings: dict = field(default_factory=dict)

    def public_dict(self, root: Path) -> dict[str, object]:
        path = workflow_path(root, self)
        metadata = _workflow_file_metadata(root, self)
        return {
            "id": self.id,
            "filename": self.filename,
            "custom": self.custom,
            "bindings": self.bindings,
            "label": self.label,
            "media_type": self.media_type,
            "description": self.description,
            "requires_reference": self.requires_reference,
            "available": path.is_file(),
            "missing_reason": "" if path.is_file() else f"缺少固定工作流：{self.filename}",
            "file": metadata,
            "expected_sha256": self.expected_sha256,
            "defaults": {
                "width": self.default_width,
                "height": self.default_height,
                "steps": self.default_steps,
                "cfg": self.default_cfg,
                "duration_seconds": self.default_duration_seconds,
                "fps": self.default_fps,
            },
        }


WORKFLOWS: dict[str, WorkflowDefinition] = {
    "anima-2.9b-image": WorkflowDefinition(
        id="anima-2.9b-image",
        label="Anima 2.9B 图片",
        media_type="image",
        filename="无敌图片.json",
        description="本地 Anima 2.9B 文生图，一次默认生成一张。",
        default_width=864,
        default_height=1536,
        default_steps=4,
        default_cfg=1.0,
        expected_sha256="4ba5202a9dc185efd230d45be4175648754bc44bb7dff4d76f8cd1e018aaa7ee",
    ),
    "minimax-h3-video": WorkflowDefinition(
        id="minimax-h3-video",
        label="MiniMax H3 视频",
        media_type="video",
        filename="黑鹤.json",
        description="本地 MiniMax H3 图生视频，需要一张参考图。",
        requires_reference=True,
        default_width=576,
        default_height=1024,
        default_steps=6,
        default_cfg=1.0,
        expected_sha256="1b6a89f2bb8fbb7c845ec50df320c266f9b4da39d78947fc973ca76ed072a749",
    ),
}


UI_ONLY_NODE_TYPES = {
    "Label (rgthree)",
    "Note",
    "MarkdownNote",
    # rgthree's group toggles only control the editor and have no executable
    # inputs/outputs in the API prompt.  They are absent from some ComfyUI
    # object_info payloads, so treating them as runtime nodes is incorrect.
    "Fast Groups Bypasser (rgthree)",
}


# Model-bearing widgets used by the two approved workflows.  The values in a
# saved ComfyUI canvas are relative to one of these model directories; the
# adapter never accepts an arbitrary absolute path.
MODEL_INPUTS: dict[str, tuple[tuple[str, str], ...]] = {
    "UNETLoader": (("unet_name", "unet"),),
    "UnetLoaderGGUF": (("unet_name", "gguf"),),
    "CLIPLoader": (("clip_name", "clip"),),
    "CLIPLoaderGGUF": (("clip_name", "clip"),),
    "VAELoader": (("vae_name", "vae"),),
    "UpscaleModelLoader": (("model_name", "upscale_models"),),
    "LoraLoaderModelOnly": (("lora_name", "loras"),),
}


MODEL_ROOTS: dict[str, tuple[str, ...]] = {
    "unet": ("unet", "diffusion_models"),
    "gguf": ("gguf", "unet"),
    "clip": ("clip", "text_encoders"),
    "vae": ("vae", "checkpoints"),
    "upscale_models": ("upscale_models",),
    "loras": ("loras",),
}


LEGACY_WIDGET_NAMES: dict[str, tuple[str | None, ...]] = {
    "UNETLoader": ("unet_name", "weight_dtype"),
    "ModelSamplingAuraFlow": ("shift",),
    "CLIPLoader": ("clip_name", "type", "device"),
    "CLIPTextEncode": ("text",),
    "EmptyLatentImage": ("width", "height", "batch_size"),
    "VAELoader": ("vae_name",),
    "KSampler": (
        "seed",
        None,
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "denoise",
    ),
    "SaveImage": ("filename_prefix",),
}


def require_workflow(workflow_id: str, media_type: str = "", *, include_disabled: bool = False) -> WorkflowDefinition:
    from .creation_custom import default_ids, definitions
    clean = str(workflow_id or "").strip()
    if not clean:
        clean = default_ids()["video_workflow_id" if media_type == "video" else "image_workflow_id"]
    definition = WORKFLOWS.get(clean)
    if definition is None:
        definition = next((item for item in definitions(include_disabled=include_disabled) if item.id == clean), None)
    if definition is None:
        raise ValueError("不允许使用未登记的 ComfyUI 工作流。")
    if media_type and definition.media_type != media_type:
        raise ValueError("工作流类型与本次生成类型不一致。")
    return definition


def workflow_path(root: Path, definition: WorkflowDefinition) -> Path:
    if definition.custom:
        from .creation_custom import _path
        return _path(definition.id)
    workflow_root = (root / "my_workflows").resolve()
    path = (workflow_root / definition.filename).resolve()
    if not path.is_relative_to(workflow_root):
        raise ValueError("工作流路径超出授权目录。")
    return path


def load_workflow(root: Path, definition: WorkflowDefinition) -> dict[str, Any]:
    path = workflow_path(root, definition)
    if not path.is_file():
        raise ValueError(f"找不到固定工作流：{definition.filename}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"工作流不是有效 JSON：{definition.filename}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise ValueError("工作流缺少 nodes。")
    return payload


def workflow_catalog(root: Path) -> list[dict[str, object]]:
    from .creation_custom import definitions
    return [item.public_dict(root) for item in [*WORKFLOWS.values(), *definitions()]]


def _schema_input_names(node_info: Mapping[str, Any] | None) -> tuple[list[str], set[str]]:
    if not isinstance(node_info, Mapping):
        return [], set()
    inputs = node_info.get("input")
    if not isinstance(inputs, Mapping):
        return [], set()
    ordered: list[str] = []
    for group in ("required", "optional"):
        group_inputs = inputs.get(group)
        if isinstance(group_inputs, Mapping):
            ordered.extend(str(name) for name in group_inputs)
    return ordered, set(ordered)


def _input_present(inputs: Mapping[str, Any], name: str) -> bool:
    """Return whether a normal or dynamic-schema input is present.

    ComfyUI exposes an Autogrow/DynamicSlot container (for example ``values``)
    as a required schema entry, while the API prompt must carry its live
    members as dotted keys (``values.a``).  Treat a populated dotted member as
    satisfying the synthetic container during our preflight validation.
    """
    if name in inputs:
        return True
    prefix = f"{name}."
    return any(str(key).startswith(prefix) for key in inputs)


def _link_table(workflow: Mapping[str, Any]) -> dict[str, tuple[str, int, str, int, str]]:
    result: dict[str, tuple[str, int, str, int, str]] = {}
    for record in workflow.get("links") or []:
        if not isinstance(record, list) or len(record) < 6:
            continue
        result[str(record[0])] = (
            str(record[1]),
            int(record[2]),
            str(record[3]),
            int(record[4]),
            str(record[5]),
        )
    return result


def _resolve_bypassed_origin(
    origin_id: str,
    output_slot: int,
    link_type: str,
    nodes_by_id: Mapping[str, Mapping[str, Any]],
    links: Mapping[str, tuple[str, int, str, int, str]],
    *,
    seen: set[str] | None = None,
) -> tuple[str, int]:
    node = nodes_by_id.get(origin_id)
    if node is None:
        raise ValueError(f"工作流链接引用了不存在的节点：{origin_id}")
    mode = int(node.get("mode") or 0)
    if mode == 0:
        return origin_id, output_slot
    if mode != 4:
        raise ValueError(f"活动链路经过了已停用节点：{origin_id}")
    visited = set(seen or ())
    if origin_id in visited:
        raise ValueError("工作流旁路节点形成循环。")
    visited.add(origin_id)
    candidates = [
        item
        for item in node.get("inputs") or []
        if isinstance(item, Mapping) and item.get("link") is not None
    ]
    typed = [item for item in candidates if str(item.get("type") or "") == link_type]
    selected = (typed or candidates)
    if not selected:
        raise ValueError(f"旁路节点没有可继续追踪的输入：{origin_id}")
    upstream_link = links.get(str(selected[0].get("link")))
    if upstream_link is None:
        raise ValueError(f"旁路节点输入缺少链接记录：{origin_id}")
    return _resolve_bypassed_origin(
        upstream_link[0],
        upstream_link[1],
        upstream_link[4],
        nodes_by_id,
        links,
        seen=visited,
    )


def _widget_inputs(
    node: Mapping[str, Any],
    node_info: Mapping[str, Any] | None,
) -> dict[str, Any]:
    ordered_names, allowed_names = _schema_input_names(node_info)
    stored_values = node.get("widgets_values") or []
    values = [] if isinstance(stored_values, Mapping) else list(stored_values)
    result: dict[str, Any] = {}
    node_type = str(node.get("type") or "")
    named = node.get("widgets_values_named") or (stored_values if isinstance(stored_values, Mapping) else None)
    if isinstance(named, Mapping):
        for name, value in named.items():
            clean = str(name)
            is_power_lora = (
                node_type == "Power Lora Loader (rgthree)"
                and clean.startswith("lora_")
                and clean.removeprefix("lora_").isdigit()
                and isinstance(value, Mapping)
            )
            if is_power_lora or (node_type != "Power Lora Loader (rgthree)" and (not allowed_names or clean in allowed_names)):
                result[clean] = value

    # rgthree's loader accepts arbitrary lora_N kwargs through a flexible
    # optional-input mapping, but ComfyUI publishes that mapping as an empty
    # object_info.optional object. Older saved canvases also lack the named
    # widget map, so recover the serialized LoRA slots from their values.
    if node_type == "Power Lora Loader (rgthree)" and not any(
        name.startswith("lora_") for name in result
    ):
        lora_index = 0
        for value in values:
            if not isinstance(value, Mapping) or "lora" not in value:
                continue
            lora_index += 1
            result[f"lora_{lora_index}"] = value

    descriptors = [item for item in node.get("inputs") or [] if isinstance(item, Mapping)]
    value_index = 0
    used_descriptors = False
    for descriptor in descriptors:
        if not isinstance(descriptor.get("widget"), Mapping):
            continue
        used_descriptors = True
        if value_index >= len(values):
            break
        value = values[value_index]
        value_index += 1
        name = str(descriptor.get("name") or descriptor["widget"].get("name") or "")
        if descriptor.get("link") is None and name and (not allowed_names or name in allowed_names):
            result.setdefault(name, value)

    legacy_names = LEGACY_WIDGET_NAMES.get(node_type)
    if legacy_names and not used_descriptors:
        for index, name in enumerate(legacy_names):
            if name and index < len(values) and (not allowed_names or name in allowed_names):
                result.setdefault(name, values[index])
    elif not used_descriptors and not named and ordered_names:
        linked_names = {
            str(item.get("name") or "")
            for item in descriptors
            if item.get("link") is not None
        }
        candidates = [name for name in ordered_names if name not in linked_names]
        for name, value in zip(candidates, values):
            result.setdefault(name, value)
    return result


def ui_workflow_to_api_prompt(
    workflow: Mapping[str, Any],
    *,
    object_info: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    nodes = [item for item in workflow.get("nodes") or [] if isinstance(item, Mapping)]
    nodes_by_id = {str(item.get("id")): item for item in nodes}
    links = _link_table(workflow)
    prompt: dict[str, dict[str, Any]] = {}

    # A saved ComfyUI canvas may contain several alternate, editor-visible
    # branches.  The API must receive only the branch that contributes to an
    # output node: otherwise an unconnected required input (for example the
    # optional TE-Speed patch in 黑鹤.json) can reject the entire request.  The
    # small synthetic graphs used by unit tests may not have an output node;
    # retain their previous all-active-node behaviour in that case.
    output_ids: set[str] = set()
    for node in nodes:
        node_id = str(node.get("id"))
        node_type = str(node.get("type") or "").strip()
        if int(node.get("mode") or 0) != 0 or node_type in UI_ONLY_NODE_TYPES:
            continue
        node_info = object_info.get(node_type) if isinstance(object_info, Mapping) else None
        if node_type in {"SaveImage", "SaveVideo"} or (
            isinstance(node_info, Mapping) and bool(node_info.get("output_node"))
        ):
            output_ids.add(node_id)

    reachable: set[str] | None = None
    if output_ids:
        reachable = set()
        pending = list(output_ids)
        while pending:
            node_id = str(pending.pop())
            if node_id in reachable:
                continue
            node = nodes_by_id.get(node_id)
            if node is None or int(node.get("mode") or 0) != 0:
                continue
            node_type = str(node.get("type") or "").strip()
            if node_type in UI_ONLY_NODE_TYPES:
                continue
            reachable.add(node_id)
            for descriptor in node.get("inputs") or []:
                if not isinstance(descriptor, Mapping) or descriptor.get("link") is None:
                    continue
                link = links.get(str(descriptor.get("link")))
                if link is None:
                    continue
                origin_id, _ = _resolve_bypassed_origin(
                    link[0], link[1], link[4], nodes_by_id, links
                )
                if origin_id not in reachable:
                    pending.append(origin_id)

    for node in nodes:
        node_id = str(node.get("id"))
        node_type = str(node.get("type") or "").strip()
        if int(node.get("mode") or 0) != 0 or node_type in UI_ONLY_NODE_TYPES:
            continue
        if reachable is not None and node_id not in reachable:
            continue
        node_info = object_info.get(node_type) if isinstance(object_info, Mapping) else None
        if isinstance(object_info, Mapping) and node_info is None:
            raise ValueError(f"ComfyUI 缺少工作流节点：{node_type}")
        inputs = _widget_inputs(node, node_info if isinstance(node_info, Mapping) else None)
        for descriptor in node.get("inputs") or []:
            if not isinstance(descriptor, Mapping) or descriptor.get("link") is None:
                continue
            link = links.get(str(descriptor.get("link")))
            if link is None:
                raise ValueError(f"节点 {node_id} 的输入 {descriptor.get('name')} 缺少链接记录。")
            origin_id, output_slot = _resolve_bypassed_origin(
                link[0], link[1], link[4], nodes_by_id, links
            )
            _assign_input(
                inputs,
                str(descriptor.get("name") or "input"),
                [origin_id, output_slot],
            )
        ordered_required, _ = _schema_input_names(node_info if isinstance(node_info, Mapping) else None)
        if isinstance(node_info, Mapping):
            required = node_info.get("input", {}).get("required", {})
            if isinstance(required, Mapping):
                missing = [name for name in required if not _input_present(inputs, str(name))]
                if missing:
                    raise ValueError(f"节点 {node_type} 缺少必填输入：{', '.join(missing)}")
        prompt[node_id] = {
            "class_type": node_type,
            "inputs": inputs,
            "_meta": {"title": str(node.get("title") or node_type)},
        }
    if not prompt:
        raise ValueError("工作流没有可执行节点。")
    return prompt


def _set_first(prompt: dict[str, dict[str, Any]], node_type: str, name: str, value: Any) -> bool:
    for node in prompt.values():
        if node.get("class_type") == node_type:
            node["inputs"][name] = value
            return True
    return False


def _set_text_source(
    prompt: dict[str, dict[str, Any]],
    node_id: str,
    value: str,
    *,
    seen: set[str] | None = None,
) -> bool:
    """Set a text widget, following a saved graph's text link when needed."""
    node = prompt.get(str(node_id))
    if not isinstance(node, Mapping):
        return False
    visited = set(seen or ())
    clean_id = str(node_id)
    if clean_id in visited:
        return False
    visited.add(clean_id)
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return False
    node_type = str(node.get("class_type") or "")
    if node_type in {"CR Text", "PrimitiveStringMultiline"}:
        key = "text" if "text" in inputs or node_type == "CR Text" else "value"
        inputs[key] = value
        return True
    if "text" in inputs and not isinstance(inputs.get("text"), list):
        inputs["text"] = value
        return True
    text_ref = inputs.get("text")
    if isinstance(text_ref, (list, tuple)) and text_ref:
        return _set_text_source(prompt, str(text_ref[0]), value, seen=visited)
    return False


def _set_conditioning_texts(
    prompt: dict[str, dict[str, Any]],
    positive: str,
    negative: str,
) -> None:
    """Apply prompts according to positive/negative sampler connections.

    Anima's ``无敌图片.json`` routes both positive encoders through a ``CR
    Text`` node, while its negative encoders are direct widgets.  Older/simple
    graphs often put text directly on CLIPTextEncode.  Looking at the sampler
    links handles both layouts without relying on node IDs or list order.
    """
    positive_ids: set[str] = set()
    negative_ids: set[str] = set()
    for node_id, node in prompt.items():
        if node.get("class_type") not in {"KSampler", "KSamplerAdvanced"}:
            continue
        inputs = node.get("inputs") or {}
        for key, target in (("positive", positive_ids), ("negative", negative_ids)):
            ref = inputs.get(key)
            if isinstance(ref, (list, tuple)) and ref:
                target.add(str(ref[0]))

    # Follow each conditioning source to its text widget.  A source may be a
    # CLIPTextEncode or a small text utility node such as CR Text.
    for node_id in positive_ids:
        _set_text_source(prompt, node_id, positive)
    for node_id in negative_ids:
        _set_text_source(prompt, node_id, negative)

    # If a graph does not expose sampler-role links, keep the historical
    # fallback: first encoder is positive and subsequent direct encoders are
    # negative.
    encoders = _nodes_of_type(prompt, "CLIPTextEncode")
    if not positive_ids and encoders:
        first_id = next(
            (node_id for node_id, node in prompt.items() if node is encoders[0]),
            "",
        )
        _set_text_source(prompt, first_id, positive)
    if not negative_ids:
        for node_id, node in prompt.items():
            if node.get("class_type") == "CLIPTextEncode" and isinstance(node.get("inputs", {}).get("text"), str):
                node["inputs"]["text"] = negative if node is not (encoders[0] if encoders else None) else positive


def _set_resolution_selector(
    prompt: dict[str, dict[str, Any]], width: int, height: int
) -> bool:
    """Set a ResolutionSelector to the requested dimensions' aspect and area."""
    selectors = _nodes_of_type(prompt, "ResolutionSelector")
    if not selectors:
        return False
    node = selectors[0]
    inputs = node.setdefault("inputs", {})
    ratio = width / max(height, 1)
    choices = (
        (1 / 1, "1:1"), (2 / 3, "2:3"), (3 / 2, "3:2"),
        (3 / 4, "3:4"), (4 / 3, "4:3"), (9 / 16, "9:16"),
        (16 / 9, "16:9"), (21 / 9, "21:9"),
    )
    _, prefix = min(choices, key=lambda item: abs(item[0] - ratio))
    current = str(inputs.get("aspect_ratio") or "")
    if "(" in current and current.startswith(prefix):
        aspect = current
    else:
        aspect = next((item for item in ("1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)", "3:4 (Portrait Standard)", "4:3 (Standard)", "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)") if item.startswith(prefix)), prefix)
    inputs["aspect_ratio"] = aspect
    inputs["megapixels"] = round(width * height / 1_048_576, 3)
    inputs["multiple"] = 32
    return True


def _nodes_of_type(prompt: dict[str, dict[str, Any]], node_type: str) -> list[dict[str, Any]]:
    return [node for node in prompt.values() if node.get("class_type") == node_type]


def _set_image_output_dimensions(
    prompt: dict[str, dict[str, Any]], width: int, height: int
) -> bool:
    """Keep the final image dimensions in sync with the latent dimensions.

    ``无敌图片.json`` performs a second upscale pass through ``ImageScale``.
    Leaving that node's saved 1296x2304 widgets untouched makes the public
    width/height controls appear to work while every output keeps the old
    fixed size.  Update every active ImageScale node so alternate refinement
    branches cannot silently restore stale dimensions.
    """
    scales = _nodes_of_type(prompt, "ImageScale")
    if not scales:
        return False
    for node in scales:
        inputs = node.setdefault("inputs", {})
        inputs["width"] = int(width)
        inputs["height"] = int(height)
    return True


def workflow_execution_snapshot(
    root: Path,
    definition: WorkflowDefinition,
    prompt: Mapping[str, Mapping[str, Any]],
    *,
    runtime_workflow_filename: str = "",
) -> dict[str, Any]:
    """Return the exact workflow/model/LoRA state submitted to ComfyUI."""
    metadata = _workflow_file_metadata(root, definition)
    models: list[dict[str, Any]] = []
    loras: list[dict[str, Any]] = []
    node_types: list[str] = []
    for node_id, node in prompt.items():
        node_type = str(node.get("class_type") or "")
        if node_type not in node_types:
            node_types.append(node_type)
        inputs = node.get("inputs") or {}
        for input_name, category in MODEL_INPUTS.get(node_type, ()):
            value = inputs.get(input_name)
            if isinstance(value, str) and value.strip():
                models.append({"node_id": str(node_id), "node_type": node_type, "input": input_name, "category": category, "name": value.strip()})
        if node_type == "Power Lora Loader (rgthree)":
            for input_name, value in inputs.items():
                if not str(input_name).startswith("lora_") or not isinstance(value, Mapping):
                    continue
                name = str(value.get("lora") or "").strip()
                if name:
                    loras.append({"node_id": str(node_id), "slot": str(input_name), "name": name, "on": bool(value.get("on")), "strength": value.get("strength")})
    return {
        "mode": "exact_workflow",
        "source_file": str(runtime_workflow_filename or definition.filename),
        "source_path": str(metadata.get("path") or ""),
        "source_sha256": str(metadata.get("sha256") or ""),
        "active_node_count": len(prompt),
        "active_node_types": sorted(node_types),
        "models": models,
        "loras": loras,
    }


def _configure_image_loras(
    prompt: dict[str, dict[str, Any]], choices: object
) -> list[str] | None:
    """Apply Agent-selected optional LoRAs while preserving the Turbo pair."""
    if choices is None:
        return None
    selected = {
        str(item).strip()
        for item in choices
        if str(item).strip() in IMAGE_OPTIONAL_LORAS
    } if isinstance(choices, (list, tuple, set)) else set()
    expected = {IMAGE_OPTIONAL_LORAS[item] for item in selected}
    seen_optional: set[str] = set()
    seen_required: set[str] = set()
    for node in _nodes_of_type(prompt, "Power Lora Loader (rgthree)"):
        inputs = node.setdefault("inputs", {})
        for name, value in inputs.items():
            if not name.startswith("lora_") or not isinstance(value, dict):
                continue
            filename = str(value.get("lora") or "").strip()
            if filename in IMAGE_REQUIRED_LORAS:
                value["on"] = True
                seen_required.add(filename)
            elif filename in IMAGE_OPTIONAL_LORAS.values():
                value["on"] = filename in expected
                seen_optional.add(filename)
    if seen_required != IMAGE_REQUIRED_LORAS:
        raise ValueError("无敌图片工作流缺少配套的 Turbo 加速 LoRA。")
    missing = expected - seen_optional
    if missing:
        raise ValueError(f"无敌图片工作流缺少已选择的 LoRA：{', '.join(sorted(missing))}")
    return [item for item in IMAGE_OPTIONAL_LORAS if item in selected]


def _assign_input(inputs: dict[str, Any], name: str, value: Any) -> None:
    """Store dynamic V3 inputs using ComfyUI's flat dotted-key wire format.

    ComfyUI's API prompt validator discovers dynamic inputs from keys such as
    ``values.a`` and ``ref_images.ref_image_0``.  It expands those keys into
    nested dictionaries only after validation, inside ``build_nested_inputs``.
    Sending an already nested ``{"values": {"a": ...}}`` object makes the
    validator see the dynamic container but not its required member and yields
    ``Required input is missing``.  Keep the wire representation flat here;
    this also works with both V3 Autogrow and DynamicSlot inputs.
    """
    clean = str(name).strip()
    if clean:
        inputs[clean] = value


def _combo_options(node_info: Mapping[str, Any] | None, input_name: str) -> list[str]:
    if not isinstance(node_info, Mapping):
        return []
    required = node_info.get("input", {}).get("required", {})
    value = required.get(input_name) if isinstance(required, Mapping) else None
    if not isinstance(value, list) or not value or not isinstance(value[0], list):
        return []
    return [str(item) for item in value[0] if isinstance(item, (str, int, float))]


def _load_runtime_workflow(
    root: Path,
    definition: WorkflowDefinition,
    object_info: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """Strictly load the user-approved workflow without fallback or mutation.

    The selected JSON is the sole source of truth; missing models or nodes are
    reported to the caller instead of silently selecting a replacement graph.
    """
    # The approved canvas file is the sole source of truth.  Do not silently
    # replace it with a compatibility graph when a checkpoint is unavailable;
    # the user must see and fix that exact workflow in ComfyUI.
    workflow = load_workflow(root, definition)
    return workflow, definition.filename


def _repair_model_loader_names(
    prompt: dict[str, dict[str, Any]],
    object_info: Mapping[str, Any] | None,
) -> None:
    """Resolve stale model filenames in a saved workflow against ComfyUI."""
    if not isinstance(object_info, Mapping):
        return
    # The two H3 conditioning nodes use different diffusion checkpoints.
    # ``MiniMaxH3ReferenceToVideo`` is ref2va, while the lightweight fallback
    # workflow uses the installed fl2va checkpoint with ``first_frame``.
    # Never silently bind a ref2va graph to fl2va: ComfyUI may accept the
    # prompt, but the model/conditioning pair is semantically incompatible.
    wants_ref2va = any(
        node.get("class_type") == "MiniMaxH3ReferenceToVideo"
        for node in prompt.values()
    )
    wants_fl2va = any(
        node.get("class_type") == "MiniMaxH3ImageToVideo"
        for node in prompt.values()
    )
    if wants_ref2va:
        def unet_predicate(value: str) -> bool:
            return "ref2va" in value.casefold()
    elif wants_fl2va:
        def unet_predicate(value: str) -> bool:
            return "fl2va" in value.casefold()
    else:
        def unet_predicate(value: str) -> bool:
            return "anima" in value.casefold()

    if wants_ref2va or wants_fl2va:
        def clip_predicate(value: str) -> bool:
            return "qwen3vl" in value.casefold() and "minimax" in value.casefold()
    else:
        def clip_predicate(value: str) -> bool:
            return "qwen" in value.casefold() and ("06b" in value.casefold() or "base" in value.casefold())
    selectors = {
        ("UNETLoader", "unet_name"): unet_predicate,
        ("CLIPLoader", "clip_name"): clip_predicate,
    }
    for node in prompt.values():
        node_type = str(node.get("class_type") or "")
        for (selector_type, input_name), predicate in selectors.items():
            if node_type != selector_type:
                continue
            options = _combo_options(object_info.get(node_type), input_name)
            if not options:
                continue
            current = str(node.get("inputs", {}).get(input_name) or "")
            if current in options:
                continue
            candidates = [item for item in options if predicate(item)]
            if not candidates:
                required_kind = (
                    "ref2va" if wants_ref2va and node_type == "UNETLoader"
                    else "fl2va" if wants_fl2va and node_type == "UNETLoader"
                    else "Anima/Qwen" if node_type == "UNETLoader"
                    else current
                )
                raise ValueError(f"ComfyUI 缺少 {node_type} 所需的 {required_kind} 模型：{current}")
            candidates.sort(key=lambda item: ("int8" not in item.casefold(), len(item), item.casefold()))
            node["inputs"][input_name] = candidates[0]


def _validate_model_loader_names(
    prompt: dict[str, dict[str, Any]],
    object_info: Mapping[str, Any] | None,
) -> None:
    """Require the saved model selection; never substitute another model."""
    if not isinstance(object_info, Mapping):
        return
    for node in prompt.values():
        node_type = str(node.get("class_type") or "")
        input_name = "unet_name" if node_type == "UNETLoader" else "clip_name" if node_type == "CLIPLoader" else ""
        if not input_name:
            continue
        current = str((node.get("inputs") or {}).get(input_name) or "").strip()
        options = _combo_options(object_info.get(node_type), input_name)
        if current and options and current not in options:
            raise ValueError(f"ComfyUI 缂哄皯宸ヤ綔娴佸凡閫夋嫨鐨勬ā鍨嬶細{current}")


def _raw_widget_value(node: Mapping[str, Any], index: int) -> Any:
    values = node.get("widgets_values")
    if isinstance(values, (list, tuple)) and index < len(values):
        return values[index]
    return None


def _raw_model_dependencies(workflow: Mapping[str, Any]) -> list[dict[str, str]]:
    """Read model references from active nodes without executing the graph.

    Some custom loaders (notably rgthree's Power Lora Loader) expose dynamic
    widgets that are not present in ``object_info``.  Reading only the saved
    widget values lets the preflight report those dependencies as well while
    keeping the original workflow file untouched.
    """
    result: list[dict[str, str]] = []
    for node in workflow.get("nodes") or []:
        if not isinstance(node, Mapping) or int(node.get("mode") or 0) != 0:
            continue
        node_type = str(node.get("type") or "").strip()
        if node_type in UI_ONLY_NODE_TYPES:
            continue
        node_id = str(node.get("id") or "")
        for input_name, category in MODEL_INPUTS.get(node_type, ()):
            value = _raw_widget_value(node, 0)
            if isinstance(node.get("widgets_values"), Mapping):
                value = node.get("widgets_values", {}).get(input_name)
            if isinstance(value, str) and value.strip():
                result.append({
                    "node_id": node_id,
                    "node_type": node_type,
                    "input": input_name,
                    "category": category,
                    "requested": value.strip(),
                })
        if node_type == "Power Lora Loader (rgthree)":
            for value in node.get("widgets_values") or ():
                if not isinstance(value, Mapping) or value.get("on") is not True:
                    continue
                lora = value.get("lora")
                if isinstance(lora, str) and lora.strip():
                    result.append({
                        "node_id": node_id,
                        "node_type": node_type,
                        "input": "lora",
                        "category": "loras",
                        "requested": lora.strip(),
                    })
    # A graph can reference the same checkpoint through several disabled or
    # duplicated widgets.  Keep each node reference for diagnostics, but do
    # not make the UI display duplicate rows for an identical requirement.
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in result:
        key = (item["category"], item["requested"].casefold(), item["node_type"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _safe_model_path(root: Path, category: str, name: str) -> Path | None:
    """Resolve a model name inside ComfyUI's model directories only."""
    clean = str(name or "").strip().replace("/", "\\")
    if not clean:
        return None
    relative = Path(clean)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    models_root = (root / "models").resolve()
    for folder in MODEL_ROOTS.get(category, (category,)):
        base = (models_root / folder).resolve()
        if not base.is_relative_to(models_root):
            continue
        candidate = (base / relative).resolve()
        if candidate.is_relative_to(base) and candidate.is_file():
            return candidate
    # Extra model paths can move a file below a category directory.  A bounded
    # basename search handles that case without ever leaving ``models``.  It
    # is deliberately a fallback after the cheap exact-path check.
    basename = relative.name.casefold()
    if not basename:
        return None
    for folder in MODEL_ROOTS.get(category, (category,)):
        base = (models_root / folder).resolve()
        if not base.is_dir() or not base.is_relative_to(models_root):
            continue
        try:
            for candidate in base.rglob("*"):
                if candidate.is_file() and candidate.name.casefold() == basename:
                    return candidate.resolve()
        except OSError:
            continue
    return None


def _workflow_file_metadata(root: Path, definition: WorkflowDefinition) -> dict[str, Any]:
    path = workflow_path(root, definition)
    result: dict[str, Any] = {
        "path": str(path),
        "available": path.is_file(),
        "sha256": "",
        "size": 0,
        "node_count": 0,
        "active_node_types": [],
    }
    if not path.is_file():
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        nodes = [item for item in (payload.values() if definition.custom else payload.get("nodes") or []) if isinstance(item, Mapping)]
        result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        result["size"] = path.stat().st_size
        result["node_count"] = len(nodes)
        result["active_node_types"] = sorted({
            str(item.get("class_type" if definition.custom else "type") or "")
            for item in nodes
            if int(item.get("mode") or 0) == 0 and str(item.get("type") or "") not in UI_ONLY_NODE_TYPES
        })
    except (OSError, ValueError, json.JSONDecodeError):
        result["error"] = f"工作流不是有效 JSON：{definition.filename}"
    return result


def inspect_workflow(
    root: Path,
    definition: WorkflowDefinition,
    *,
    object_info: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a user-facing, non-executing dependency report for a workflow."""
    if definition.custom:
        from .creation_custom import inspect_custom
        return inspect_custom(root, definition, object_info)
    metadata = _workflow_file_metadata(root, definition)
    report: dict[str, Any] = {
        "id": definition.id,
        "label": definition.label,
        "media_type": definition.media_type,
        "filename": definition.filename,
        "description": definition.description,
        "requires_reference": definition.requires_reference,
        "file": metadata,
        "nodes": {"required": metadata.get("active_node_types", []), "missing": [], "checked": object_info is not None},
        "models": [],
        "status": "missing_file" if not metadata.get("available") else "not_checked",
        "errors": [],
    }
    if not metadata.get("available"):
        report["errors"].append(metadata.get("error") or f"缺少固定工作流：{definition.filename}")
        return report
    expected_hash = str(definition.expected_sha256 or "").strip().casefold()
    actual_hash = str(metadata.get("sha256") or "").strip().casefold()
    if expected_hash and actual_hash and expected_hash != actual_hash:
        report["status"] = "changed"
        report["errors"].append(
            f"固定工作流已变化：{definition.filename}（请重新确认工作流后再使用）"
        )
    try:
        workflow = load_workflow(root, definition)
    except ValueError as exc:
        report["errors"].append(str(exc))
        report["status"] = "invalid"
        return report

    raw_dependencies = _raw_model_dependencies(workflow)
    prompt: dict[str, dict[str, Any]] | None = None
    if object_info is not None:
        report["nodes"]["missing"] = sorted({
            str(node.get("type") or "")
            for node in workflow.get("nodes") or []
            if isinstance(node, Mapping)
            and int(node.get("mode") or 0) == 0
            and str(node.get("type") or "") not in UI_ONLY_NODE_TYPES
            and str(node.get("type") or "") not in object_info
        })
        try:
            prompt = ui_workflow_to_api_prompt(workflow, object_info=object_info)
        except ValueError as exc:
            report["errors"].append(str(exc))
        if prompt is not None:
            try:
                _validate_model_loader_names(prompt, object_info)
            except ValueError as exc:
                report["errors"].append(str(exc))

    for item in raw_dependencies:
        selected = item["requested"]
        if prompt is not None:
            node = prompt.get(item["node_id"])
            if isinstance(node, Mapping):
                candidate = node.get("inputs", {}).get(item["input"])
                if isinstance(candidate, str) and candidate.strip():
                    selected = candidate.strip()
        resolved = _safe_model_path(root, item["category"], selected)
        available_in_api = False
        if object_info is not None:
            node_info = object_info.get(item["node_type"])
            api_input = item["input"]
            options = _combo_options(node_info, api_input)
            available_in_api = selected in options or item["requested"] in options
        report["models"].append({
            **item,
            "selected": selected,
            "available": bool(resolved or available_in_api),
            "resolved_path": str(resolved) if resolved else "",
        })

    if report["status"] == "changed":
        # Still return the full dependency report so the user can see what
        # changed and which files would be needed after re-approval.
        pass
    elif object_info is None:
        report["status"] = "comfyui_unavailable"
    elif report["errors"] or report["nodes"]["missing"] or any(not item["available"] for item in report["models"]):
        report["status"] = "needs_setup"
    else:
        report["status"] = "ready"
    return report


def build_workflow_prompt(
    root: Path,
    definition: WorkflowDefinition,
    spec: Mapping[str, Any],
    *,
    job_id: str,
    object_info: Mapping[str, Any] | None = None,
    uploaded_reference: str = "",
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if definition.custom:
        from .creation_custom import build_custom
        return build_custom(root, definition, spec, job_id=job_id, object_info=object_info, uploaded_reference=uploaded_reference)
    workflow, runtime_workflow_filename = _load_runtime_workflow(root, definition, object_info)
    prompt = ui_workflow_to_api_prompt(workflow, object_info=object_info)
    _validate_model_loader_names(prompt, object_info)
    effective = copy.deepcopy(dict(spec))
    effective.setdefault("width", definition.default_width)
    effective.setdefault("height", definition.default_height)
    effective.setdefault("steps", definition.default_steps)
    effective.setdefault("cfg", definition.default_cfg)
    effective.setdefault("duration_seconds", definition.default_duration_seconds)
    effective.setdefault("fps", definition.default_fps)

    seed = int(effective.get("seed", -1))
    if seed < 0:
        import secrets

        seed = secrets.randbelow(0x7FFFFFFFFFFFFFFF)
    effective["seed"] = seed
    effective["runtime_workflow_filename"] = runtime_workflow_filename
    output_prefix = f"MioJobs/{job_id}/{definition.media_type}"

    if definition.media_type == "image":
        if definition.id == "anima-2.9b-image":
            # The workflow's paired Turbo LoRAs are calibrated for these
            # sampler values. Agent-supplied generic defaults must not replace
            # the user's verified 4-step configuration.
            effective["steps"] = definition.default_steps
            effective["cfg"] = definition.default_cfg
            selected_loras = _configure_image_loras(
                prompt, effective.get("lora_choices")
            )
            if selected_loras is not None:
                effective["lora_choices"] = selected_loras
        if not _nodes_of_type(prompt, "CLIPTextEncode"):
            raise ValueError("图片工作流缺少 CLIPTextEncode。")
        _set_conditioning_texts(
            prompt,
            str(effective.get("prompt") or ""),
            str(effective.get("negative_prompt") or ""),
        )
        _set_first(prompt, "EmptyLatentImage", "width", int(effective["width"]))
        _set_first(prompt, "EmptyLatentImage", "height", int(effective["height"]))
        _set_first(prompt, "EmptyLatentImage", "batch_size", int(effective.get("batch_size") or 1))
        _set_image_output_dimensions(
            prompt, int(effective["width"]), int(effective["height"])
        )
        # 无敌图片.json has two samplers (base pass + refinement pass).  Both
        # must receive the request's deterministic controls; changing only the
        # first one leaves the second pass on a stale seed/step count.
        for sampler in _nodes_of_type(prompt, "KSampler") + _nodes_of_type(prompt, "KSamplerAdvanced"):
            sampler["inputs"]["seed"] = seed
            sampler["inputs"]["steps"] = int(effective["steps"])
            sampler["inputs"]["cfg"] = float(effective["cfg"])
        if not _set_first(prompt, "SaveImage", "filename_prefix", output_prefix):
            raise ValueError("图片工作流缺少 SaveImage。")
    else:
        if not uploaded_reference:
            raise ValueError("MiniMax H3 视频需要一张已经上传的参考图。")
        if not _set_first(prompt, "LoadImage", "image", uploaded_reference):
            raise ValueError("视频工作流缺少 LoadImage。")
        # Both shipped graphs expose the H3 conditioning node, but the
        # ref2va graph routes its prompt through a PrimitiveStringMultiline
        # node while the fl2va first-frame graph keeps it as a direct widget.
        conditioning_nodes = (
            _nodes_of_type(prompt, "MiniMaxH3ReferenceToVideo")
            + _nodes_of_type(prompt, "MiniMaxH3ImageToVideo")
        )
        prompt_nodes = [
            node for node in prompt.values()
            if node.get("class_type") == "PrimitiveStringMultiline"
            and "prompt" in str(node.get("_meta", {}).get("title") or "").lower()
        ] or _nodes_of_type(prompt, "PrimitiveStringMultiline")
        if prompt_nodes:
            prompt_nodes[0]["inputs"]["value"] = str(effective.get("prompt") or "")
        elif conditioning_nodes and any("prompt" in node.get("inputs", {}) for node in conditioning_nodes):
            for node in conditioning_nodes:
                if "prompt" in node.get("inputs", {}):
                    node["inputs"]["prompt"] = str(effective.get("prompt") or "")
        else:
            raise ValueError("视频工作流缺少提示词节点。")
        # Both the high-fidelity ref2va graph and the fl2va first-frame
        # fallback share width/height/length controls.  Keep the adapter
        # agnostic to which compatible graph was selected above.
        # 黑鹤.json drives H3 dimensions through ResolutionSelector links;
        # changing the conditioning widget directly would be overwritten by
        # that link during execution.  Update the selector instead.
        if not _set_resolution_selector(
            prompt, int(effective["width"]), int(effective["height"])
        ):
            for node in conditioning_nodes:
                node["inputs"]["width"] = int(effective["width"])
                node["inputs"]["height"] = int(effective["height"])
        duration_nodes = [
            node for node in prompt.values()
            if node.get("class_type") == "PrimitiveFloat"
            and "duration" in str(node.get("_meta", {}).get("title") or "").lower()
        ] or _nodes_of_type(prompt, "PrimitiveFloat")
        if duration_nodes:
            duration_nodes[0]["inputs"]["value"] = float(effective["duration_seconds"])
            # The shipped H3 graph converts seconds to a frame count through a
            # ``ComfyMathExpression`` node.  Its saved expression is written
            # for the original 24 FPS setting; changing only CreateVideo.fps
            # would therefore produce a video several times longer than the
            # requested duration.  Keep the graph's required 5 (mod 17) frame
            # alignment, but derive the count from the requested FPS.
            duration_ids = {
                node_id for node_id, node in prompt.items()
                if node in duration_nodes
            }
            target_fps = int(effective["fps"])
            expression = (
                f"max(5, round(a * {target_fps})) + "
                f"(5 - (max(5, round(a * {target_fps})) % 17)) % 17"
            )
            for node in prompt.values():
                if node.get("class_type") != "ComfyMathExpression":
                    continue
                inputs = node.setdefault("inputs", {})
                expression_value = str(inputs.get("expression") or "")
                duration_ref = inputs.get("values.a")
                if (
                    "a * 24" in expression_value
                    and isinstance(duration_ref, (list, tuple))
                    and duration_ref
                    and str(duration_ref[0]) in duration_ids
                ):
                    inputs["expression"] = expression
        _set_first(prompt, "Seed (rgthree)", "seed", seed)
        _set_first(prompt, "RandomNoise", "noise_seed", seed)
        save_nodes = _nodes_of_type(prompt, "SaveVideo")
        if not save_nodes:
            raise ValueError("视频工作流缺少 SaveVideo。")
        for node in save_nodes:
            node["inputs"]["filename_prefix"] = output_prefix
        for node in _nodes_of_type(prompt, "CreateVideo"):
            node["inputs"]["fps"] = int(effective["fps"])
    effective["workflow_snapshot"] = workflow_execution_snapshot(
        root,
        definition,
        prompt,
        runtime_workflow_filename=runtime_workflow_filename,
    )
    return prompt, effective


__all__ = [
    "WORKFLOWS",
    "WorkflowDefinition",
    "build_workflow_prompt",
    "inspect_workflow",
    "load_workflow",
    "require_workflow",
    "ui_workflow_to_api_prompt",
    "workflow_execution_snapshot",
    "workflow_catalog",
]
