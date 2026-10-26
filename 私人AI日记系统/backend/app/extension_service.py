"""Persistent, versioned data tools and declarative panels; never grants access."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import db, maintenance_service
from .repositories import extensions_repository as repository
from .config import settings
from .extension_worker import validate_source

_lock = threading.RLock()
_running = {}
_round_catalogs = {}
API_VERSION = 1
RUNTIME = {'os_sandbox': False, 'mode': 'restricted_data_worker', 'host_access': 'authorized_proxy_only'}


class ConflictError(ValueError):
    pass


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PanelBlock(Input):
    type: Literal['text', 'metric', 'table']
    text: str = Field(default='', max_length=10000)
    label: str = Field(default='', max_length=200)
    value: str | int | float = ''
    columns: list[str] = Field(default_factory=list, max_length=20)
    rows: list[list[str | int | float | bool | None]] = Field(default_factory=list, max_length=500)


class Panel(Input):
    title: str = Field(min_length=1, max_length=160)
    blocks: list[PanelBlock] = Field(min_length=1, max_length=40)


class Manifest(Input):
    id: str = Field(default='', pattern=r'^$|^ext_[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=1000)
    kind: Literal['python', 'panel']
    version: str = Field(pattern=r'^\d{1,4}\.\d{1,4}\.\d{1,4}$')
    api_version: int = 1
    parameters: dict = Field(default_factory=lambda: {'type': 'object', 'properties': {}, 'additionalProperties': False})
    result_schema: dict = Field(default_factory=lambda: {'type': 'object'})
    permissions: list[Literal['file.read', 'artifact.write']] = Field(default_factory=list, max_length=2)
    file_inputs: list[str] = Field(default_factory=list, max_length=10)
    panel: Panel | None = None
    timeout_seconds: float = Field(default=10, ge=0.1, le=30)

    @model_validator(mode='after')
    def valid_kind(self):
        if self.kind == 'panel' and (self.panel is None or self.permissions or self.file_inputs):
            raise ValueError('面板必须为纯声明式内容，不能请求执行权限。')
        if self.kind == 'python' and self.panel is not None:
            raise ValueError('Python 工具不能注入界面。')
        if self.file_inputs and 'file.read' not in self.permissions:
            raise ValueError('文件参数必须声明 file.read，不会自动获得授权。')
        return self


class TestCase(Input):
    arguments: dict = Field(default_factory=dict)
    expected: dict
    files: dict[str, str] = Field(default_factory=dict)


class SaveRequest(Input):
    manifest: Manifest
    source: str = Field(default='', max_length=100000)
    tests: list[TestCase] = Field(default_factory=list, max_length=30)
    expected_revision: int = Field(default=0, ge=0)


def root():
    target = settings.data_dir.resolve() / '扩展'
    target.mkdir(parents=True, exist_ok=True)
    if target.resolve() != target:
        raise ValueError('扩展目录不能是链接。')
    return target


def initialize():
    """Explicit compatibility bootstrap; ordinary calls never migrate."""
    db.init_db()


def _row(identifier, conn=None):
    row = repository.get(identifier, conn)
    if not row:
        raise ValueError('扩展不存在。')
    return dict(row)


def _bundle(identifier, version, conn=None):
    row = repository.version(identifier, version, conn)
    if not row:
        raise ValueError('扩展版本不存在。')
    path = root() / row['bundle_path']
    if not path.resolve().is_relative_to(root()) or path.is_symlink():
        raise ValueError('扩展路径无效。')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row['bundle_hash']:
        raise ValueError('扩展包已变化，请保存新版本并重新验证。')
    bundle = json.loads(raw)
    Manifest.model_validate(bundle['manifest'])
    return bundle, dict(row)


def read(identifier, version=''):
    with _lock:
        item = _row(identifier)
        bundle, selected = _bundle(identifier, version or item['latest_version'])
        versions = repository.versions(identifier)
        return {**bundle['manifest'], **item, 'version': selected['version'], 'manifest': bundle['manifest'],
                'source': bundle['source'], 'tests': bundle['tests'], 'enabled': bool(item['enabled']),
                'validation': json.loads(selected['validation_json']),
                'versions': [{'version': v['version'], 'validation': json.loads(v['validation_json']), 'created_at': v['created_at']} for v in versions],
                'runtime': RUNTIME.copy()}


def listing():
    ids = repository.ids()
    result = []
    for identifier in ids:
        try:
            item = read(identifier)
            result.append({k: v for k, v in item.items() if k not in {'source', 'tests', 'manifest'}})
        except (ValueError, OSError) as exc:
            result.append({**_row(identifier), 'name': identifier, 'enabled': False, 'validation': {'valid': False, 'errors': [str(exc)]}, 'runtime': RUNTIME.copy()})
    return result


def freeze_round(run_id, round_index):
    """Called once before a model request, never between calls in that request."""
    with _lock:
        rows=repository.active()
        snapshot={}
        for row in rows:
            try:
                bundle, version=_bundle(row['id'],row['active_version'])
                if json.loads(version['validation_json']).get('valid') and _check_bundle(bundle)['kind']=='python':
                    snapshot[row['id']]=row['active_version']
            except (ValueError,OSError):
                continue
        _round_catalogs[(run_id,int(round_index))]=snapshot
        while len(_round_catalogs)>256:
            _round_catalogs.pop(next(iter(_round_catalogs)))
    return dict(snapshot)


def release_run(run_id):
    with _lock:
        for key in list(_round_catalogs):
            if key[0]==run_id:
                _round_catalogs.pop(key,None)


def _schema(schema, depth=0):
    allowed = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'description', 'title', 'default',
               'minLength', 'maxLength', 'minimum', 'maximum', 'minItems', 'maxItems'}
    if depth > 10 or not isinstance(schema, dict) or set(schema) - allowed:
        raise ValueError('参数结构包含不支持的 JSON Schema 字段。')
    if not isinstance(schema.get('type'),str) or schema['type'] not in {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}:
        raise ValueError('参数结构需要明确 JSON 类型。')
    if schema['type'] == 'object':
        props = schema.get('properties', {})
        if not isinstance(props, dict) or len(props) > 80 or any(not isinstance(k, str) or k.startswith('_') for k in props):
            raise ValueError('对象参数属性无效。')
        if type(schema.get('additionalProperties', False)) is not bool:
            raise ValueError('additionalProperties 必须为布尔值。')
        if not isinstance(schema.get('required', []), list) or any(not isinstance(k,str) or k not in props for k in schema.get('required', [])):
            raise ValueError('required 必须引用存在的属性。')
        for item in props.values():
            _schema(item, depth + 1)
    if schema['type'] == 'array':
        _schema(schema.get('items', {}), depth + 1)
    for key in ('minLength', 'maxLength', 'minItems', 'maxItems'):
        if key in schema and (type(schema[key]) is not int or not 0 <= schema[key] <= 2000000):
            raise ValueError('参数长度约束无效。')
    for key in ('minimum', 'maximum'):
        if key in schema and type(schema[key]) not in (int, float):
            raise ValueError('参数数值约束无效。')
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not schema['enum']):
        raise ValueError('enum 必须是非空数组。')


def validate_value(schema, value, path='arguments'):
    kind = schema['type']
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list), 'string': isinstance(value, str),
             'integer': type(value) is int, 'number': type(value) in (int, float), 'boolean': type(value) is bool, 'null': value is None}[kind]
    if not valid or ('enum' in schema and value not in schema['enum']):
        raise ValueError(path + ' 类型或取值无效。')
    if kind == 'object':
        props = schema.get('properties', {})
        if any(k not in value for k in schema.get('required', [])):
            raise ValueError(path + ' 缺少必填参数。')
        if not schema.get('additionalProperties', False) and any(k not in props for k in value):
            # Empty result schemas mean any object; input schemas always forbid extras.
            if path == 'result' and not props and 'additionalProperties' not in schema:
                return
            raise ValueError(path + ' 包含未声明参数。')
        for key, child in value.items():
            if key in props:
                validate_value(props[key], child, path + '.' + key)
    if kind == 'array':
        for child in value:
            validate_value(schema['items'], child, path + '[]')
    for lower, upper, actual in [('minLength', 'maxLength', len(value) if kind == 'string' else None),
                                 ('minItems', 'maxItems', len(value) if kind == 'array' else None),
                                 ('minimum', 'maximum', value if kind in {'integer', 'number'} else None)]:
        if actual is not None and (actual < schema.get(lower, actual) or actual > schema.get(upper, actual)):
            raise ValueError(path + ' 超出参数范围。')


def _check_bundle(bundle):
    manifest = Manifest.model_validate(bundle['manifest']).model_dump()
    if manifest['api_version'] != API_VERSION:
        raise ValueError('扩展 API 版本不兼容。')
    _schema(manifest['parameters']); _schema(manifest['result_schema'])
    if manifest['parameters']['type'] != 'object' or manifest['result_schema']['type'] != 'object':
        raise ValueError('工具输入输出必须为 JSON 对象。')
    props = manifest['parameters'].get('properties', {})
    if 'files' in props:
        raise ValueError('files 是宿主授权输入保留字段。')
    if any(key not in props or props[key].get('type') != 'string' for key in manifest['file_inputs']):
        raise ValueError('文件参数必须是声明的字符串属性。')
    if manifest['kind'] == 'python':
        validate_source(bundle['source'])
        if not bundle['tests']:
            raise ValueError('Python 工具至少需要一个合成输入输出验收用例。')
    elif bundle['source'] or bundle['tests']:
        raise ValueError('声明式面板不能包含脚本或测试代码。')
    return manifest


@maintenance_service.mutation_scope()
def save(manifest, source='', tests=None, expected_revision=0):
    request = SaveRequest.model_validate({'manifest': manifest, 'source': source, 'tests': tests or [], 'expected_revision': expected_revision})
    bundle = {'manifest': request.manifest.model_dump(), 'source': source, 'tests': [t.model_dump() for t in request.tests]}
    identifier = request.manifest.id or 'ext_' + uuid.uuid4().hex[:24]
    bundle['manifest']['id'] = identifier
    # Invalid drafts are persisted for inspection, but cannot be activated.
    try:
        _check_bundle(bundle)
        validation = {'valid': False, 'errors': [], 'status': 'untested'}
    except (ValueError, SyntaxError) as exc:
        validation = {'valid': False, 'errors': [str(exc)], 'status': 'invalid'}
    version = request.manifest.version
    with _lock, repository.transaction() as conn:
        current = repository.get(identifier, conn)
        if (current['revision'] if current else 0) != expected_revision:
            raise ConflictError('扩展已变化，请刷新；你的草稿未覆盖新版本。')
        if repository.version(identifier, version, conn):
            raise ValueError('同一版本不能覆盖，请使用新的版本号。')
        if current:
            try:
                old,_=_bundle(identifier,current['latest_version'],conn)
            except (ValueError,OSError):
                old=None
            if old and old['manifest']['kind']!=bundle['manifest']['kind']:
                raise ValueError('扩展类型不能在同一身份下改变，请创建新扩展。')
        raw = json.dumps(bundle, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(raw)>4*1024*1024:
            raise ValueError('扩展包含合成测试最多 4 MB。')
        digest = hashlib.sha256(raw).hexdigest()
        path = root() / (identifier + '-' + version + '-' + digest[:16] + '.json')
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError('扩展存储冲突。')
        else:
            with path.open('xb') as stream:
                stream.write(raw)
        repository.save(conn,identifier,expected_revision+1,version,path.name,digest,validation)
    return read(identifier)


def _worker_command(request_path,response_path):
    ipc=['--extension-request',str(request_path),'--extension-response',str(response_path)]
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--extension-worker',*ipc]
    return [sys.executable, '-I', '-S', str(Path(__file__).with_name('extension_worker.py')),*ipc]


def _stop_process(entry):
    process, owner, task_id, identifier = entry
    from .terminal_service import _terminate
    _terminate(process, owner)


def _limit_windows_memory(owner):
    # Hard process memory ceiling, in addition to the shared kill-on-close job.
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                  ('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('active',w.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_uint64) for name in ('read','write','other','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                  ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    limits=Extended(); limits.basic.flags=0x2000|0x100; limits.process_memory=256*1024*1024
    if not owner.kernel.SetInformationJobObject(owner.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
        raise OSError('无法设置扩展进程内存限制。')


def stop_task(task_id):
    with _lock:
        entries = list(_running.values())
    for entry in entries:
        if entry[2] == task_id:
            _stop_process(entry)


def stop_all():
    with _lock:
        entries = list(_running.values())
    for entry in entries:
        _stop_process(entry)


async def _run_worker(bundle, arguments, *, task_id='', timeout=None):
    from .terminal_service import WindowsJob
    owner = None
    token = uuid.uuid4().hex
    process = None
    # Keep startup essentials, never inherit API keys, tokens, PYTHONPATH or user home.
    env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'COMSPEC') if key in os.environ}
    with tempfile.TemporaryDirectory(prefix='mio-extension-') as temporary:
        request = json.dumps({'source': bundle['source'], 'arguments': arguments}, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(request) > 4 * 1024 * 1024:
            raise ValueError('扩展输入超过 4 MB。')
        request_path=Path(temporary)/'request.json';response_path=Path(temporary)/'response.json'
        request_path.write_bytes(request)
        try:
            flags = (subprocess.CREATE_NO_WINDOW | 0x00000004) if os.name == 'nt' else 0
            process = subprocess.Popen(_worker_command(request_path,response_path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       cwd=temporary, env=env, creationflags=flags, start_new_session=os.name != 'nt')
            if os.name == 'nt':
                owner = WindowsJob(process._handle)
                _limit_windows_memory(owner)
            entry = (process, owner, task_id, bundle['manifest']['id'])
            with _lock:
                _running[token] = entry
            communicate = asyncio.create_task(asyncio.to_thread(process.wait))
            deadline = time.monotonic() + (timeout or bundle['manifest']['timeout_seconds'])
            while not communicate.done():
                if time.monotonic() >= deadline:
                    raise ValueError('扩展执行超时，进程已停止。')
                if task_id:
                    from . import agent_task_service as tasks
                    task = await asyncio.to_thread(tasks.get, task_id)
                    if not task or task['status'] in {'cancelled', 'paused'}:
                        raise ValueError('所属任务已停止。')
                await asyncio.sleep(0.05)
            await communicate
            if process.returncode != 0:
                raise ValueError('扩展进程失败或已停止。')
            if not response_path.is_file():
                raise ValueError('扩展进程未返回结构化结果。')
            if response_path.stat().st_size > 2 * 1024 * 1024 + 2000:
                raise ValueError('扩展结果过大。')
            stdout=response_path.read_bytes()
            response = json.loads(stdout.decode('utf-8'))
            if not response.get('ok'):
                raise ValueError(response.get('error', '扩展失败。'))
            return response['result']
        finally:
            if process is not None:
                _stop_process((process, owner, task_id, bundle['manifest']['id']))
                await asyncio.to_thread(process.wait)
            if owner:
                owner.close()
            with _lock:
                _running.pop(token, None)


async def validate(identifier, version='', expected_revision=0):
    with _lock:
        item = _row(identifier)
        if item['revision'] != expected_revision:
            raise ConflictError('扩展版本已变化，请刷新。')
        bundle, row = _bundle(identifier, version or item['latest_version'])
    validation = {'valid': False, 'errors': [], 'status': 'invalid', 'tests_passed': 0}
    try:
        manifest = _check_bundle(bundle)
        for test in bundle['tests']:
            validate_value(manifest['parameters'], test['arguments'])
            if set(test['files']) != set(manifest['file_inputs']):
                raise ValueError('合成测试 files 必须精确对应 file_inputs。')
            result = await _run_worker(bundle, {**test['arguments'], 'files': test['files']})
            validate_value(manifest['result_schema'], result, 'result')
            _validate_outputs(result, manifest)
            if result != test['expected']:
                raise ValueError('合成用例结果与预期不一致。')
            validation['tests_passed'] += 1
        validation.update(valid=True, status='validated', checked_at=db.now_iso())
    except (ValueError, SyntaxError, OSError, json.JSONDecodeError) as exc:
        validation['errors'].append(str(exc))
    with _lock, repository.transaction() as conn:
        if _row(identifier,conn)['revision'] != expected_revision:
            raise ConflictError('验证期间扩展版本已变化，请重新验证。')
        repository.set_validation(conn,identifier,row['version'],validation)
    return read(identifier, row['version'])


@maintenance_service.mutation_scope()
def enabled(identifier, enabled, expected_revision, version=''):
    with _lock, repository.transaction() as conn:
        item = _row(identifier,conn)
        if item['revision'] != expected_revision:
            raise ConflictError('扩展版本已变化，请刷新。')
        target = version or item['latest_version']
        if enabled:
            bundle, row = _bundle(identifier, target,conn)
            _check_bundle(bundle)
            if not json.loads(row['validation_json']).get('valid'):
                raise ValueError('扩展尚未通过验证，不能启用。')
        repository.set_enabled(conn,identifier,enabled,target,item['active_version'])
        entries = [entry for entry in _running.values() if entry[3] == identifier]
    for entry in entries:
        _stop_process(entry)
    try:
        return read(identifier,target if enabled else '')
    except (ValueError,OSError) as exc:
        # Disabling must remain effective even if the latest bundle is damaged.
        return {**_row(identifier),'name':identifier,'enabled':False,
                'validation':{'valid':False,'errors':[str(exc)]},'runtime':RUNTIME.copy()}


def rollback(identifier, expected_revision):
    item = _row(identifier)
    history = repository.activation_history(identifier)
    versions = repository.versions(identifier)
    valid = {v['version'] for v in versions if json.loads(v['validation_json']).get('valid')}
    candidates = []
    for version in history:
        if version!=item['active_version'] and version in valid:
            try:
                bundle,_=_bundle(identifier,version)
                _check_bundle(bundle)
                candidates.append(version)
            except (ValueError,OSError):
                continue
    if not candidates:
        raise ValueError('没有可恢复的已验证历史版本。')
    return enabled(identifier, True, expected_revision, candidates[0])


def panel(identifier):
    item = _row(identifier)
    if not item['enabled']:
        raise ValueError('扩展已停用。')
    bundle, row = _bundle(identifier, item['active_version'])
    manifest = _check_bundle(bundle)
    if manifest['kind'] != 'panel' or not json.loads(row['validation_json']).get('valid'):
        raise ValueError('扩展没有有效声明式面板。')
    return {'id': identifier, 'version': item['active_version'], 'panel': manifest['panel']}


def _validate_outputs(result, manifest):
    outputs = result.get('outputs', [])
    if not outputs:
        return
    if 'artifact.write' not in manifest['permissions'] or not isinstance(outputs, list) or len(outputs) > 10:
        raise ValueError('扩展未声明成果写入权限或输出过多。')
    for output in outputs:
        if not isinstance(output, dict) or set(output) != {'name', 'content'}:
            raise ValueError('成果只允许 name 和 content。')
        name, content = output['name'], output['content']
        if not isinstance(name, str) or Path(name).name != name or name.startswith('.') or ':' in name or Path(name).suffix.lower() not in {'.txt', '.md', '.json', '.csv', '.tsv'}:
            raise ValueError('扩展成果必须使用普通文本文件名。')
        if not isinstance(content, str) or len(content) > 200000:
            raise ValueError('成果正文无效或过大。')
        if name.endswith('.json'):
            json.loads(content)


async def invoke(identifier, arguments, context, version=''):
    with _lock:
        if context and getattr(context,'run_id',''):
            snapshot=_round_catalogs.get((context.run_id,int(getattr(context,'round_index',0))),{})
            if not version or snapshot.get(identifier)!=version:
                raise ValueError('扩展未包含在本轮清单，请在下一轮重新发现。')
        item = _row(identifier)
        if not item['enabled'] or version and version != item['active_version']:
            raise ValueError('扩展已停用或版本已变化，请在下一轮重新发现。')
        bundle, row = _bundle(identifier, item['active_version'])
        manifest = _check_bundle(bundle)
        if not json.loads(row['validation_json']).get('valid') or manifest['kind'] != 'python':
            raise ValueError('扩展不是已验证 Python 工具。')
        baseline = item['revision']
    if not context or not context.task_id or not context.conversation_id.startswith('desktop_agent_') or context.source not in {'desktop', 'agent', 'agent_task', 'agent_background'}:
        raise ValueError('扩展执行仅用于本机 Agent 任务。')
    from . import agent_task_service as tasks, agent_file_service as files
    task = tasks.get(context.task_id)
    if not task or task['conversation_id'] != context.conversation_id or task.get('source') not in {'desktop', 'agent', 'agent_task'} or task['status'] in {'paused', 'cancelled'}:
        raise ValueError('任务来源或状态不能执行本机扩展。')
    validate_value(manifest['parameters'], arguments)
    materialized = {}
    for key in manifest['file_inputs']:
        if key not in arguments:
            raise ValueError('缺少声明的文件输入。')
        source = files.resolve_read(arguments[key], task_id=context.task_id)
        if source.suffix.lower() not in files.TEXT or not source.is_file() or source.stat().st_size > 1000000:
            raise ValueError('扩展输入仅支持不超过 1 MB 的授权文本文件。')
        materialized[key] = source.read_text(encoding='utf-8-sig')
    result = await _run_worker(bundle, {**arguments, 'files': materialized}, task_id=context.task_id)
    validate_value(manifest['result_schema'], result, 'result')
    _validate_outputs(result, manifest)
    artifacts = []
    with _lock:
        current = _row(identifier)
        if not current['enabled'] or current['revision'] != baseline:
            raise ValueError('扩展执行期间已停用或更新，结果未写入。')
        task = tasks.get(context.task_id)
        if not task or task['status'] in {'paused', 'cancelled'}:
            raise ValueError('所属任务已停止，结果未写入。')
        # Recheck source authorization before any host-side write.
        for key in manifest['file_inputs']:
            files.resolve_read(arguments[key], task_id=context.task_id)
        for output in result.get('outputs', []):
            artifacts.append(files.write_document(output['name'], output['content'], context.task_id))
    return {'extension_id': identifier, 'version': item['active_version'], 'result': result, 'artifacts': artifacts, 'runtime': RUNTIME.copy()}
