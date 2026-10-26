"""Persistent capability defaults for projects without explicit local grants."""
import hashlib
import json

from . import db
from .repositories import execution_capability_defaults_repository as repository

DEFAULTS = {'read_enabled': True, 'write_enabled': True,
            'execute_enabled': True, 'desktop_enabled': True}


def initialize():
    with repository.unit_of_work() as store:
        store.initialize(json.dumps(DEFAULTS), db.now_iso())


def configuration(conn=None):
    if conn is None:
        initialize()
        with repository.unit_of_work() as store:
            row = store.stored_defaults()
    else:
        row = repository.stored_defaults(conn)
    return {**DEFAULTS, **json.loads(row['settings_json']), 'revision': row['revision']}


def configure(*, expected_revision, **changes):
    if set(changes) - set(DEFAULTS) or any(not isinstance(value, bool) for value in changes.values()):
        raise ValueError('默认能力只支持读取、修改、命令与电脑操作开关。')
    initialize()
    with repository.unit_of_work() as store:
        store.lock_for_update()
        previous = configuration(store.connection)
        if previous['revision'] != expected_revision:
            raise ValueError('默认能力已在另一个窗口改变，请刷新后保存。')
        values = {key: changes.get(key, previous[key]) for key in DEFAULTS}
        if (values['write_enabled'] or values['execute_enabled']) and not values['read_enabled']:
            raise ValueError('修改或运行代码前须启用默认读取能力。')
        store.save_defaults(previous['revision'] + 1, json.dumps(values), db.now_iso())
    return configuration()


def inherited_revision(project_id, kind, scope='', defaults=None):
    # A stable JS-safe integer binds a tool receipt to defaults and its exact scope.
    current = defaults or configuration()
    payload = json.dumps([project_id, kind, scope, current['revision']], ensure_ascii=False)
    digest = int.from_bytes(hashlib.sha256(payload.encode('utf-8')).digest()[:6], 'big')
    return (1 << 50) + digest
