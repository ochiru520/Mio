"""Rollback and connection-lifetime regressions for execution persistence boundaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib
import sqlite3
from unittest.mock import patch

import pytest

from app import agent_handoff_service as handoff, agent_input_service as inputs
from app import agent_task_service as tasks, code_workspace_service as code, db
from app import workspace_service as workspace
from app.repositories import agent_handoff_repository, agent_input_repository, code_workspace_repository
from test_agent_handoff import context
from test_runtime_safety_v2 import isolated


def make_task():
    return tasks.prepare(run_id='repository-run', request_id='repository-request',
                         conversation_id='desktop_agent_repository', source='desktop',
                         user_message='synthetic task', model_id='test', reasoning_level='off',
                         allowed_tools=[], context=[])


def test_handoff_failure_rolls_back_conversation_task_and_run():
    with patch('app.companion_service.load_config', return_value={'chat_model_id': 'test', 'chat_reasoning_level': 'off'}), \
         patch.object(agent_handoff_repository.Store, 'insert_task_run', side_effect=sqlite3.OperationalError('synthetic failure')):
        with pytest.raises(sqlite3.OperationalError, match='synthetic failure'):
            handoff.create_handoff({'goal': 'synthetic handoff'}, context('rollback-handoff'))
    with closing(db.get_conn()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE source='chat_handoff'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM agent_conversations WHERE id LIKE 'desktop_agent_%'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM agent_task_runs WHERE run_id LIKE 'handoff:%'").fetchone()[0] == 0


def test_amendment_failure_rolls_back_input_snapshot_and_message():
    task = make_task()
    with patch.object(agent_input_repository.Store, 'invalidate_unclaimed_approvals', side_effect=sqlite3.OperationalError('synthetic failure')):
        with pytest.raises(sqlite3.OperationalError, match='synthetic failure'):
            inputs.append(task['id'], 'synthetic amendment', 'rollback-input')
    current = tasks.get(task['id'])
    assert current['snapshot'] == task['snapshot']
    assert current['revision'] == task['revision']
    assert inputs.read(task['id'])['inputs'] == []
    with closing(db.get_conn()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE delivery_key LIKE 'task-input:%'").fetchone()[0] == 0


def test_amendments_serialize_versions_and_replay_same_request_once():
    task = make_task()
    inputs.initialize()
    with ThreadPoolExecutor(max_workers=4) as pool:
        inserted = list(pool.map(lambda i: inputs.append(task['id'], f'amendment {i}', f'unique-{i}'), range(4)))
    assert sorted(item['version'] for item in inserted) == [1, 2, 3, 4]
    with ThreadPoolExecutor(max_workers=4) as pool:
        replayed = list(pool.map(lambda _: inputs.append(task['id'], 'shared amendment', 'same-request'), range(4)))
    assert len({item['id'] for item in replayed}) == 1
    actual = inputs.read(task['id'])['inputs']
    assert [item['version'] for item in actual] == [1, 2, 3, 4, 5]
    assert tasks.get(task['id'])['snapshot']['input_version'] == 5


def test_project_binding_failure_rolls_back_permission_and_directory(tmp_path):
    project = workspace.save_project('repository rollback')
    root = tmp_path / 'source'; root.mkdir()
    with patch.object(code_workspace_repository.Store, 'bind_project_directory', side_effect=sqlite3.OperationalError('synthetic failure')):
        with pytest.raises(sqlite3.OperationalError, match='synthetic failure'):
            code.configure(project['id'], str(root), True, True, True, expected_revision=0)
    with closing(db.get_conn()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM code_workspaces WHERE project_id=?', (project['id'],)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM workspace_project_directories WHERE project_id=?', (project['id'],)).fetchone()[0] == 0


@pytest.mark.parametrize('domain', [
    'agent_file', 'agent_handoff', 'agent_input', 'agent_loop', 'agent_source',
    'browser', 'code_workspace', 'companion_action', 'desktop_control', 'terminal',
    'execution_policy', 'execution_capability_defaults', 'model_runtime',
    'agent_protocol_history', 'agent_result_context', 'context_tools',
])
def test_execution_work_unit_closes_connection_and_rolls_back_on_failure(domain, tmp_path):
    repository = importlib.import_module(f'app.repositories.{domain}_repository')

    class Connection(sqlite3.Connection):
        was_closed = False

        def close(self):
            self.was_closed = True
            super().close()

    path = tmp_path / 'rollback.db'
    connection = sqlite3.connect(path, factory=Connection)
    with patch.object(repository.db, 'get_conn', return_value=connection):
        with pytest.raises(RuntimeError, match='synthetic failure'):
            with repository.unit_of_work() as store:
                store.connection.execute('CREATE TABLE probe(value TEXT)')
                store.connection.execute("INSERT INTO probe VALUES('synthetic')")
                raise RuntimeError('synthetic failure')
    assert connection.was_closed
    with closing(sqlite3.connect(path)) as verification:
        assert verification.execute('SELECT COUNT(*) FROM probe').fetchone()[0] == 0
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')
