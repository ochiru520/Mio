"""Owner-configured autonomy; tool policy is distinct from process isolation."""
from __future__ import annotations

from contextlib import closing, contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import threading

from . import db

_lock = threading.RLock()
_context = ContextVar('mio_execution_read_context', default=None)
DEFAULTS = dict(mode='autonomous', read_scope='project', terminal_mode='host',
                network_enabled=False, confirm_project_edits=False,
                sandbox_image='python:3.12-alpine')
LABELS = dict(read_only='只查看', assisted='帮我做事', autonomous='自主执行')
CONTROL = {'agent_task_update', 'compress_current_context', 'terminal_stop', 'desktop_stop', 'extension_stop',
           'self_upgrade_stop', 'creation_cancel_job', 'agent_discover_tools'}


def initialize():
    with _lock, closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS execution_policy(
            id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,
            settings_json TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM execution_policy WHERE id=1').fetchone()
        if row:
            values = json.loads(row['settings_json'])
            # Older mode-only saves left autonomy contradicted by terminal confirmation.
            if values.get('mode') == 'autonomous' and values.get('terminal_mode') == 'confirm':
                values['terminal_mode'] = 'host'
                conn.execute('UPDATE execution_policy SET revision=?,settings_json=?,updated_at=? WHERE id=1',
                             (row['revision'] + 1, json.dumps(values, ensure_ascii=False), db.now_iso()))


def configuration():
    initialize()
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM execution_policy WHERE id=1').fetchone()
    return {**DEFAULTS, **(json.loads(row['settings_json']) if row else {}),
            'revision': row['revision'] if row else 0, 'configured': bool(row)}


def snapshot():
    from .terminal_sandbox import status
    config = configuration()
    return {**config, 'mode_label': LABELS[config['mode']], 'sandbox': status(config['sandbox_image']),
            'terminal_description': '隔离环境，仅项目副本可写' if config['terminal_mode']=='sandbox'
                else '本机命令需逐次确认' if config['terminal_mode']=='confirm'
                else '已允许本机执行，命令不受项目目录隔离'}


def configure(*, expected_revision, **changes):
    if set(changes)-set(DEFAULTS):
        raise ValueError('包含不支持的执行设置。')
    config = {**configuration(), **changes}
    if changes.get('mode') == 'autonomous':
        if 'terminal_mode' not in changes and config['terminal_mode'] != 'sandbox':
            config['terminal_mode'] = 'host'
        if 'confirm_project_edits' not in changes:
            config['confirm_project_edits'] = False
    elif changes.get('mode') == 'assisted' and 'terminal_mode' not in changes and config['terminal_mode'] != 'sandbox':
        config['terminal_mode'] = 'confirm'
    if changes.get('terminal_mode') == 'confirm' and 'mode' not in changes:
        config['mode'] = 'assisted'
    if config['mode'] == 'autonomous' and config['terminal_mode'] == 'confirm':
        raise ValueError('自主执行使用持续授权本机命令或隔离环境；逐次确认请使用帮我做事。')
    if config['mode'] not in LABELS or config['read_scope'] not in {'project','folders','computer'}:
        raise ValueError('请选择有效的权限和读取范围。')
    if config['terminal_mode'] not in {'confirm','sandbox','host'}:
        raise ValueError('请选择有效的终端环境。')
    for key in ('network_enabled','confirm_project_edits'):
        if not isinstance(config[key], bool):
            raise ValueError('执行开关必须是布尔值。')
    from .terminal_sandbox import validate_image, status
    validate_image(config['sandbox_image'])
    if config['terminal_mode']=='sandbox' and not status(config['sandbox_image'])['available']:
        raise ValueError('隔离终端当前不可用。请启动 Docker 并准备对应镜像；不会自动改为本机执行。')
    initialize()
    with _lock, closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT revision FROM execution_policy WHERE id=1').fetchone()
        revision = row[0] if row else 0
        if revision != expected_revision:
            raise ValueError('执行设置已在另一个窗口改变，请刷新后保存。')
        values={key:config[key] for key in DEFAULTS}
        conn.execute('INSERT OR REPLACE INTO execution_policy VALUES(1,?,?,?)',
            (revision+1,json.dumps(values,ensure_ascii=False),db.now_iso()))
    return snapshot()


def is_local(context):
    return bool(context and str(getattr(context,'conversation_id','')).startswith('desktop_agent_') and
                getattr(context,'source','desktop') in {'desktop','agent','agent_task','agent_background'})


@contextmanager
def scope(context):
    token = _context.set(context)
    try:
        yield
    finally:
        _context.reset(token)


def require_write(context, operation='此操作'):
    if is_local(context) and configuration()['mode']=='read_only':
        raise ValueError(f'当前为“只查看”，不能执行{operation}。可在输入框旁切换权限。')


def decision(definition, arguments, context):
    """Return allow/ask/deny; underlying services still verify path/window grants."""
    from .tool_types import ToolPermission
    if not is_local(context):
        return ('ask' if definition.permission==ToolPermission.HIGH_RISK_WRITE else 'allow','')
    config = configuration()
    if definition.permission==ToolPermission.READ_ONLY or definition.name in CONTROL:
        return 'allow',''
    if config['mode']=='read_only':
        return 'deny','当前为“只查看”，此操作会写入或控制电脑。请切换权限后再执行。'
    if definition.permission!=ToolPermission.HIGH_RISK_WRITE:
        return 'allow',''
    name = definition.name
    if name in {'code_apply_patch','code_restore','terminal_start','code_verify'}:
        from . import code_workspace_service as code
        try:
            code.project_context(context,'execute' if name in {'terminal_start','code_verify'} else 'write',
                                 arguments.get('workspace_revision'))
        except (ValueError,OSError) as exc:
            return 'deny',str(exc)
        if name in {'terminal_start','code_verify'}:
            return ('allow','') if config['terminal_mode'] in {'sandbox','host'} else ('ask','本机命令需确认。')
        return ('ask','本项目设置为每次确认修改。') if config['confirm_project_edits'] else ('allow','')
    if name in {'desktop_act','desktop_launch'}:
        from . import desktop_control_service as desktop
        try:
            desktop.project_context(context,arguments.get('permission_revision'))
        except (ValueError,OSError) as exc:
            return 'deny',str(exc)
        # The exact window/executable, observation and revision are checked by dispatch.
        if name == 'desktop_launch' and config['mode'] == 'assisted':
            return 'ask','帮我做事模式下，启动已授权应用需确认；自主执行可在窗口授权范围内连续完成。'
        return 'allow',''
    if name=='self_upgrade_request_apply':
        return 'allow',''  # The installation service owns final user consent.
    if name=='self_upgrade_start':
        from .self_upgrade_service import configuration as upgrade_configuration
        source=upgrade_configuration()
        if not source.get('backend_root') or not source.get('desktop_root'):
            return 'deny','请先在“改造 Mio”中绑定自身源码。'
        return 'allow',''
    if name in {'self_upgrade_validate','self_upgrade_build','self_upgrade_preview'}:
        return ('allow','') if config['mode']=='autonomous' and config['terminal_mode']=='host' else ('ask','开发检查与构建会在本机执行代码，需确认。')
    # Publishing, permission changes and other high-risk mutations keep their own gate.
    return 'ask','此操作需要单独确认。'


def local_roots():
    if os.name=='nt':
        import ctypes
        mask=ctypes.windll.kernel32.GetLogicalDrives()
        return [Path(chr(65+i)+':/') for i in range(26) if mask & (1<<i)
                and ctypes.windll.kernel32.GetDriveTypeW(chr(65+i)+':\\')==3]
    return [Path('/')]


def protected(path, *, broad=True):
    """Exclude system/private credential locations from broad document discovery."""
    parts={part.casefold().rstrip(' .') for part in Path(path).parts}
    blocked={'windows','program files','program files (x86)','programdata','appdata',
             '$recycle.bin','system volume information','proc','sys','dev','etc',
             'credentials','secrets','tokens','node_modules','__pycache__','.venv','venv'}
    name=Path(path).name.casefold()
    return bool((broad and parts & blocked) or any(part.startswith('.') for part in parts)
                or name in {'credentials.json','secrets.json','id_rsa','id_ed25519'}
                or name.startswith('.env') or name.endswith(('.pem','.key','.pfx','.p12','.db','.sqlite')))


def document_roots(existing):
    config=configuration()
    context=_context.get()
    if not config['configured'] and not is_local(context):
        return existing
    from . import agent_file_service as files
    output=[str(files.workspace())]
    if config['read_scope']=='computer':
        return list(dict.fromkeys([*output,*(str(p) for p in local_roots())]))
    if config['read_scope']=='folders':
        return existing
    from . import code_workspace_service as code, workspace_service as workspace
    if is_local(context):
        project=workspace.project_for_conversation(context.conversation_id)
        if project:
            permission=code.configuration(project['id'])
            if permission['read_enabled'] and permission['root']:
                output.append(permission['root'])
    return list(dict.fromkeys(output))
