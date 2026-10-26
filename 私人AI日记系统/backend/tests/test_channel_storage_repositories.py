"""Storage-boundary regressions for channel, memory and supporting services."""
import importlib
import sqlite3
from types import SimpleNamespace

import pytest

from app import db, mobile_task_service
from app.repositories import migration_repository, mobile_task_service_repository


DOMAINS = ('autonomy', 'conversation', 'conversation_state', 'external_notes',
           'knowledge', 'learning', 'memory_revision', 'mobile_file',
           'mobile_task', 'onboarding', 'routine', 'wechat')
ADDITIONAL_MODULES = ('mobile_gateway', 'wechat_history', 'diagnostic_export', 'review_sources')


class TrackingConnection(sqlite3.Connection):
    closed = False

    def close(self):
        self.closed = True
        super().close()


@pytest.mark.parametrize('domain', DOMAINS + ADDITIONAL_MODULES)
@pytest.mark.parametrize('mode', ('read', 'transaction'))
@pytest.mark.parametrize('fail', (False, True))
def test_owned_scope_closes_connections_on_success_and_failure(domain, mode, fail, monkeypatch):
    connection = sqlite3.connect(':memory:', factory=TrackingConnection)
    monkeypatch.setattr(db, 'get_conn', lambda: connection)
    suffix = '_repository' if domain in ADDITIONAL_MODULES else '_service_repository'
    repository = importlib.import_module('app.repositories.' + domain + suffix)
    try:
        with getattr(repository, mode)():
            if fail:
                raise RuntimeError('synthetic storage boundary failure')
    except RuntimeError:
        assert fail
    assert connection.closed
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute('SELECT 1')


def test_mobile_handoff_and_outbox_join_callers_transaction_without_committing(tmp_path, monkeypatch):
    path = tmp_path / 'mobile.db'
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    store = mobile_task_service_repository.bind(connection)
    store.initialize_initialize_mobile_task_bindings()
    monkeypatch.setattr(mobile_task_service, 'valid_origin', lambda origin: True)
    origin = {'self_id': '90001', 'user_id': '10001'}
    mobile_task_service.bind_handoff(connection, 'task_1', origin)
    assert connection.in_transaction
    assert connection.execute('SELECT COUNT(*) FROM mobile_task_bindings').fetchone()[0] == 1
    assert connection.execute('SELECT COUNT(*) FROM mobile_task_outbox').fetchone()[0] == 1
    connection.rollback()
    assert connection.execute('SELECT COUNT(*) FROM mobile_task_bindings').fetchone()[0] == 0
    assert connection.execute('SELECT COUNT(*) FROM mobile_task_outbox').fetchone()[0] == 0
    mobile_task_service.queue(origin, 'durable result', key='result', conn=connection)
    connection.commit()
    assert connection.execute('SELECT COUNT(*) FROM mobile_task_outbox').fetchone()[0] == 1
    connection.close()


def test_failed_migration_rolls_back_data_and_ledger_and_closes_connection(tmp_path, monkeypatch):
    path = tmp_path / 'migration.db'
    with sqlite3.connect(path) as setup:
        setup.execute('CREATE TABLE synthetic_migration_data(value TEXT)')
    opened = []

    def connect():
        connection = sqlite3.connect(path, factory=TrackingConnection)
        connection.row_factory = sqlite3.Row
        opened.append(connection)
        return connection

    monkeypatch.setattr(db, 'get_conn', connect)

    def failed_apply(connection):
        connection.execute("INSERT INTO synthetic_migration_data VALUES('must rollback')")
        raise RuntimeError('synthetic migration failure')

    with pytest.raises(RuntimeError, match='synthetic migration failure'):
        with migration_repository.ledger() as ledger:
            ledger.apply(SimpleNamespace(version=91, name='failure', apply=failed_apply), lambda: 'now')
    assert opened[0].closed
    with sqlite3.connect(path) as verify:
        assert verify.execute('SELECT COUNT(*) FROM synthetic_migration_data').fetchone()[0] == 0
        assert verify.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 0
