import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, memory_revision_service as revisions, creation_service as creation
from app import agent_task_service as tasks
from app.memory_service import save_memory_item, build_structured_memory_context
from app.prompts import build_system_prompt, build_group_system_prompt, build_diary_messages
from app.routes.memory import router
from test_runtime_safety_v2 import isolated, remote_job


def memory(content='喜欢喝浓咖啡'):
    message_id = db.save_message('user', content, conversation_id='desktop_test')
    result = save_memory_item(layer='L0', category='preference', memory_key='drink', content=content,
                              source_conversation_id='desktop_test', source_message_id=message_id, confidence=.95)
    return result['id'], message_id


def test_correction_preserves_source_and_propagates_to_private_prompts():
    old, message_id = memory()
    db.upsert_diary('2026-09-18', '历史', '喜欢喝浓咖啡', '', 'unknown')
    new = revisions.revise(old, 'correct', content='只喝温水', expected_content='喜欢喝浓咖啡')
    assert db.get_message_by_id(message_id)['content'] == '喜欢喝浓咖啡'
    assert db.get_diary('2026-09-18')['markdown_content'] == '喜欢喝浓咖啡'
    assert revisions.evidence(new)['source']['content'] == '喜欢喝浓咖啡'
    assert revisions.evidence(new)['kind'] == 'user_confirmed'
    assert '只喝温水' in build_structured_memory_context('desktop_test')
    for channel in ('desktop', 'qq', 'desktop_pet'):
        assert '只喝温水' in build_system_prompt([], channel=channel, compact=True)
    diary = build_diary_messages('2026-09-18', '喜欢喝浓咖啡')
    assert '只喝温水' in diary[0]['content']
    assert '喜欢喝浓咖啡' not in diary[1]['content']
    assert '只喝温水' not in build_group_system_prompt('today')
    with pytest.raises(ValueError):
        revisions.revise(old, 'correct', content='stale tab', expected_content='喜欢喝浓咖啡')
    candidate = save_memory_item(layer='L0', category='preference', memory_key='drink', content='喜欢喝浓咖啡',
                                 source_conversation_id='desktop_test', confidence=1)
    assert candidate['outcome'] == 'candidate'
    assert [r['content'] for r in db.list_structured_memories(status='active')] == ['只喝温水']



def test_archive_and_restore_update_control_without_destroying_history():
    first, _ = memory()
    second = revisions.revise(first, 'correct', content='只喝温水')
    revisions.revise(second, 'archive')
    assert not db.list_structured_memories(status='active')
    assert '只喝温水' not in revisions.sanitize_history('只喝温水')
    revisions.revise(first, 'restore')
    assert [r['id'] for r in db.list_structured_memories(status='active')] == [first]
    assert revisions.sanitize_history('喜欢喝浓咖啡') == '喜欢喝浓咖啡'


def test_evidence_api_and_stale_editor_conflict():
    first, _ = memory()
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        assert client.get(f'/api/memory/items/{first}/evidence').json()['source']['role'] == 'user'
        assert client.put(f'/api/memory/items/{first}', json={'content':'只喝温水','expected_content':'喜欢喝浓咖啡'}).status_code == 200
        assert client.put(f'/api/memory/items/{first}', json={'content':'旧页面覆盖','expected_content':'喜欢喝浓咖啡'}).status_code == 409


def test_remote_receipt_is_durable_and_never_claims_verified_artifact():
    job, _ = remote_job()
    creation._mark_remote_unknown(job['id'], 'synthetic')
    with patch.object(creation, 'schedule_job') as schedule:
        value = creation.reconcile_job(job['id'], outcome='completed_external', receipt='receipt-1', note='供应商页面显示已完成')
        assert value['status'] == 'unknown' and not value['outputs']
        assert value['spec']['reconciliations'][0]['checked_at']
        schedule.assert_not_called()
        value = creation.reconcile_job(job['id'], outcome='not_completed', receipt='receipt-2', note='供应商更正为未执行')
        assert value['status'] == 'failed'
        assert len(creation.get_job(job['id'])['spec']['reconciliations']) == 2


def test_late_remote_response_after_stop_cannot_publish_output():
    job, connection = remote_job()
    async def late(*args):
        creation._update_job(job['id'], status='cancel_requested')
        return b'late image'
    async def run():
        with patch.object(creation, '_provider_connection', return_value=connection), \
             patch.object(creation, '_request_remote_image', new=late), \
             patch.object(creation, 'archive_outputs') as archive:
            await creation._run_remote_job(job['id'], creation._get_job_row(job['id']))
            archive.assert_not_called()
        assert creation.get_job(job['id'])['status'] == 'unknown'
        assert not creation.get_job(job['id'])['outputs']
    asyncio.run(run())


def test_unknown_job_blocks_automatic_parent_continuation():
    job, _ = remote_job()
    creation._mark_remote_unknown(job['id'], 'synthetic')
    task = tasks.prepare(run_id='run-synthetic', request_id='request-synthetic', conversation_id='desktop_test',
                         source='desktop', user_message='create', model_id='mock', reasoning_level='off', allowed_tools=[], context=[])
    task = tasks.update(task['id'], status='waiting_jobs', waiting_jobs=[job['id']])
    assert not tasks.ready_to_continue(task)
    assert tasks.get(task['id'])['status'] == 'waiting_user'


def test_diagnostics_does_not_export_private_fields():
    from app.diagnostic_export import snapshot
    with patch('app.dependency_installer.list_dependencies', return_value=[{
        'id':'whisper', 'status':'unverified', 'path':'private-path', 'last_error':'private-key', 'api_key':'secret'}]):
        report = snapshot()
    assert report['checked_at']
    assert 'private' not in str(report) and 'secret' not in str(report)
    assert not report['dependencies'][0]['verified']
