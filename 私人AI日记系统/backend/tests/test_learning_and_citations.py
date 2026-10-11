import json

from app import db, learning_service, music_service, workspace_service
from app.agent_citation_service import attachment, decode_attachments
from test_runtime_safety_v2 import isolated


def test_learning_session_preserves_attempt_progress(isolated):
    project = workspace_service.save_project('学习闭环')
    session = learning_service.create_session(project['id'], '资料复习', '掌握关键概念')
    item = learning_service.add_item(project['id'], session['id'], '什么是可验证来源？', '版本,快照', '第 2 页')
    result = learning_service.answer(project['id'], item['id'], '需要保留来源版本和快照')
    assert result['score'] == 1
    current = learning_service.session(project['id'], session['id'])
    assert current['progress'] == {'total': 1, 'answered': 1, 'average_score': 1.0}
    assert current['items'][0]['latest_attempt']['feedback'] == '要点已覆盖。'


def test_learning_review_queue_returns_unanswered_and_low_score_items(isolated):
    project = workspace_service.save_project('复习队列')
    session = learning_service.create_session(project['id'], '复习', '巩固')
    unanswered = learning_service.add_item(project['id'], session['id'], '未作答题')
    low = learning_service.add_item(project['id'], session['id'], '低分题', '关键点')
    learning_service.answer(project['id'], low['id'], '没有覆盖')
    review = learning_service.review(project['id'])
    assert {item['id'] for item in review['items']} == {unanswered['id'], low['id']}
    assert all('session_title' in item for item in review['items'])


def test_agent_citation_attachment_is_immutable_snapshot(isolated, tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('资料内容', encoding='utf-8')
    # The manifest builder must accept the same public receipt shape used by ChatResult.
    raw = attachment('结论。', [{
        'tool_name': 'knowledge_search',
        'result': {'results': [{'source': {'document_id': 'missing', 'sha256': 'abc', 'chunk_id': 1, 'name': '资料'}}]},
    }])
    assert raw['version'] == 1 and raw['sentences'][0]['source_keys'] == []
    assert decode_attachments(json.dumps([raw], ensure_ascii=False))[0]['kind'] == 'citation_manifest'


def test_agent_citation_manifest_maps_sentences_to_matching_evidence(isolated, tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('星空测试结论。海洋测试结论。', encoding='utf-8')
    from unittest.mock import patch
    from app import knowledge_service as knowledge
    digest = __import__('hashlib').sha256(source.read_bytes()).hexdigest()
    raw = {'document_id': 'doc_test', 'content': '星空测试结论。',
           'source': {'document_id': 'doc_test', 'sha256': digest, 'chunk_id': 1, 'name': '资料'}}
    with patch.object(knowledge, 'source_file', return_value=(source, {'project_id': 'project_test', 'path': str(source)})), \
         patch.object(knowledge, 'validate_agent_result', return_value={'results': [raw]}):
        result = attachment('星空测试结论。无关的说明。', [{'tool_name': 'knowledge_search', 'result': {'results': [raw]}}])
    assert result['sentences'][0]['source_keys']
    assert result['sentences'][1]['source_keys'] == []
    assert result['source_snapshot'][0]['excerpt'] == '星空测试结论。'


def test_project_note_revision_rejects_stale_external_update(isolated):
    project = workspace_service.save_project('笔记版本')
    first = workspace_service.add_note(project['id'], '记录', '版本一')
    second = workspace_service.add_note(project['id'], '记录', '版本二', note_id=first['id'], expected_revision=first['revision'])
    assert second['revision'] == 2
    import pytest
    with pytest.raises(ValueError, match='其他窗口更新'):
        workspace_service.add_note(project['id'], '记录', '旧窗口覆盖', note_id=first['id'], expected_revision=1)


def test_music_catalog_only_scans_authorized_audio_and_rejects_changed_file(isolated, tmp_path):
    root = tmp_path / 'music'; root.mkdir()
    audio = root / 'quiet.wav'; audio.write_bytes(b'RIFFsynthetic')
    ignored = root / 'notes.txt'; ignored.write_text('not audio', encoding='utf-8')
    from app import agent_file_service as files
    files.roots([str(root)])
    result = music_service.scan(str(root))
    assert [item['name'] for item in result['tracks']] == ['quiet.wav']
    audio.write_bytes(b'changed')
    try:
        music_service.file(result['tracks'][0]['id'])
    except ValueError as exc:
        assert '变化' in str(exc)
    else:
        raise AssertionError('changed audio must be rejected')
