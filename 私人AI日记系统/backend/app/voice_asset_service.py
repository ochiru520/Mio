"""Inspect voice originals and derived copies without loading checkpoint code.

Inventory is passive. Explicit selection loads a candidate under a transaction,
checks its identity and generates a short, unplayed sample before keeping it.
"""
from __future__ import annotations

import functools
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
import json
import os
import platform
from pathlib import Path
import threading
import time
import wave
import uuid

_CHANGING = threading.Event()
_SELECTION_CONDITION = threading.Condition()
_SELECTION_READERS = 0
_TRANSACTION_LOCK = threading.Lock()
SELECTION_WAIT_SECONDS = 30.0
_FILE_FIELDS = ('gpt_sovits_gpt_weights', 'gpt_sovits_sovits_weights', 'gpt_sovits_ref_audio',
                'so_vits_svc_model_path', 'so_vits_svc_config_path')
_SELECTION_FIELDS = ('default_voice_profile_id', 'voice_engine', 'local_voice_runtime',
                     'aniflive_model', 'aniflive_voice_profile', 'aniflive_url')
_LABELS = {'mio_v2': '原生 GPT-SoVITS V2', 'genie': 'Genie ONNX', 'aniflive': 'AnifLive V2 加速'}


def require_stable_selection() -> None:
    if _CHANGING.is_set():
        raise ValueError('正在加载并验证音色，请完成后再播放语音；失败会保留原选择。')


@contextmanager
def stable_selection():
    """Keep an ordinary synthesis from observing a candidate transaction."""
    global _SELECTION_READERS
    # StreamingResponse may run successive generator next()/close() calls on
    # different pool threads. Never retain a thread-owned lock across a yield.
    with _SELECTION_CONDITION:
        require_stable_selection()
        _SELECTION_READERS += 1
    try:
        yield
    finally:
        with _SELECTION_CONDITION:
            _SELECTION_READERS -= 1
            if _SELECTION_READERS == 0:
                _SELECTION_CONDITION.notify_all()


@contextmanager
def _selection_transaction():
    from . import companion_service
    from .call_session_service import manager as call_manager
    if call_manager.status().get('active'):
        raise ValueError('请先结束语音通话，再操作模型资源。')
    if not _TRANSACTION_LOCK.acquire(blocking=False):
        raise ValueError('已有音色操作正在进行，请完成后重试；不会排队更改声音。')
    try:
        with _SELECTION_CONDITION:
            _CHANGING.set()
        try:
            # Cancel existing playback before waiting for an in-flight reader;
            # never hold a lifecycle lock while waiting for synthesis to leave.
            companion_service.cancel_speech_playback()
            with _SELECTION_CONDITION:
                if not _SELECTION_CONDITION.wait_for(lambda: _SELECTION_READERS == 0, timeout=SELECTION_WAIT_SECONDS):
                    raise ValueError('仍有语音正在播放或合成，请先停止语音再切换；原选择已保留。')
            yield
        finally:
            with _SELECTION_CONDITION:
                _CHANGING.clear()
                _SELECTION_CONDITION.notify_all()
    finally:
        _TRANSACTION_LOCK.release()


def _resources():
    from . import model_management_service
    return model_management_service


def _read(path: Path) -> dict:
    try:
        _resources()._plain(path)
        # These are small manifests, never deserialize a training checkpoint.
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _path(value) -> str:
    return _resources()._canonical(value) if value else ''


def _file(value) -> bool:
    if not value:
        return False
    try:
        path = Path(str(value))
        _resources()._plain(path)
        return path.is_absolute() and path.is_file() and path.stat().st_size > 0
    except (OSError, ValueError):
        return False


def _args(entry: dict) -> dict:
    args = entry.get('args') or []
    if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
        return {}
    result = {}
    for flag in ('--gpt', '--sovits', '--reference', '--reference-text', '--reference-language', '--voice-profile', '--engines'):
        indexes = [index for index, value in enumerate(args) if value == flag]
        if len(indexes) == 1 and indexes[0] + 1 < len(args):
            result[flag] = args[indexes[0] + 1]
    return result


