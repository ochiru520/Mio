"""Parameter bindings for the user's existing Qwen and H3 canvases.

Only select existing branches and bind task inputs. Never write to the user's
workflow, replace models, or synthesize a replacement sampling graph.
"""
from __future__ import annotations

import copy
import math
import secrets


QWEN_ID = 'qwen-2.1-image'
H3_ID = 'minimax-h3-video'


def qwen_canvas(graph: dict, references: list[str]) -> dict:
    graph = copy.deepcopy(graph)
    nodes = {str(n['id']): n for n in graph['nodes']}
    links = {str(x[0]): x for x in graph.get('links', [])}
    # These are bindings into this supplied canvas, not a generated graph.
    edit = bool(references)
    output_id = '487' if edit else '508'
    encoder_id = '497' if edit else '522'
    if nodes.get(output_id, {}).get('type') != 'SaveImage' or nodes.get(encoder_id, {}).get('type') != 'TextEncodeQwenImage21':
        raise ValueError('千问工作流的原分支节点已变化，请重新检查参数绑定。')
    if len(references) > 8:
        raise ValueError('千问工作流最多接收 8 张参考图。')
    if edit:
        for item in nodes[encoder_id].get('inputs', []):
            name = str(item.get('name', ''))
            if not name.startswith('images.image_'):
                continue
            index = int(name.rsplit('_', 1)[1]) - 1
            if index >= len(references):
                item['link'] = None
                continue
            link = links.get(str(item.get('link')))
            if not link:
                raise ValueError(f'千问参考图 {index + 1} 的原始连线不可用。')
            source = nodes[str(link[1])]
            seen = set()
            while source.get('type') != 'LoadImage':
                if source['id'] in seen:
                    raise ValueError('千问参考图连线形成循环。')
                seen.add(source['id'])
                upstream = next((links.get(str(x.get('link'))) for x in source.get('inputs', []) if x.get('type') == 'IMAGE' and x.get('link') is not None), None)
                if not upstream:
                    raise ValueError('千问参考图没有连到原文件的 LoadImage。')
                source = nodes[str(upstream[1])]
            # The finalized canvas stores both positional and named widgets.
            # Conversion prefers named values, so update both representations
            # in this in-memory copy or the saved example image can leak in.
            values = source.get('widgets_values')
            if isinstance(values, dict):
                values['image'] = references[index]
            elif isinstance(values, list) and values:
                values[0] = references[index]
            else:
                source['widgets_values'] = [references[index]]
            if isinstance(source.get('widgets_values_named'), dict):
                source['widgets_values_named']['image'] = references[index]
    reachable = set()
    pending = [output_id]
    while pending:
        key = pending.pop()
        if key in reachable:
            continue
        if key not in nodes:
            raise ValueError('千问工作流存在断开的节点。')
        reachable.add(key)
        for item in nodes[key].get('inputs', []):
            if item.get('link') is not None:
                link = links.get(str(item['link']))
                if not link:
                    raise ValueError('千问工作流存在断开的连线。')
                pending.append(str(link[1]))
    for key, node in nodes.items():
        node['mode'] = 0 if key in reachable else 2
    return graph


def _scalar(prompt, node_id, key):
    value = prompt[node_id]['inputs'].get(key)
    seen = set()
    while isinstance(value, (tuple, list)) and len(value) == 2:
        source = str(value[0])
        if source in seen or source not in prompt:
            raise ValueError('参数绑定包含循环或不存在的节点。')
        seen.add(source)
        fields = prompt[source]['inputs']
        name = next((x for x in ('value', 'seed', 'noise_seed', 'text') if x in fields), None)
        if not name:
            return None
        value = fields[name]
    return value


def set_scalar(prompt, node_id, key, value):
    inputs = prompt[node_id]['inputs']
    previous = inputs.get(key)
    if isinstance(previous, (tuple, list)) and len(previous) == 2:
        source = str(previous[0])
        fields = prompt.get(source, {}).get('inputs', {})
        name = next((x for x in ('value', 'seed', 'noise_seed', 'text') if x in fields), None)
        if not name:
            raise ValueError(f'参数 {node_id}.{key} 不是可调标量。')
        set_scalar(prompt, source, name, value)
    else:
        inputs[key] = value


def selector_size(inputs):
    ratio = str(inputs['aspect_ratio']).split()[0].split(':')
    w, h = int(ratio[0]), int(ratio[1])
    multiple = int(inputs['multiple'])
    scale = math.sqrt(float(inputs['megapixels']) * 1024 * 1024 / (w * h))
    return round(w * scale / multiple) * multiple, round(h * scale / multiple) * multiple


