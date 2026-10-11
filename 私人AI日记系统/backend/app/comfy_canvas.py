"""Serialize saved ComfyUI canvases without inventing execution nodes.

Set/Get and bypass nodes are frontend wiring, not server node classes. Widget
values follow schema/widget order, which is not necessarily socket order.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


VIRTUAL = {"GetNode", "SetNode", "Reroute"}
SCALAR = {"STRING", "INT", "FLOAT", "BOOLEAN", "COMBO", "COMFY_DYNAMICCOMBO_V3"}


def widget_inputs(node: Mapping, info: Mapping | None, legacy: Mapping) -> dict:
    values = node.get("widgets_values") or []
    descriptors = [x for x in node.get("inputs", []) if isinstance(x, Mapping)]
    schema = dict((info or {}).get("input", {}).get("required", {}))
    schema.update((info or {}).get("input", {}).get("optional", {}))
    named = node.get("widgets_values_named") or (values if isinstance(values, Mapping) else None)
    node_type = node.get("type")
    if node_type == "Power Lora Loader (rgthree)":
        if named:
            return {k: v for k, v in named.items() if k.startswith("lora_") and isinstance(v, Mapping)}
        return {f"lora_{i}": dict(v) for i, v in enumerate(
            (v for v in values if isinstance(v, Mapping) and "lora" in v), 1)}
    if named:
        # VHS stores codec widgets by name although object_info only declares
        # the format selector. These are real widgets, unlike its UI preview.
        names = set(schema) | {str(x.get("name")) for x in descriptors if x.get("widget")}
        return {str(k): v for k, v in named.items() if not names or str(k) in names}
    values = list(values)
    result = {}
    position = 0

    def consume(name, spec):
        nonlocal position
        if position >= len(values):
            return
        value = values[position]
        position += 1
        result[name] = value
        options = spec[1] if len(spec) > 1 and isinstance(spec[1], Mapping) else {}
        if spec[0] == "COMFY_DYNAMICCOMBO_V3":
            branch = next((x for x in options.get("options", []) if isinstance(x, Mapping) and x.get("key") == value), None)
            if branch:
                for group in ("required", "optional"):
                    for child, child_spec in branch.get("inputs", {}).get(group, {}).items():
                        consume(f"{name}.{child}", child_spec)
        if (options.get("control_after_generate") or name in {"seed", "noise_seed"}) and position < len(values):
            if values[position] in ("fixed", "randomize", "increment", "decrement"):
                position += 1

    if schema:
        widget_names = {str(x.get("name")) for x in descriptors if x.get("widget")}
        for name, spec in schema.items():
            if not isinstance(spec, (list, tuple)) or not spec:
                continue
            kind = spec[0]
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], Mapping) else {}
            scalar = isinstance(kind, list) or (isinstance(kind, str) and kind in SCALAR)
            if scalar and (name in widget_names or not descriptors or not options.get("forceInput")):
                consume(name, spec)
    else:
        names = legacy.get(node_type) if not any(x.get("widget") for x in descriptors) else None
        if names:
            result.update({name: values[i] for i, name in enumerate(names) if name and i < len(values)})
        else:
            for item in descriptors:
                if item.get("widget"):
                    consume(str(item.get("name")), [item.get("type", "STRING")])
    return result


def to_prompt(workflow: Mapping, object_info: Mapping | None, ui_only: set, legacy: Mapping) -> dict[str, dict[str, Any]]:
    nodes = {str(n['id']): n for n in workflow.get('nodes', []) if isinstance(n, Mapping)}
    links = {str(x[0]): x for x in workflow.get('links', []) if isinstance(x, list) and len(x) >= 6}
    setters = {}
    for node in nodes.values():
        if node.get('type') == 'SetNode' and node.get('widgets_values'):
            setters.setdefault(str(node['widgets_values'][0]), []).append(node)

    def resolve(node_id, slot, kind, seen=frozenset()):
        key = (str(node_id), int(slot))
        if key in seen:
            raise ValueError('工作流虚拟或旁路连线形成循环。')
        seen = seen | {key}
        node = nodes.get(str(node_id))
        if not node:
            raise ValueError(f'工作流链接引用了不存在的节点：{node_id}')
        mode, node_type = int(node.get('mode') or 0), node.get('type')
        if mode == 2:
            return None
        if node_type == 'GetNode':
            name = str((node.get('widgets_values') or [''])[0])
            matches = setters.get(name, [])
            active = [x for x in matches if int(x.get('mode') or 0) == 0]
            if len(active) > 1:
                raise ValueError(f'工作流包含重名的 SetNode：{name}')
            if not active:
                if matches:
                    return None
                raise ValueError(f'GetNode 找不到对应的 SetNode：{name}')
            return resolve(str(active[0]['id']), 0, kind, seen)
        if node_type in {'SetNode', 'Reroute'} or mode == 4:
            inputs = [x for x in node.get('inputs', []) if x.get('link') is not None]
            same = [x for x in inputs if x.get('type') == kind]
            candidates = same or inputs
            if not candidates:
                return None
            chosen = candidates[min(int(slot), len(candidates) - 1)] if len(same) > 1 else candidates[0]
            link = links.get(str(chosen['link']))
            if not link:
                raise ValueError(f'节点 {node_id} 缺少链接记录。')
            return resolve(link[1], link[2], link[5], seen)
        return str(node_id), int(slot)

    def input_links(node):
        result = {}
        for item in node.get('inputs', []):
            if item.get('link') is None:
                continue
            link = links.get(str(item['link']))
            if not link:
                raise ValueError(f"节点 {node['id']} 的输入 {item.get('name')} 缺少链接记录。")
            origin = resolve(link[1], link[2], link[5])
            if origin:
                result[str(item['name'])] = list(origin)
        return result

    active = {k: n for k, n in nodes.items() if int(n.get('mode') or 0) == 0 and n.get('type') not in ui_only | VIRTUAL}
    output_ids = [k for k, n in active.items() if n.get('type') in {'SaveImage', 'SaveVideo', 'VHS_VideoCombine'}
                  or bool((object_info or {}).get(n.get('type'), {}).get('output_node'))]
    reachable = set()
    pending = list(output_ids or active)
    resolved_links = {}
    while pending:
        key = pending.pop()
        if key in reachable or key not in active:
            continue
        reachable.add(key)
        resolved_links[key] = input_links(active[key])
        pending.extend(x[0] for x in resolved_links[key].values())
    prompt = {}
    for key, node in active.items():
        if key not in reachable:
            continue
        node_type = node['type']
        info = (object_info or {}).get(node_type)
        if object_info is not None and info is None:
            raise ValueError(f'ComfyUI 缺少工作流节点：{node_type}')
        inputs = widget_inputs(node, info, legacy)
        inputs.update(resolved_links[key])
        # A bypassed input must not retain the stale widget value underneath.
        for item in node.get('inputs', []):
            if item.get('link') is not None and item.get('name') not in resolved_links[key]:
                inputs.pop(item.get('name'), None)
        for name, spec in (info or {}).get('input', {}).get('required', {}).items():
            if name in inputs or any(k.startswith(name + '.') for k in inputs):
                continue
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], Mapping) else {}
            if spec[0] == 'COMFY_AUTOGROW_V3' and options.get('template', {}).get('min', 1) == 0:
                continue
            raise ValueError(f'节点 {node_type} 缺少必填输入：{name}')
        prompt[key] = {'class_type': node_type, 'inputs': inputs, '_meta': {'title': str(node.get('title') or node_type)}}
    if not prompt:
        raise ValueError('工作流没有可执行节点。')
    return prompt