def _snapshot_matches(profile: dict, source: dict) -> bool:
    pairs = [('gpt_sovits_gpt_weights', 'gpt'), ('gpt_sovits_sovits_weights', 'sovits'),
             ('gpt_sovits_ref_audio', 'reference')]
    return all(_path(profile.get(field)) and _path(profile[field]) == _path(source.get(key)) for field, key in pairs)


def _conversion_source_valid(profile) -> bool:
    return (isinstance(profile, dict) and isinstance(profile.get('id'), str)
            and all(isinstance(profile.get(key), str) and Path(profile[key]).is_absolute()
                    for key in ('gpt', 'sovits', 'reference')))


def conversions(config: dict) -> list[dict]:
    """Track known managed job snapshots and the registered external Anif entry.

    Incomplete jobs also depend on their originals, so cleanup cannot invalidate
    a retry. Arbitrary external directory trees are never traversed.
    """
    from . import local_voice_engines
    root = Path(_resources().settings.voice_training_dir).absolute() / 'aniflive-managed'
    owner = _read(root / '.mio-anif-owner.json')
    installed = _read(root / 'installed.json')
    engines = _read(local_voice_engines.manifest_path()).get('engines')
    entry = engines.get('aniflive') if isinstance(engines, dict) else {}
    entry = entry if isinstance(entry, dict) else {}
    rows = []
    if owner == {'schema': 1, 'owner': 'mio-aniflive-managed'}:
        jobs = root / 'jobs'
        try:
            _resources()._plain(jobs)
            children = list(jobs.iterdir()) if jobs.is_dir() else []
        except (OSError, ValueError):
            children = []
        for job in sorted(children):
            if len(job.name) != 32 or any(char not in '0123456789abcdef' for char in job.name):
                continue
            value = _read(job / 'job.json')
            profile = value.get('profile')
            if not _conversion_source_valid(profile):
                continue
            state = _read(job / 'status.json')
            identifier = str(profile.get('id') or '')
            registered = bool(entry and installed.get('job_id') == job.name and installed.get('registration') == entry)
            rows.append(dict(kind='aniflive', profile_id=identifier, path=str(job), managed=True,
                             registered=registered, ready=state.get('stage') == 'ready' and state.get('ready') is True,
                             state=str(state.get('stage') or 'interrupted'), source=profile,
                             source_files=[str(profile[key]) for key in ('gpt', 'sovits', 'reference') if profile.get(key)],
                             cleanup='anif_setup_uninstall', cleanup_label='在 Anif 安装面板卸载加速副本（保留原始音色）'))
    args = _args(entry)
    if args and not any(row['registered'] for row in rows):
        source = {'id': args.get('--voice-profile', ''), 'gpt': args.get('--gpt', ''),
                  'sovits': args.get('--sovits', ''), 'reference': args.get('--reference', ''),
                  'reference_text': args.get('--reference-text', ''), 'reference_language': args.get('--reference-language', '')}
        conversion_manifest = _read(Path(args['--engines']) / 'v2-manifest.json') if args.get('--engines') else {}
        weights = conversion_manifest.get('source_weights') or {}
        source['weight_sha256'] = {key: weights[key].get('sha256') for key in ('gpt', 'sovits')
                                   if isinstance(weights, dict) and isinstance(weights.get(key), dict)}
        rows.append(dict(kind='aniflive', profile_id=str(source['id']), path=args.get('--engines', ''), managed=False,
                         registered=True, ready=conversion_manifest.get('model_family') == 'GPT-SoVITS V2',
                         state='external', source=source, source_files=[source[key] for key in ('gpt', 'sovits', 'reference') if source[key]],
                         cleanup='', cleanup_label='外部加速副本仅保留引用，不由 Mio 删除'))
    return rows


def referenced_originals(config: dict) -> dict[str, list[str]]:
    result = {}
    for row in conversions(config):
        for path in row['source_files']:
            result.setdefault(_path(path), []).append(row['profile_id'] or 'Anif 加速副本')
    return result


