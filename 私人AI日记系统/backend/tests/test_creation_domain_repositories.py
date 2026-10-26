"""Creation persistence keeps atomic retries, scan rollback and closed handles."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app import db, creation_service as creation, creation_provenance as provenance
from app import workspace_service as workspace
from app.config import settings
from app.creation_models import CreationJobRequest
from app.repositories import music_repository as music
from app.repositories import daily_diary_repository as diary
from test_runtime_safety_v2 import isolated


def test_concurrent_same_creation_key_reuses_one_job(isolated):
    payload = CreationJobRequest(prompt='synthetic image', idempotency_key='same-key')

    def create(_):
        return creation.create_job(payload, conversation_id='desktop_atomic', force_confirmation=True)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(create, range(4)))
    assert len({job['id'] for job, created in results}) == 1
    assert sum(created for job, created in results) == 1
    assert all(job['status'] == 'needs_confirmation' for job, created in results)


def test_failed_provenance_binding_rolls_back_new_creation(isolated, monkeypatch):
    project = workspace.save_project('合成事务测试')
    task = workspace.enqueue_goal('合成目标', project['id'])

    def fail_binding(*args, **kwargs):
        raise RuntimeError('synthetic binding failure')

    monkeypatch.setattr(provenance, 'bind', fail_binding)
    with pytest.raises(RuntimeError, match='synthetic binding failure'):
        creation.create_job(CreationJobRequest(prompt='synthetic image', idempotency_key='rollback'),
                            conversation_id=task['conversation_id'], task_id=task['id'], force_confirmation=True)
    with creation.repository.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creation_jobs WHERE idempotency_key='rollback'").fetchone()[0] == 0


def test_interrupted_music_scan_rolls_back_entire_batch(isolated):
    music.initialize()

    def records():
        yield ('track_one', 'synthetic/path', 'one.wav', 12, 0, db.now_iso())
        raise RuntimeError('synthetic scan interrupted')

    with pytest.raises(RuntimeError, match='synthetic scan interrupted'):
        music.upsert_scanned(records())
    assert music.list_tracks(50) == []


def test_diary_stream_and_creation_transaction_close_on_failure(isolated, monkeypatch):
    handles = []

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def connect():
        connection = sqlite3.connect(settings.db_path, factory=TrackedConnection)
        connection.row_factory = sqlite3.Row
        handles.append(connection)
        return connection

    monkeypatch.setattr(db, 'get_conn', connect)
    with pytest.raises(RuntimeError):
        with diary.source_dates('2099-01-01'):
            raise RuntimeError('synthetic discovery failure')
    with pytest.raises(RuntimeError):
        with creation.repository.transaction():
            raise RuntimeError('synthetic write failure')
    assert len(handles) == 2 and all(connection.closed for connection in handles)
