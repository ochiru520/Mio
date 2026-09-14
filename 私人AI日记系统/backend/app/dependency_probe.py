"""Explicit, bounded local runtime checks. Listing settings never loads a model."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

_results: dict[tuple, dict] = {}
_lock = threading.Lock()


def _key(kind: str, python: Path, model: Path) -> tuple:
    paths = [python]
    paths.extend(sorted(model.rglob('*')) if model.is_dir() else [model])
    return (kind, str(python.resolve()), str(model.resolve()), tuple(
        (str(p), p.stat().st_size, p.stat().st_mtime_ns)
        for p in paths if p.is_file()
    ))


def cached(kind: str, python: Path, model: Path) -> dict:
    with _lock:
        return dict(_results.get(_key(kind, python, model), {}))


def verify(kind: str, python: Path, model: Path) -> dict:
    if kind == 'genie_runtime':
        code = ('import sys, genie_tts, jieba, numpy, onnxruntime as ort; '
                'assert int(numpy.__version__.split(".")[0]) < 2; '
                'ort.InferenceSession(sys.argv[1], providers=["CPUExecutionProvider"])')
    elif kind == 'whisper':
        code = ('import sys; from faster_whisper import WhisperModel; '
                'WhisperModel(sys.argv[1], device="cpu", compute_type="int8", '
                'local_files_only=True)')
    else:
        raise ValueError('不支持的本地验证项目。')
    key = _key(kind, python, model)
    environment = os.environ.copy()
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1')
    try:
        result = subprocess.run([str(python), '-I', '-c', code, str(model)],
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=60, env=environment,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        outcome = {'ok': result.returncode == 0,
                   'detail': '本地模型加载验证通过。' if result.returncode == 0 else
                   '本地模型加载失败：' + (result.stderr.strip()[-1200:] or f'退出码 {result.returncode}')}
    except (OSError, subprocess.TimeoutExpired) as exc:
        outcome = {'ok': False, 'detail': '本地验证失败：' + str(exc)[:800]}
    with _lock:
        _results[key] = outcome
    return outcome