def require_known_conversion_references() -> None:
    """A damaged dependency record is not evidence that originals are unused."""
    from . import local_voice_engines
    root = Path(_resources().settings.voice_training_dir) / 'aniflive-managed'
    _resources()._plain(root)
    if root.is_dir() and any(root.iterdir()):
        if _read(root / '.mio-anif-owner.json') != {'schema': 1, 'owner': 'mio-aniflive-managed'}:
            raise ValueError('Anif 转换归属记录无法确认，请先修复或在 Anif 面板卸载；原始音色未删除。')
        jobs = root / 'jobs'
        _resources()._plain(jobs)
        for job in jobs.iterdir() if jobs.is_dir() else []:
            if job.is_dir() and (job / 'job.json').exists() and not _conversion_source_valid(_read(job / 'job.json').get('profile')):
                raise ValueError('Anif 转换的原件引用记录无法读取，请先修复或卸载加速副本；原始音色未删除。')
    registry = local_voice_engines.manifest_path()
    if registry.exists() and not isinstance(_read(registry).get('engines'), dict):
        raise ValueError('语音引擎引用记录无法读取，请先修复配置；原始音色未删除。')


@functools.lru_cache(maxsize=64)
def _digest_cached(path: str, size: int, modified: int) -> str:
    return _resources()._digest(Path(path))


def _digest(path: str) -> str:
    info = Path(path).stat()
    return _digest_cached(path, info.st_size, info.st_mtime_ns)


def _asset_path() -> Path:
    return Path(_resources().settings.companion_dir) / 'voice-assets.json'


def _asset_records() -> dict:
    value = _read(_asset_path())
    records = value.get('voices')
    if _asset_path().exists() and (value.get('schema') != 1 or not isinstance(records, dict)
            or any(not isinstance(record, dict)
                   or 'genie' in record and not isinstance(record['genie'], dict)
                   or 'verified' in record and not isinstance(record['verified'], dict)
                   for record in records.values())):
        raise ValueError('音色身份记录无法读取，请先修复 voice-assets.json；原文件和声音选择均保留。')
    return value.get('voices', {}) if value.get('schema') == 1 and isinstance(value.get('voices'), dict) else {}


