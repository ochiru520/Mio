"""Storage ownership, concurrent revisions and failed deletion recovery."""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException

from app import db, conversation_service, workspace_service as workspace
from app.repositories.workspace_repository import WorkspaceUnit
from app.routes.conversations import delete_conversation
from test_runtime_safety_v2 import isolated


def test_concurrent_note_saves_accept_only_one_revision(isolated, monkeypatch):
    project = workspace.save_project('并发笔记')
    note = workspace.add_note(project['id'], '标题', '初始内容')
    barrier = Barrier(4)
    original = WorkspaceUnit.note_state

    def slow_state_read(unit, parameters):
        cursor = original(unit, parameters)
        # Overlap competing saves across a realistically slow revision read.
        time.sleep(0.05)
        return cursor

    monkeypatch.setattr(WorkspaceUnit, 'note_state', slow_state_read)

    def save(index):
        barrier.wait(timeout=5)
        content = f'窗口 {index} 保存的内容'
        try:
            result = workspace.add_note(project['id'], '标题', content,
                                        note_id=note['id'], expected_revision=1)
            return ('saved', content, result['revision'])
        except ValueError as exc:
            assert '其他窗口更新' in str(exc)
            return ('conflict', content, None)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(save, range(4)))
    winners = [result for result in results if result[0] == 'saved']
    assert len(winners) == 1
    assert sum(result[0] == 'conflict' for result in results) == 3
    saved = workspace.collection_page(project['id'], 'notes')['notes'][0]
    assert saved['revision'] == winners[0][2] == 2
    assert saved['content'] == winners[0][1]


def test_note_id_cannot_update_another_project(isolated):
    first = workspace.save_project('项目 A')
    second = workspace.save_project('项目 B')
    note = workspace.add_note(first['id'], '原始标题', '原始内容')
    with pytest.raises(ValueError, match='不属于当前项目'):
        workspace.add_note(second['id'], '错误标题', '错误内容',
                           note_id=note['id'], expected_revision=1)
    saved = workspace.collection_page(first['id'], 'notes')['notes'][0]
    assert (saved['title'], saved['content'], saved['revision']) == ('原始标题', '原始内容', 1)
    assert workspace.collection_page(second['id'], 'notes')['notes'] == []


def assert_failed_delete_preserves_conversation(conversation_id):
    before = [dict(row) for row in db.get_recent_messages(conversation_id=conversation_id)]
    with pytest.raises(HTTPException) as error:
        asyncio.run(delete_conversation(conversation_id))
    assert error.value.status_code == 409
    assert '暂存不可用' in error.value.detail
    assert not db.conversation_deleted(conversation_id)
    db.assert_conversation_writable(conversation_id)
    assert db.get_agent_conversation(conversation_id) is not None
    assert [dict(row) for row in db.get_recent_messages(conversation_id=conversation_id)] == before
    db.save_message('user', '仍可继续写入', conversation_id=conversation_id)
    assert db.get_last_message(conversation_id)['content'] == '仍可继续写入'


@pytest.mark.parametrize('error_type', [OSError, PermissionError])
def test_attachment_staging_error_restores_conversation(isolated, monkeypatch, error_type):
    conversation_id = 'desktop_staging_failure'
    db.create_agent_conversation(conversation_id, '保留对话')
    db.save_message('user', '删除失败后必须保留', conversation_id=conversation_id)

    def fail(*args, **kwargs):
        raise error_type('暂存不可用')

    monkeypatch.setattr(conversation_service, 'stage_archived_attachments', fail)
    assert_failed_delete_preserves_conversation(conversation_id)


def test_real_attachment_staging_directory_failure_restores_conversation(isolated, monkeypatch):
    conversation_id = 'desktop_mkdtemp_failure'
    db.create_agent_conversation(conversation_id, '保留对话')

    def fail(*args, **kwargs):
        raise PermissionError('暂存不可用')

    monkeypatch.setattr(conversation_service.tempfile, 'mkdtemp', fail)
    assert_failed_delete_preserves_conversation(conversation_id)
