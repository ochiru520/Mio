"""Bounded local discovery in configured workflow folders and authorized data roots."""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from . import agent_file_service as files, creation_custom
from .config import settings

SKIP = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'models', 'python', 'python_embeded', 'python_embedded'}


def linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def walk(roots: list[Path], query: str, *, suffixes: set[str], limit: int = 100) -> dict:
    deadline = time.monotonic() + 4
    pending = [(root, 0) for root in roots]
    visited, matches, count = set(), [], 0
    truncated = False
    query = query.strip().casefold()
    while pending:
        if count >= 10000 or time.monotonic() >= deadline or len(matches) >= limit:
            truncated = True
            break
        directory, depth = pending.pop(0)
        try:
            canonical = directory.resolve(strict=True)
            if canonical in visited or linked(directory):
                continue
            visited.add(canonical)
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count >= 10000 or time.monotonic() >= deadline:
                        truncated = True
                        break
                    path = Path(entry.path)
                    if entry.name.startswith('.') or entry.name.casefold() in SKIP or linked(path):
                        continue
                    if files._private_path(path.resolve()) and not path.resolve().is_relative_to(files.workspace()):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < 8:
                            pending.append((path, depth + 1))
                        else:
                            truncated = True
                    elif path.suffix.casefold() in suffixes and (not query or query in entry.name.casefold() or fnmatch.fnmatchcase(entry.name.casefold(), query)):
                        matches.append({'path': str(path.resolve()), 'name': entry.name, 'size': entry.stat(follow_symlinks=False).st_size})
                        if len(matches) >= limit:
                            truncated = True
                            break
        except (OSError, ValueError):
            continue
    return {'files': matches, 'truncated': truncated, 'scanned_entries': count, 'roots': [str(p) for p in roots]}


def search_files(query: str = '', path: str = '', limit: int = 100) -> dict:
    roots = [files.resolve_read(path)] if path else [Path(p) for p in files.roots()]
    if any(not p.is_dir() for p in roots):
        raise ValueError('请选择要搜索的文件夹。')
    return walk(roots, query, suffixes=files.READABLE, limit=max(1, min(limit, 100)))


def workflow_roots() -> list[Path]:
    root = settings.comfyui_root.resolve()
    values = [root/'my_workflows', root/'user/default/workflows', root/'workflows']
    # Node examples are useful but model/runtime trees are never scanned.
    nodes = root/'custom_nodes'
    if nodes.is_dir() and not linked(nodes):
        for child in list(nodes.iterdir())[:300]:
            if child.is_dir() and not linked(child):
                values.extend([child/'example_workflows', child/'examples'])
    values = [p for p in values if p.is_dir() and p.resolve().is_relative_to(root)
              and not any(linked(part) for part in [p, *p.parents] if part != root and part.is_relative_to(root))]
    values.extend(Path(p) for p in files.roots())
    return list(dict.fromkeys(p for p in values if p.is_dir() and not linked(p)))


def read_workflow(path: str) -> dict:
    candidate = Path(path)
    resolved = candidate.resolve(strict=True)
    roots = workflow_roots()
    if candidate.suffix.casefold() != '.json' or not any(resolved.is_relative_to(root.resolve()) for root in roots):
        raise ValueError('请先在文件权限中选择并授权该工作流所在目录。')
    if any(linked(part) for part in [candidate, *candidate.parents] if part.exists()):
        raise ValueError('不能通过链接读取工作流。')
    if files._private_path(resolved) and not resolved.is_relative_to(files.workspace()):
        raise ValueError('不能读取 Mio 私人配置。')
    if any(part.startswith('.') for part in resolved.parts) or ':' in resolved.name or linked(candidate):
        raise ValueError('不能读取隐藏文件或链接。')
    with resolved.open('rb') as handle:
        content = handle.read(2_000_001)
    if len(content) > 2_000_000:
        raise ValueError('工作流不能超过 2 MB。')
    graph = json.loads(content.decode('utf-8-sig'))
    if not isinstance(graph, dict) or not (isinstance(graph.get('nodes'), list) or any(isinstance(n, dict) and 'class_type' in n for n in graph.values())):
        raise ValueError('该 JSON 不是 ComfyUI 工作流。')
    return {'path': str(resolved), 'name': resolved.name, 'prompt': graph, 'sha256': hashlib.sha256(content).hexdigest()}


def discover_workflows(query: str = '', limit: int = 100) -> dict:
    result = walk(workflow_roots(), query, suffixes={'.json'}, limit=max(1, min(limit, 100)))
    valid = []
    for item in result['files']:
        if item['size'] > 2_000_000:
            continue
        try:
            graph = read_workflow(item['path'])
            valid.append({**item, 'sha256': graph['sha256'], 'format': 'canvas' if 'nodes' in graph['prompt'] else 'api'})
        except (ValueError, OSError, UnicodeError):
            continue
    return {**result, 'files': valid}


async def import_local_workflow(path: str, label: str = '') -> dict:
    from .routes.creation import creation_preview_workflow
    source = await asyncio.to_thread(read_workflow, path)
    prepared = await creation_preview_workflow(creation_custom.WorkflowPreview(prompt=source['prompt']))
    payload = creation_custom.WorkflowImport(label=label or Path(source['name']).stem,
        media_type=prepared['media_type'], prompt=prepared['prompt'], bindings=prepared['bindings'])
    result = creation_custom.import_workflow(payload)
    return {'workflow': result, 'source_path': source['path'], 'source_sha256': source['sha256'], 'notes': prepared.get('notes', [])}
