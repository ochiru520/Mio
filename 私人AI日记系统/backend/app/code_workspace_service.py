"""Versioned local code workspaces, separate from document permissions."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import tempfile
import threading
import uuid
from contextlib import closing
from pathlib import Path

from . import db, workspace_service as workspace, execution_capability_defaults_service as defaults

_lock = threading.RLock()
MAX_FILE = 2 * 1024 * 1024
EXCLUDED = {'.git', '.svn', 'node_modules', '__pycache__', '.venv', 'venv', '备份', '运行数据'}
PRIVATE = {'.env', 'credentials', 'credentials.json', 'secrets.json', 'id_rsa', 'id_ed25519', 'personal_ai.db'}


def is_link(path):
    try: return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError: return False


def initialize():
    workspace.initialize()
    defaults.initialize()
    with closing(db.get_conn()) as conn, conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS code_workspaces(
          project_id TEXT PRIMARY KEY,root TEXT NOT NULL,read_enabled INTEGER NOT NULL DEFAULT 0,
          write_enabled INTEGER NOT NULL DEFAULT 0,execute_enabled INTEGER NOT NULL DEFAULT 0,
          revision INTEGER NOT NULL DEFAULT 1,verification_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS code_changes(
          id TEXT PRIMARY KEY,project_id TEXT NOT NULL,task_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
          workspace_revision INTEGER NOT NULL,changes_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
        ''')


def configuration(project_id):
    initialize()
    project = workspace.require_project(project_id)
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM code_workspaces WHERE project_id=?', (project_id,)).fetchone()
    if not row:
        capability = defaults.configuration()
        root = project.get('directory', '')
        return {'project_id': project_id, 'root': root,
                **{key: capability[key] for key in ('read_enabled', 'write_enabled', 'execute_enabled')},
                'revision': defaults.inherited_revision(project_id, 'code', root, capability),
                'verification': {}, 'permission_source': 'global', 'defaults_revision': capability['revision']}
    result = dict(row)
    result['verification'] = json.loads(result.pop('verification_json'))
    for key in ('read_enabled', 'write_enabled', 'execute_enabled'):
        result[key] = bool(result[key])
    result['permission_source'] = 'project'
    return result


def configure(project_id, root, read_enabled=False, write_enabled=False, execute_enabled=False,
              verification=None, expected_revision=None):
    initialize()
    project = workspace.require_project(project_id, active=True)
    if not str(root or '').strip():
        raise ValueError('请先选择项目文件夹，不能授权空目录。')
    path = Path(root).expanduser().resolve(strict=True)
    from .agent_file_service import _private_path
    if _private_path(path): raise ValueError('不能将 Mio 私人数据设为代码工作区。')
    if not path.is_dir() or path == Path(path.anchor):
        raise ValueError('请选择具体的项目文件夹，不能授权整个磁盘。')
    if (write_enabled or execute_enabled) and not read_enabled:
        raise ValueError('修改或运行代码前须启用项目读取权限。')
    commands = dict(verification or {})
    if any(key not in {'test', 'build', 'lint', 'typecheck'} or not isinstance(value, str) or len(value) > 8000
           for key, value in commands.items()):
        raise ValueError('验证命令仅支持 test/build/lint/typecheck。')
    with _lock, closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = conn.execute('SELECT root,revision FROM code_workspaces WHERE project_id=?', (project_id,)).fetchone()
        revision = int(previous['revision']) if previous else 0
        inherited = defaults.inherited_revision(project_id, 'code', project.get('directory', ''), defaults.configuration(conn))
        accepted = {revision} if previous else {0, inherited}
        if expected_revision is not None and expected_revision not in accepted:
            raise ValueError('项目授权已改变，请重新读取。')
        previous_root = previous['root'] if previous else project.get('directory', '')
        if previous_root:
            old_path = Path(previous_root).expanduser().resolve()
            if old_path != path and (read_enabled or write_enabled or execute_enabled):
                raise ValueError('更换项目目录时不能沿用旧目录授权，请先保存新目录，再单独授予权限。')
        conn.execute('INSERT OR REPLACE INTO code_workspaces VALUES(?,?,?,?,?,?,?,?)',
                     (project_id, str(path), int(read_enabled), int(write_enabled), int(execute_enabled),
                      revision + 1, workspace.encode(commands), db.now_iso()))
        # Code authorization can bind an existing directory, but must not
        # erase provenance when the project already points at a Mio-created
        # folder.  A changed root is user-selected and therefore unowned.
        conn.execute('''INSERT INTO workspace_project_directories(project_id,path,created_by_mio)
            VALUES(?,?,COALESCE((SELECT created_by_mio FROM workspace_project_directories
                                 WHERE project_id=? AND path=?),0))
            ON CONFLICT(project_id) DO UPDATE SET path=excluded.path,created_by_mio=excluded.created_by_mio''',
                     (project_id, str(path), project_id, str(path)))
    return configuration(project_id)


def project_context(context, permission='read', expected_revision=None):
    if not context or not context.conversation_id.startswith('desktop_agent_') or context.source not in {'desktop', 'agent', 'agent_task', 'agent_background'}:
        raise ValueError('本机代码和电脑工具仅用于桌面 Agent 项目对话。')
    if permission != 'read':
        from .execution_policy_service import require_write
        require_write(context, '修改代码' if permission=='write' else '终端命令')
    from . import agent_task_service as tasks
    if tasks.automatic_work_paused():
        raise ValueError('本机执行能力已暂停。')
    task = tasks.get(context.task_id) if context.task_id else None
    if context.task_id and (not task or task['conversation_id'] != context.conversation_id or task.get('source') not in {'desktop', 'agent', 'agent_task'}):
        raise ValueError('手机和远程渠道任务不能取得本机执行权限。')
    project = workspace.project_for_conversation(context.conversation_id)
    if not project or project['archived']:
        raise ValueError('请先将对话放入一个有效项目。')
    config = configuration(project['id'])
    if expected_revision is not None and config['revision'] != expected_revision:
        raise ValueError('项目授权版本已改变，请重新读取后执行。')
    if not config.get(permission + '_enabled'):
        raise ValueError('项目尚未启用' + {'read': '读取代码', 'write': '修改代码', 'execute': '本机命令'}[permission] + '权限。')
    if not str(config['root'] or '').strip():
        raise ValueError('请先选择项目文件夹。默认能力不会自动授权电脑上的其他目录。')
    root = Path(config['root'])
    from .agent_file_service import _private_path
    if root.resolve() == Path(root.resolve().anchor) or _private_path(root.resolve()):
        raise ValueError('请选择具体的项目文件夹，不能使用整个磁盘或 Mio 私人数据目录。')
    if is_link(root): raise ValueError('代码目录已变为链接，请重新授权。')
    if not root.is_dir():
        raise ValueError('项目代码目录已不存在。')
    return config


def resolve(config, path, *, allow_missing=False):
    text = str(path or '.')
    relative = Path(text)
    if relative.is_absolute() or relative.anchor or relative.drive or ':' in text or '\x00' in text:
        raise ValueError('代码路径必须是项目内相对路径，不能包含磁盘、UNC 或 ADS。')
    if '..' in relative.parts:
        raise ValueError('不能越过项目目录。')
    for part in relative.parts:
        low = part.lower().rstrip(' .')
        if low in EXCLUDED or low in PRIVATE or low.startswith('.env') or low.endswith(('.pem', '.key', '.pfx', '.p12', '.sqlite', '.db')):
            raise ValueError('此目录或凭据/私人数据文件不允许进入代码工具。')
    root = Path(config['root']).resolve(strict=True)
    candidate = root / relative
    cursor = candidate
    while cursor != root:
        if is_link(cursor):
            raise ValueError('代码工具不跟随符号链接或 junction。')
        cursor = cursor.parent
    actual = candidate.resolve(strict=not allow_missing)
    from .agent_file_service import _private_path
    if _private_path(actual): raise ValueError('私人运行数据不允许进入代码工具。')
    if not actual.is_relative_to(root):
        raise ValueError('代码路径不在已授权项目内。')
    return actual


def _decode(raw):
    if len(raw) > MAX_FILE:
        raise ValueError('文件超过 2 MB，请缩小文件或使用分页资料工具。')
    encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-16-le' if raw.startswith(b'\xff\xfe') else 'utf-16-be' if raw.startswith(b'\xfe\xff') else 'utf-8'
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = 'gb18030'
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            raise ValueError('不支持此二进制或未知编码文件。') from exc
    if '\x00' in text or any(ord(char) < 9 or 13 < ord(char) < 32 for char in text[:10000]):
        raise ValueError('代码工具只处理文本文件。')
    return text, encoding


def _file(config, path):
    target = resolve(config, path)
    if not target.is_file() or target.stat().st_size > MAX_FILE:
        raise ValueError('请选择不超过 2 MB 的文本文件。')
    raw = target.read_bytes()
    text, encoding = _decode(raw)
    return target, raw, text, encoding


def list_files(context, path='', limit=200, offset=0):
    config = project_context(context)
    root = resolve(config, path)
    if not root.is_dir():
        raise ValueError('请选择目录。')
    entries = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d.lower() not in EXCLUDED and not is_link(Path(folder) / d))
        for name in sorted(files):
            relative = (Path(folder) / name).relative_to(config['root']).as_posix()
            try:
                file = resolve(config, relative)
                if file.stat().st_size <= MAX_FILE:
                    entries.append({'path': relative, 'size': file.stat().st_size})
            except (ValueError, OSError):
                continue
            if len(entries) >= offset + limit + 1:
                return {'files': entries[offset:offset + limit], 'has_more': True, 'next_offset': offset + limit,
                        'workspace_revision': config['revision'], 'root': config['root']}
    return {'files': entries[offset:offset + limit], 'has_more': False, 'workspace_revision': config['revision'], 'root': config['root']}


def read_file(context, path, offset=0, limit=300):
    config = project_context(context)
    _, raw, text, encoding = _file(config, path)
    lines = text.splitlines()
    return {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(), 'encoding': encoding,
            'newline': 'CRLF' if '\r\n' in text else 'LF', 'workspace_revision': config['revision'],
            'content': '\n'.join(lines[offset:offset + limit]), 'lines': [{'number': offset + n + 1, 'text': line}
             for n, line in enumerate(lines[offset:offset + limit])], 'total_lines': len(lines),
            'has_more': offset + limit < len(lines), 'next_offset': offset + limit}


def search(context, query, path='', limit=100):
    config = project_context(context)
    results = []
    listing = list_files(context, path, 2000)
    for item in listing['files']:
        try:
            _, _, text, _ = _file(config, item['path'])
        except (ValueError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if query.casefold() in line.casefold():
                results.append({'path': item['path'], 'line': number, 'text': line[:1000]})
                if len(results) >= limit:
                    return {'matches': results, 'truncated': True, 'workspace_revision': config['revision']}
    return {'matches': results, 'truncated': listing['has_more'], 'workspace_revision': config['revision']}


def _atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.mio-edit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temp, path.stat().st_mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def apply_patch(context, changes, workspace_revision):
    """Exact text edits with CAS. Preflight all files before writing any file."""
    with _lock:
        config = project_context(context, 'write', workspace_revision)
        prepared, seen = [], set()
        for change in changes:
            target = resolve(config, change['path'], allow_missing=bool(change.get('create')))
            key = str(target).casefold()
            if key in seen:
                raise ValueError('每个文件在一次补丁中只出现一次；多个替换放在 edits 中。')
            seen.add(key)
            exists = target.exists()
            if change.get('create'):
                if exists or change.get('expected_sha256'):
                    raise ValueError('新文件已经存在，须重新读取后修改。')
                raw, old, encoding = b'', '', 'utf-8'
                text = change.get('content', '')
            else:
                _, raw, old, encoding = _file(config, change['path'])
                if hashlib.sha256(raw).hexdigest() != change.get('expected_sha256'):
                    raise ValueError('文件已被其他程序修改，请重新读取后生成补丁。')
                text = old
                newline = '\r\n' if '\r\n' in old else '\n'
                for edit in change.get('edits', []):
                    before = edit['old_text'].replace('\r\n', '\n').replace('\n', newline)
                    after = edit['new_text'].replace('\r\n', '\n').replace('\n', newline)
                    if not before or text.count(before) != 1:
                        raise ValueError('替换片段必须在文件中恰好匹配一次。')
                    text = text.replace(before, after, 1)
                if not change.get('edits'):
                    raise ValueError('修改已有文件须提供非空 edits。')
            encoded = text.encode(encoding)
            if len(encoded) > MAX_FILE:
                raise ValueError('修改后的文件超过 2 MB。')
            diff = ''.join(difflib.unified_diff(old.splitlines(True), text.splitlines(True),
                         fromfile='a/' + change['path'], tofile='b/' + change['path']))
            prepared.append((target, raw, encoded, exists, change['path'], diff))
        change_id = 'change_' + uuid.uuid4().hex
        backup = db.settings.db_path.parent / '代码修改备份' / change_id
        backup.mkdir(parents=True, exist_ok=True)
        records = []
        for index, (_, old, new, exists, path, diff) in enumerate(prepared):
            (backup / str(index)).write_bytes(old)
            records.append({'path': path, 'before_sha256': hashlib.sha256(old).hexdigest(),
                            'after_sha256': hashlib.sha256(new).hexdigest(), 'existed': exists,
                            'backup': str(backup / str(index)), 'diff': diff})
        with closing(db.get_conn()) as conn, conn:
            conn.execute('INSERT INTO code_changes VALUES(?,?,?,?,?,?,?,?)',
                         (change_id, config['project_id'], context.task_id, context.conversation_id,
                          config['revision'], workspace.encode(records), 'prepared', db.now_iso()))
        written = []
        try:
            for target, old, new, exists, _, _ in prepared:
                project_context(context, 'write', workspace_revision)
                resolve(config, target.relative_to(config['root']).as_posix(), allow_missing=not exists)
                if target.exists() != exists or (exists and target.read_bytes() != old):
                    raise ValueError('写入前文件已发生变化，请重新读取。')
                _atomic(target, new)
                written.append((target, old, new, exists))
        except Exception:
            for target, old, new, exists in reversed(written):
                if target.read_bytes() == new:
                    _atomic(target, old) if exists else target.unlink()
            with closing(db.get_conn()) as conn, conn:
                conn.execute("UPDATE code_changes SET status='failed' WHERE id=?", (change_id,))
            raise
        with closing(db.get_conn()) as conn, conn:
            conn.execute("UPDATE code_changes SET status='applied' WHERE id=?", (change_id,))
        return {'change_id': change_id, 'status': 'applied', 'files': [{k: v for k, v in row.items() if k != 'backup'} for row in records],
                'workspace_revision': config['revision']}


def change_detail(context, change_id):
    config = project_context(context)
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM code_changes WHERE id=?', (change_id,)).fetchone()
    if not row or row['project_id'] != config['project_id'] or row['conversation_id'] != context.conversation_id:
        raise ValueError('修改记录不属于当前对话项目。')
    result = dict(row)
    result['files'] = json.loads(result.pop('changes_json'))
    return result


def diff(context, change_id):
    result = change_detail(context, change_id)
    for item in result['files']:
        item.pop('backup', None)
    return result


def restore(context, change_id, workspace_revision):
    with _lock:
        config = project_context(context, 'write', workspace_revision)
        result = change_detail(context, change_id)
        if result['status'] != 'applied' or result['workspace_revision'] != config['revision']:
            raise ValueError('修改已恢复或授权已改变，不能直接恢复。')
        prepared = []
        for item in result['files']:
            target = resolve(config, item['path'])
            if hashlib.sha256(target.read_bytes()).hexdigest() != item['after_sha256']:
                raise ValueError('文件有后续修改，恢复将覆盖用户内容；请人工合并。')
            old = Path(item['backup']).read_bytes()
            if hashlib.sha256(old).hexdigest() != item['before_sha256']:
                raise ValueError('备份校验失败。')
            prepared.append((target, old, target.read_bytes(), item['existed']))
        written = []
        try:
            for target, old, new, existed in prepared:
                project_context(context, 'write', workspace_revision)
                resolve(config, target.relative_to(config['root']).as_posix())
                if target.read_bytes() != new:
                    raise ValueError('恢复前文件已改变。')
                _atomic(target, old) if existed else target.unlink()
                written.append((target, old, new, existed))
        except Exception:
            for target, old, new, existed in reversed(written):
                if (existed and target.exists() and target.read_bytes() == old) or (not existed and not target.exists()):
                    _atomic(target, new)
            raise
        with closing(db.get_conn()) as conn, conn:
            conn.execute("UPDATE code_changes SET status='restored' WHERE id=?", (change_id,))
        return {'change_id': change_id, 'status': 'restored'}
