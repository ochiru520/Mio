"""Persistent capability defaults for projects without explicit local grants."""
from contextlib import closing
import hashlib
import json

from . import db

DEFAULTS = {'read_enabled': True, 'write_enabled': True,
            'execute_enabled': True, 'desktop_enabled': True}


def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS execution_capability_defaults(
            id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
            settings_json TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('INSERT OR IGNORE INTO execution_capability_defaults VALUES(1,1,?,?)',
                     (json.dumps(DEFAULTS), db.now_iso()))


def configuration(conn=None):
    if conn is None:
        initialize()
        with closing(db.get_conn()) as current:
            return configuration(current)
    row = conn.execute('SELECT * FROM execution_capability_defaults WHERE id=1').fetchone()
    return {**DEFAULTS, **json.loads(row['settings_json']), 'revision': row['revision']}


def configure(*, expected_revision, **changes):
    if set(changes) - set(DEFAULTS) or any(not isinstance(value, bool) for value in changes.values()):
        raise ValueError('默认能力只支持读取、修改、命令与电脑操作开关。')
    initialize()
    with closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = configuration(conn)
        if previous['revision'] != expected_revision:
            raise ValueError('默认能力已在另一个窗口改变，请刷新后保存。')
        values = {key: changes.get(key, previous[key]) for key in DEFAULTS}
        if (values['write_enabled'] or values['execute_enabled']) and not values['read_enabled']:
            raise ValueError('修改或运行代码前须启用默认读取能力。')
        conn.execute('UPDATE execution_capability_defaults SET revision=?,settings_json=?,updated_at=? WHERE id=1',
                     (previous['revision'] + 1, json.dumps(values), db.now_iso()))
    return configuration()


def inherited_revision(project_id, kind, scope='', defaults=None):
    # A stable JS-safe integer binds a tool receipt to defaults and its exact scope.
    current = defaults or configuration()
    payload = json.dumps([project_id, kind, scope, current['revision']], ensure_ascii=False)
    digest = int.from_bytes(hashlib.sha256(payload.encode('utf-8')).digest()[:6], 'big')
    return (1 << 50) + digest