def _write_assets(records: dict) -> None:
    path = _asset_path(); _resources()._plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps({'schema': 1, 'voices': records}, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def profile_record(profile_id: str) -> dict:
    return _asset_records().get(profile_id) or {}


def forget_profile(profile_id: str) -> None:
    records = _asset_records()
    if profile_id in records:
        records.pop(profile_id)
        _write_assets(records)


def relocated_records(records: dict, source: Path, destination: Path, targets: list[Path], config: dict) -> dict:
    """Move known Genie identity with the verified model copy, not its originals."""
    result = json.loads(json.dumps(records))
    for identifier, record in result.items():
        genie = record.get('genie')
        if not isinstance(genie, dict) or not genie.get('path'):
            continue
        candidate = Path(genie['path']).absolute()
        if any(candidate.is_relative_to(target) for target in targets):
            copied = destination / candidate.relative_to(source)
            _resources()._plain(copied)
            if _genie_identity(copied) != genie.get('files'):
                raise ValueError('迁移后的 Genie 音色副本与原身份不符；原文件和配置保留。')
            genie['path'] = str(copied)
            profile = (config.get('voice_profiles') or {}).get(identifier)
            if isinstance(profile, dict):
                genie['source'] = _source_identity(profile)
            # A copied model is not a new successful load on the new location.
            record.pop('verified', None)
    return result


def _source_identity(profile: dict) -> dict:
    result = {key: str(profile.get(key) or '') for key in ('gpt_sovits_prompt_text', 'gpt_sovits_prompt_language')}
    for field in _FILE_FIELDS[:3]:
        value = profile.get(field)
        result[field] = {'path': _path(value), 'sha256': _digest(str(value)) if _file(value) else ''}
    return result


def _genie_identity(directory: Path) -> dict:
    from . import genie_tts_service
    if not genie_tts_service._character_ready(directory):
        raise ValueError('Genie 音色副本不完整。')
    return {name: _digest(str(directory / name)) for name in genie_tts_service.REQUIRED_CHARACTER_FILES}


def _stored_genie(profile_id: str, profile: dict) -> Path | None:
    value = profile_record(profile_id).get('genie')
    if not isinstance(value, dict):
        return None
    if value.get('source') != _source_identity(profile):
        raise ValueError('音色原件或参考内容已变化，请重新转换并验证 Genie；不会继续使用旧副本。')
    path = Path(str(value.get('path') or ''))
    root = Path(_resources().settings.voice_training_dir) / 'models/genie'
    _resources()._plain(path)
    if not path.is_absolute() or not path.resolve().is_relative_to(root.resolve()) or _genie_identity(path) != value.get('files'):
        raise ValueError('Genie 音色副本已变化或缺失，请重新验证；不会改用默认声音。')
    return path


def _legacy_genie(profile_id: str, profile: dict, config: dict) -> Path | None:
    from . import genie_tts_service
    if (config.get('voice_engine') != 'gpt_sovits' or config.get('local_voice_runtime') != 'genie'
            or config.get('default_voice_profile_id') != profile_id
            or not _file(profile.get('gpt_sovits_ref_audio')) or not str(profile.get('gpt_sovits_prompt_text') or '').strip()
            or _file(profile.get('gpt_sovits_gpt_weights')) and _file(profile.get('gpt_sovits_sovits_weights'))):
        return None
    root = Path(_resources().settings.voice_training_dir) / 'models/genie'
    for directory in (root / 'mio-v1', root / 'default-v2'):
        _resources()._plain(directory)
        if genie_tts_service._character_ready(directory):
            return directory
    return None


def _remember_genie(profile_id: str, profile: dict, directory: Path) -> None:
    records = _asset_records()
    record = dict(records.get(profile_id) or {})
    record['genie'] = {'path': str(directory), 'source': _source_identity(profile), 'files': _genie_identity(directory)}
    records[profile_id] = record
    _write_assets(records)


def _remember_verification(profile_id: str, engine: str, identity: dict) -> None:
    records = _asset_records()
    record = dict(records.get(profile_id) or {})
    verified = dict(record.get('verified') or {})
    verified[engine] = {'source': identity, 'checked_at': datetime.now(timezone.utc).isoformat(),
                        'device': _device_identity(),
                        'model_version': 'GPT-SoVITS V2' if engine in {'mio_v2', 'aniflive'} else 'Genie ONNX'}
    record['verified'] = verified; records[profile_id] = record
    _write_assets(records)


def _device_identity() -> str:
    # No device name or hardware identifier is exposed in the UI or diagnostics.
    raw = f'{platform.node()}:{uuid.getnode()}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def genie_model_override(config: dict) -> Path | None:
    """Pin a known legacy voice once; never guess a different profile's voice."""
    identifier = str(config.get('voice_profile_id') or config.get('default_voice_profile_id') or '')
    profile = (config.get('voice_profiles') or {}).get(identifier) or config
    try:
        stored = _stored_genie(identifier, profile)
    except ValueError:
        if config.get('_voice_asset_revalidate'):
            return None
        raise
    if stored:
        return stored
    from . import companion_service
    saved = companion_service.load_config()
    current = (saved.get('voice_profiles') or {}).get(identifier) or {}
    if current and _source_identity(current) == _source_identity(profile):
        legacy = _legacy_genie(identifier, profile, saved)
        if legacy:
            _remember_genie(identifier, profile, legacy)
            return legacy
    return None


def _variant(engine: str, state: str, reason: str, *, can_select=False, verified=False) -> dict:
    return dict(engine=engine, label=_LABELS[engine], state=state, compatible=state == 'available',
                can_select=can_select, reason=reason, verified=verified)


def _genie_markers() -> list[Path]:
    import os
    root = Path(_resources().settings.voice_training_dir) / 'models/genie'
    if not root.is_dir():
        return []
    result = []
    try:
        _resources()._plain(root)
        for directory, folders, files in os.walk(root, followlinks=False):
            retained = []
            for name in folders:
                try:
                    _resources()._plain(Path(directory) / name)
                    retained.append(name)
                except ValueError:
                    pass
            folders[:] = retained
            if 'mio-genie-v2.json' in files:
                result.append(Path(directory) / 'mio-genie-v2.json')
    except (OSError, ValueError):
        return []
    return result


def inspect_profile(profile_id: str, config: dict, *, known_conversions=None) -> dict:
    from . import local_voice_engines
    profiles = config.get('voice_profiles') or {}
    profile = profiles.get(profile_id)
    if not isinstance(profile, dict):
        raise ValueError('音色不存在。')
    linked = []
    for row in conversions(config) if known_conversions is None else known_conversions:
        if row['profile_id'] == profile_id or _snapshot_matches(profile, row['source']):
            linked.append(row)
    missing = []
    names = {'gpt_sovits_gpt_weights': 'GPT 权重', 'gpt_sovits_sovits_weights': 'SoVITS 权重',
             'gpt_sovits_ref_audio': '参考音频', 'so_vits_svc_model_path': '变声权重', 'so_vits_svc_config_path': '变声配置'}
    base_engine = str(profile.get('engine') or 'gpt_sovits')
    fields = _FILE_FIELDS[:3] if base_engine == 'gpt_sovits' else _FILE_FIELDS[3:]
    for field in fields:
        if not _file(profile.get(field)):
            missing.append(names[field])
    prompt = str(profile.get('gpt_sovits_prompt_text') or '').strip()
    if base_engine == 'gpt_sovits' and not prompt:
        missing.append('参考音频准确原文')
    source_ok = base_engine == 'gpt_sovits' and not missing
    selected = config.get('default_voice_profile_id') == profile_id
    active = ('aniflive' if config.get('voice_engine') == 'aniflive' and config.get('aniflive_voice_profile') == profile_id
              else config.get('local_voice_runtime') if selected and config.get('voice_engine') == 'gpt_sovits' else '')
    external_engine = config.get('voice_engine') if selected and config.get('voice_engine') not in {'gpt_sovits', 'aniflive'} else ''
    external_labels = {'gpt_sovits_v5': 'GPT-SoVITS v5 外部服务（音色身份待确认）',
                       'indextts2': 'IndexTTS 外部服务（音色身份待确认）', 'cloud': '云端语音（独立服务声音）'}
    candidate = dict(config, default_voice_profile_id=profile_id, voice_profile_id=profile_id, **profile)
    variants = []
    installed_native = False
    if source_ok:
        try:
            local_voice_engines.load_entry('mio_v2', candidate)
            installed_native = True
        except (OSError, ValueError):
            pass
    variants.append(_variant('mio_v2', 'available' if source_ok and installed_native else 'missing',
        '配套文件齐全；切换时实际加载并验证 V2，失败保留原选择。' if source_ok and installed_native else
        '请补齐原始权重、参考音频与原文，并安装原生 V2 运行环境。', can_select=source_ok and installed_native))
    # An ONNX voice must be matched to BOTH original hashes at selection time.
    # File extensions or the profile name alone never establish compatibility.
    genie_markers = _genie_markers()
    try:
        stored_genie = _stored_genie(profile_id, profile)
        genie_error = ''
    except ValueError as exc:
        stored_genie, genie_error = None, str(exc)
    legacy_genie = _legacy_genie(profile_id, profile, config) if not genie_error else None
    genie_possible = bool(stored_genie or legacy_genie or source_ok and genie_markers and _matching_genie(profile))
    variants.append(_variant('genie', 'available' if genie_possible else 'needs_conversion',
        '旧版已选 Genie 音色包可继续使用；原始权重未导入，切换时核对固定副本并发声验证。' if legacy_genie or stored_genie and not source_ok else
        '切换时检查 ONNX 副本与原始双权重的哈希，并生成验证音频。' if genie_possible else
        genie_error or '需要与这套原始权重配套的 Genie ONNX 音色；不会替换为默认声音。', can_select=genie_possible))
    matching = next((row for row in linked if row['registered'] and row['ready'] and _snapshot_matches(profile, row['source'])), None)
    variants.append(_variant('aniflive', 'available' if source_ok and matching else 'needs_conversion',
        '已有关联加速副本；切换时核对原始权重哈希和实际服务身份。' if source_ok and matching else
        '请在 Anif 安装面板选择此音色编译并试听；原始权重保留。', can_select=bool(source_ok and matching), verified=False))
    if base_engine != 'gpt_sovits':
        variants = [_variant(engine, 'incompatible', '此档案为变声音色，不是 GPT-SoVITS V2 配套原声；保留原配置，请在声音设置使用。') for engine in _LABELS]
    verified = profile_record(profile_id).get('verified') or {}
    valid_receipts = {}
    if isinstance(verified, dict):
        for engine, receipt in verified.items():
            if (isinstance(receipt, dict) and receipt.get('source') == _source_identity(profile)
                    and receipt.get('device') == _device_identity()):
                valid_receipts[engine] = receipt
        for variant in variants:
            variant['verified'] = variant['engine'] in valid_receipts
    # Without loading, a .pth/.ckpt pair is only a declared/unknown family.
    version = '旧版 Genie ONNX 音色包（原始权重未导入）' if not source_ok and (stored_genie or legacy_genie) else 'GPT-SoVITS V2 加速副本（原件切换时核验）' if matching else '待加载验证' if base_engine == 'gpt_sovits' else 'so-vits-svc（版本待验证）'
    if valid_receipts.get('mio_v2') or valid_receipts.get('aniflive'):
        version = 'GPT-SoVITS V2（本机加载发声通过）'
    elif valid_receipts.get('genie'):
        version = 'Genie ONNX（本机加载发声通过；原始权重版本待验证）'
    language = str(profile.get('gpt_sovits_text_language') or 'auto')
    language_label = {'all_zh': 'zh', 'all_ja': 'ja'}.get(language, language)
    return dict(id=profile_id, name=profile.get('name') or profile_id, selected=selected,
                active=bool(active or external_engine), active_engine=active or external_engine,
                engine_label=external_labels.get(external_engine) or _LABELS.get(active, '音色原始文件'),
                model_version=version, languages=['zh', 'ja'] if base_engine == 'gpt_sovits' else [],
                language_note='中文、日语为现有引擎支持范围；此音色实际发音需试听确认。',
                selected_language=language_label, reference_language=profile.get('gpt_sovits_prompt_language') or 'unknown',
                missing_files=missing, compatibility=variants, variants=variants,
                verification={key: {'checked_at': value.get('checked_at'), 'model_version': value.get('model_version')} for key, value in valid_receipts.items()},
                conversions=[{key: value for key, value in row.items() if key not in {'source', 'source_files'}} for row in linked],
                conversion_cleanup='请到 Anif 安装面板单独卸载加速环境与转换副本，原始音色保留。' if linked else '',
                external_engine=external_engine)


def _matching_genie(profile: dict) -> bool:
    from . import genie_tts_service
    wanted = {name: _digest(profile[field]) for name, field in
              (('gpt_sha256', 'gpt_sovits_gpt_weights'), ('sovits_sha256', 'gpt_sovits_sovits_weights'))}
    for marker in _genie_markers():
        manifest = _read(marker)
        if all(manifest.get(key) == value for key, value in wanted.items()) and genie_tts_service._character_ready(marker.parent):
            return True
    return False


def _verify_audio(content: bytes) -> None:
    try:
        with wave.open(BytesIO(content), 'rb') as wav:
            frames = wav.readframes(wav.getnframes())
            valid = wav.getnframes() > 0 and wav.getnchannels() in {1, 2} and wav.getsampwidth() == 2 and any(frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError('音色验证没有生成有效音频，已保留原选择。') from exc
    if not valid:
        raise ValueError('音色验证返回空白音频，已保留原选择。')


def _verify_candidate(engine: str, candidate: dict) -> None:
    from . import local_voice_engines, mio_v2_tts_service, aniflive_tts_service, genie_tts_service
    if engine == 'genie':
        _verify_audio(genie_tts_service.synthesize_wav('你好。', dict(candidate, gpt_sovits_text_language='zh'), timeout=120))
        return
    local_voice_engines.manager.start(engine, candidate)
    service = mio_v2_tts_service if engine == 'mio_v2' else aniflive_tts_service
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        health = service.runtime_status(candidate)
        ready = health.get('service_ready') if engine == 'mio_v2' else health.get('selected_identity_ready')
        if ready:
            if engine == 'aniflive':
                hashes = health.get('weight_sha256') or {}
                if any(hashes.get(key) != _digest(candidate[field]) for key, field in
                       (('gpt', 'gpt_sovits_gpt_weights'), ('sovits', 'gpt_sovits_sovits_weights'))):
                    raise ValueError('Anif 实际权重身份与所选音色不一致，已保留原选择。')
            else:
                metadata = mio_v2_tts_service._health(candidate)
                mio_v2_tts_service._validate_profile(metadata, candidate)
                hashes = metadata.get('weight_sha256') or {}
                if any(hashes.get(key) != _digest(candidate[field]) for key, field in
                       (('gpt', 'gpt_sovits_gpt_weights'), ('sovits', 'gpt_sovits_sovits_weights'))):
                    raise ValueError('V2 服务未确认所选音色的实际双权重身份，已保留原选择。')
            _verify_audio(service.synthesize_wav('你好。', candidate, language='zh'))
            return
        owned = local_voice_engines.manager.status(engine, candidate)
        if not owned.get('managed_running') or owned.get('managed_last_error'):
            raise ValueError('音色加载失败，已保留原选择；请在声音设置查看加载诊断。')
        time.sleep(.25)
    raise ValueError('音色加载验证超时，已保留原选择。')


def select(profile_id: str, engine: str) -> dict:
    """Explicit transactional selection; never runs during upgrade/inventory."""
    from . import companion_service, local_voice_engines, genie_tts_service
    resources = _resources()
    if engine not in _LABELS:
        raise ValueError('此音色运行方式不支持直接切换；外部引擎请保留并在声音设置配置。')
    with _selection_transaction(), resources._LOCK, local_voice_engines.manager.lifecycle_lock, genie_tts_service._worker_lock:
        resources._idle('voice')
        before = companion_service.load_config()
        old_id = str(before.get('default_voice_profile_id') or '')
        old_profile = (before.get('voice_profiles') or {}).get(old_id) or {}
        if old_profile and not profile_record(old_id).get('genie'):
            legacy = _legacy_genie(old_id, old_profile, before)
            if legacy:
                _remember_genie(old_id, old_profile, legacy)
        profile = (before.get('voice_profiles') or {}).get(profile_id)
        info = inspect_profile(profile_id, before)
        source_identity = _source_identity(profile)
        option = next(row for row in info['variants'] if row['engine'] == engine)
        if not option['can_select']:
            raise ValueError(option['reason'])
        if engine == 'genie':
            try:
                known_genie = _stored_genie(profile_id, profile)
            except ValueError:
                known_genie = None
            if not known_genie and (not all(_file(profile.get(field)) for field in _FILE_FIELDS[:2]) or not _matching_genie(profile)):
                raise ValueError('没有与此音色双权重匹配的完整 Genie ONNX 副本；不会改用默认声音。')
        changes = {'default_voice_profile_id': profile_id}
        if engine in {'mio_v2', 'genie'}:
            changes.update(voice_engine='gpt_sovits', local_voice_runtime=engine)
        else:
            linked = next(row for row in conversions(before) if row['registered'] and row['ready'] and _snapshot_matches(profile, row['source']))
            expected = linked['source'].get('weight_sha256') or {}
            if any(expected.get(key) != _digest(profile[field]) for key, field in
                   (('gpt', 'gpt_sovits_gpt_weights'), ('sovits', 'gpt_sovits_sovits_weights'))):
                raise ValueError('原始权重与 Anif 加速副本不一致，请重新编译；原选择保留。')
            source = linked['source']
            if (source.get('reference_sha256') and source['reference_sha256'] != _digest(profile['gpt_sovits_ref_audio'])
                    or source.get('reference_text') and source['reference_text'] != profile.get('gpt_sovits_prompt_text')
                    or source.get('reference_language') and source['reference_language'] != {'all_zh': 'zh', 'all_ja': 'ja'}.get(profile.get('gpt_sovits_prompt_language'), profile.get('gpt_sovits_prompt_language'))):
                raise ValueError('参考音频、原文或语言已改变，请重新编译 Anif；原选择保留。')
            from . import local_voice_engines
            registration = (_read(local_voice_engines.manifest_path()).get('engines') or {}).get('aniflive') or {}
            changes.update(voice_engine='aniflive', aniflive_model='mio-v2-aniflive',
                           aniflive_voice_profile=profile_id, aniflive_url=registration.get('url'))
        previous = {key: before.get(key) for key in changes}
        old_child = local_voice_engines.manager.child
        old_owned = local_voice_engines.manager.engine if old_child is not None and old_child.poll() is None else ''
        old_genie = genie_tts_service.runtime_status().get('running')
        interim = None
        try:
            companion_service.cancel_speech_playback()
            companion_service._stop_owned_original_voice()
            local_voice_engines.manager.stop_all()
            interim = companion_service.save_config(changes)
            candidate = dict(interim, voice_profile_id=profile_id, **profile)
            if engine == 'genie':
                candidate['_voice_asset_revalidate'] = True
            _verify_candidate(engine, candidate)
            if _source_identity(profile) != source_identity:
                raise ValueError('加载验证期间音色原件已变化，请重新选择；原选择保留。')
            if engine == 'genie':
                _remember_genie(profile_id, profile, genie_tts_service._resolve_character_model(candidate))
            current = companion_service.load_config()
            if any(current.get(key) != interim.get(key) for key in _SELECTION_FIELDS) or current.get('voice_profiles') != interim.get('voice_profiles'):
                raise ValueError('验证期间音色设置已变化，请重新选择；未覆盖较新的设置。')
            _remember_verification(profile_id, engine, source_identity)
            return dict(ok=True, profile_id=profile_id, engine=engine, verified=True,
                        message='已加载并生成验证音频，当前使用此音色；其他原始音色和加速副本均保留。')
        except BaseException as exc:
            stop_failed = False
            try:
                local_voice_engines.manager.stop_all()
                genie_tts_service.stop_worker()
            except (OSError, ValueError):
                stop_failed = True
            rollback_failed = False
            current = companion_service.load_config()
            expected = interim if interim is not None else changes
            if interim is not None or all(current.get(key) == changes.get(key) for key in changes):
                # Restore only fields this transaction changed. Do not discard
                # unrelated user edits or a newer explicit selection.
                if all(current.get(key) == expected.get(key) for key in changes):
                    try:
                        companion_service.save_config(previous)
                    except (OSError, ValueError):
                        rollback_failed = True
                    if old_owned and not stop_failed and not rollback_failed:
                        try:
                            local_voice_engines.manager.start(old_owned, companion_service.load_config())
                        except (OSError, ValueError):
                            pass  # Original selection stays saved, next playback can retry.
                    elif old_genie and not stop_failed and not rollback_failed:
                        try:
                            genie_tts_service.start_worker()
                        except (OSError, ValueError):
                            pass
            if rollback_failed:
                raise ValueError('音色选择保存或恢复失败，请检查磁盘权限后重新选择；暂未确认恢复原音色。') from exc
            if stop_failed:
                raise ValueError('音色验证失败，原选择已保留；候选进程未能完全停止，请先在声音设置停止服务后重试。') from exc
            if isinstance(exc, ValueError):
                raise
            raise ValueError('音色加载或发声验证失败，已保留原选择；请在声音设置检查文件和运行环境。') from exc