def bind_profile(prompt, definition, spec, job_id, references):
    effective = copy.deepcopy(dict(spec))
    prefix = f'MioJobs/{job_id}/{definition.media_type}'
    seed = int(effective.get('seed', -1))
    # rgthree's Seed node has a smaller range than Comfy's uint64 seed.
    seed_limit = (1 << 50) if any(n['class_type'] == 'Seed (rgthree)' for n in prompt.values()) else 0x7FFFFFFFFFFFFFFF
    if seed < 0:
        seed = secrets.randbelow(seed_limit)
    if seed > seed_limit:
        raise ValueError('随机种子超过当前工作流 Seed 节点支持的范围。')
    effective['seed'] = seed
    effective['runtime_workflow_filename'] = definition.filename
    qwen = definition.id == QWEN_ID
    if qwen:
        encoders = [k for k, n in prompt.items() if n['class_type'] == 'TextEncodeQwenImage21']
        if len(encoders) != 1:
            raise ValueError('千问工作流必须选中一个已有的编码分支。')
        encoder = encoders[0]
        set_scalar(prompt, encoder, 'prompt', str(spec.get('prompt', '')))
        set_scalar(prompt, encoder, 'negative_prompt', str(spec.get('negative_prompt', '')))
        effective['workflow_mode'] = 'reference_image' if references else 'text_to_image'
        for key, node in prompt.items():
            if node['class_type'] == 'KSampler':
                set_scalar(prompt, key, 'seed', seed)
                for field in ('steps', 'cfg'):
                    if spec.get(field) is not None:
                        set_scalar(prompt, key, field, spec[field])
                    effective[field] = node['inputs'][field]
            if node['class_type'] == 'EmptyLatentImage':
                node['inputs']['batch_size'] = int(spec.get('batch_size') or 1)
            if node['class_type'] == 'SaveImage':
                node['inputs']['filename_prefix'] = prefix
            if node['class_type'] == 'RandomNoise':
                set_scalar(prompt, key, 'noise_seed', seed)
        schedules = [n for n in prompt.values() if n['class_type'] == 'ViggleTurboSigmas']
        if schedules:
            steps = len(str(schedules[0]['inputs']['nodes']).split(','))
            if spec.get('steps') is not None and int(spec['steps']) != steps:
                raise ValueError('千问 Viggle 自定义采样沿用原工作流的 sigma 序列，请省略 steps。')
            if spec.get('cfg') is not None and float(spec['cfg']) != 1:
                raise ValueError('千问当前使用原工作流 BasicGuider，请省略 CFG 或使用 1。')
            effective.update(steps=steps, cfg=1.0)
    else:
        conditioners = [(k, n) for k, n in prompt.items() if n['class_type'] in {
            'MiniMaxH3ReferenceToVideo', 'MiniMaxH3ImageToVideo'}]
        if not conditioners or len(references) != 1:
            raise ValueError('MINIMAX H3 双采样视频需要一张参考图及原有 H3 视频条件节点。')
        if len({n['class_type'] for _, n in conditioners}) != 1:
            raise ValueError('H3 工作流混用了参考图与首尾帧模型条件，请检查原文件。')
        key, conditioner = conditioners[0]
        length_ref = conditioner['inputs'].get('length')
        if any(n['inputs'].get('length') != length_ref for _, n in conditioners):
            raise ValueError('H3 两个采样阶段的帧数来源不一致，请检查原文件。')
        for conditioner_id, _ in conditioners:
            set_scalar(prompt, conditioner_id, 'prompt', str(spec.get('prompt', '')))
        loaders = [n for n in prompt.values() if n['class_type'] == 'LoadImage']
        if len(loaders) != 1:
            raise ValueError('8G 视频工作流应仅启用参考图 1，请检查原文件。')
        loaders[0]['inputs']['image'] = references[0]
        if spec.get('steps') is not None or spec.get('cfg') is not None:
            raise ValueError('双采样视频的步数、引导与分段调度沿用原工作流，请不要套用生图采样参数。')
        for node in prompt.values():
            if node['class_type'] == 'RandomNoise':
                node['inputs']['noise_seed'] = seed
        length_id = str(length_ref[0]) if isinstance(length_ref, list) else ''
        expression = prompt.get(length_id)
        if not expression or expression['class_type'] != 'ComfyMathExpression':
            raise ValueError('视频帧数没有使用原文件的时长换算节点。')
        duration_ref = expression['inputs'].get('values.a')
        if not isinstance(duration_ref, list):
            raise ValueError('视频时长绑定不可用。')
        if spec.get('duration_seconds') is not None:
            set_scalar(prompt, str(duration_ref[0]), 'value', float(spec['duration_seconds']))
        effective['duration_seconds'] = float(_scalar(prompt, str(duration_ref[0]), 'value'))
        outputs = [n for n in prompt.values() if n['class_type'] == 'VHS_VideoCombine']
        if not outputs:
            raise ValueError('视频工作流缺少原有 VHS_VideoCombine 输出。')
        effective['fps'] = int(spec.get('fps') or outputs[0]['inputs']['frame_rate'])
        frames = max(5, round(effective['duration_seconds'] * effective['fps']))
        effective['frame_count'] = frames + (5 - frames % 17) % 17
        effective['actual_duration_seconds'] = effective['frame_count'] / effective['fps']
        if spec.get('max_frames') and effective['frame_count'] > spec['max_frames']:
            raise ValueError('按 H3 对齐后的帧数超过 max_frames。')
        fps = effective['fps']
        expression['inputs']['expression'] = f'max(5, round(a * {fps})) + (5 - (max(5, round(a * {fps})) % 17)) % 17'
        for node in outputs:
            node['inputs'].update(filename_prefix=prefix, frame_rate=fps, save_output=True)
    selectors = [n for n in prompt.values() if n['class_type'] == 'ResolutionSelector']
    if len(selectors) != 1:
        raise ValueError('工作流需要一个明确的尺寸选择器。')
    dimensions = selectors[0]['inputs']
    if spec.get('width') is not None or spec.get('height') is not None:
        from .creation_workflows import _set_resolution_selector
        old_width, old_height = selector_size(dimensions)
        width, height = int(spec.get('width') or old_width), int(spec.get('height') or old_height)
        multiple = dimensions['multiple']
        _set_resolution_selector(prompt, width, height)
        dimensions['multiple'] = multiple
    effective['width'], effective['height'] = selector_size(dimensions)
    if not qwen:
        upscaler = next((n for n in prompt.values() if n['class_type'] == 'MinimaxH3LatentUpscaler3D'), None)
        if not upscaler:
            raise ValueError('视频工作流缺少原有 3D 潜空间放大节点。')
        up_id = next(k for k, n in prompt.items() if n is upscaler)
        effective['upscale_factor'] = _scalar(prompt, up_id, 'mode.scale')
    return effective
