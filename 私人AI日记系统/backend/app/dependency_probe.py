"""Explicit, bounded local runtime checks. Listing settings never loads a model."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_results: dict[tuple, dict] = {}
_lock = threading.Lock()
_generation = 0
CACHE_SECONDS = 300
logger = logging.getLogger(__name__)


def _failure_detail(output: str) -> tuple[str, str]:
    if 'MIO_GENIE_DATA_MISSING' in output:
        return 'missing_resources', 'Genie 资源不完整，请使用“修复安装”补齐运行引擎。'
    if 'ModuleNotFoundError' in output or 'ImportError' in output:
        return 'missing_runtime_dependency', '本地 Python 依赖缺失或不兼容，请修复安装后重新验证。'
    if 'UnicodeEncodeError' in output or 'UnicodeDecodeError' in output:
        return 'encoding_error', '本地验证进程遇到字符编码错误，请更新或修复 Mio 后重试。'
    return 'model_load_failed', '本地模型未能加载，请修复安装后重试；详细原因已记录到应用日志。'


def _key(kind: str, python: Path, model: Path) -> tuple:
    runtime = python.parent.parent if python.parent.name.lower() in {'scripts', 'bin'} else python.parent
    roots = [model.parent.parent if kind == 'genie_runtime' and model.parent.name == 'chinese-hubert-base' else model]
    paths = [python, runtime / 'pyvenv.cfg']
    packages = runtime / 'Lib/site-packages'
    paths.append(packages)
    names = ('genie_tts', 'jieba', 'numpy', 'onnxruntime') if kind == 'genie_runtime' else ('faster_whisper', 'ctranslate2', 'tokenizers', 'huggingface_hub', 'av')
    for name in names:
        roots.append(packages / name)
        paths.append(packages / (name + '.py'))
    # Directory mtimes catch package removal, file metadata catches in-place repairs.
    for root in roots:
        if root.is_dir():
            paths.append(root)
            paths.extend(p for p in root.rglob('*') if '__pycache__' not in p.parts and p.suffix != '.pyc')
        else:
            paths.append(root)
    stamps = []
    for p in sorted(set(paths)):
        try:
            info = p.stat()
            stamps.append((str(p), info.st_size, info.st_mtime_ns))
        except OSError:
            stamps.append((str(p), None, None))
    return (kind, str(python.resolve()), str(model.resolve()), tuple(
        stamps
    ))


def cached(kind: str, python: Path, model: Path) -> dict:
    key = _key(kind, python, model)
    with _lock:
        value = dict(_results.get(key, {}))
        if time.monotonic() - value.pop('_cached_at', 0) > CACHE_SECONDS:
            return {}
        return value


def invalidate() -> None:
    global _generation
    with _lock:
        _results.clear()
        _generation += 1


def verify(kind: str, python: Path, model: Path) -> dict:
    if kind == 'genie_runtime':
        code = ('import os, sys; from pathlib import Path; '
                'data = Path(sys.argv[1]).resolve().parent.parent; '
                'assert data.is_dir() and (data / "speaker_encoder.onnx").is_file() '
                'and Path(sys.argv[1]).is_file(), "MIO_GENIE_DATA_MISSING"; '
                'os.environ.update(GENIE_DATA_DIR=str(data), '
                'HUBERT_MODEL_DIR=str(data / "chinese-hubert-base"), '
                'Chinese_G2P_DIR=str(data / "G2P/ChineseG2P"), '
                'English_G2P_DIR=str(data / "G2P/EnglishG2P"), '
                'SV_MODEL=str(data / "speaker_encoder.onnx"), '
                'ROBERTA_MODEL_DIR=str(data / "RoBERTa")); '
                'import genie_tts, jieba, numpy, onnxruntime as ort; '
                'assert int(numpy.__version__.split(".")[0]) < 2; '
                'ort.InferenceSession(sys.argv[1], providers=["CPUExecutionProvider"])')
    elif kind == 'whisper':
        code = ('import sys; from faster_whisper import WhisperModel; '
                'WhisperModel(sys.argv[1], device="cpu", compute_type="int8", '
                'local_files_only=True)')
    else:
        raise ValueError('不支持的本地验证项目。')
    key = _key(kind, python, model)
    with _lock:
        generation = _generation
    environment = os.environ.copy()
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1',
                       TRANSFORMERS_OFFLINE='1')
    try:
        # -I ignores PYTHON* environment variables, so UTF-8 must be explicit.
        result = subprocess.run([str(python), '-I', '-B', '-X', 'utf8', '-c', code, str(model.resolve())],
                                stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=60, env=environment,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode == 0:
            outcome = {'ok': True, 'detail': '本地模型加载验证通过；语音合成效果请到语音设置试听。' if kind == 'genie_runtime'
                       else '本地模型加载验证通过；识别效果请到对应设置测试。', 'error_code': ''}
        else:
            output = (result.stderr or '') + '\n' + (result.stdout or '')
            logger.warning('Local dependency probe failed (%s, exit=%s): %s', kind, result.returncode, output[-8000:])
            error_code, detail = _failure_detail(output)
            outcome = {'ok': False, 'detail': detail, 'error_code': error_code}
    except subprocess.TimeoutExpired:
        logger.warning('Local dependency probe timed out (%s)', kind)
        outcome = {'ok': False, 'detail': '模型加载超过 60 秒，验证已停止。请关闭占用内存的程序后重试。', 'error_code': 'timeout'}
    except OSError:
        logger.warning('Local dependency probe could not start (%s)', kind, exc_info=True)
        outcome = {'ok': False, 'detail': '本地验证进程无法启动，请修复 Python 运行环境后重试。', 'error_code': 'process_start_failed'}
    with _lock:
        outcome['checked_at'] = datetime.now(timezone.utc).isoformat()
        outcome['verification_level'] = 'model_load'
        if generation == _generation:
            for old in list(_results):
                if old[:3] == key[:3]:
                    del _results[old]
            _results[key] = {**outcome, '_cached_at': time.monotonic()}
    return outcome
