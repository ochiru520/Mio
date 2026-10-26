"""History, drafts and optional local retrieval use disposable data only."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, workspace_service as workspace, learning_service as learning
from app import conversation_state_service as state, memory_service as memory
from app import agent_file_service as files, reference_service as references, knowledge_service as knowledge
from app.knowledge_retrieval import options
from app.routes.workspace import router
from test_runtime_safety_v2 import isolated


def conversation():
    identifier = 'desktop_agent_rewind_test'
    db.create_agent_conversation(identifier, '测试历史')
    user = db.save_message('user', '旧约束', conversation_id=identifier, request_id='original')
    reply = db.save_message('assistant', '旧回答', conversation_id=identifier, request_id='original')
    return identifier, user, reply


def test_edit_is_atomic_and_old_sources_cannot_return(isolated):
    identifier, user, reply = conversation()
    saved = memory.save_memory_item(layer='L1', category='plan', memory_key='test', content='旧约束',
        source_conversation_id=identifier, source_message_id=user, confidence=.95)
    result = state.rewind(identifier, user, 'edit', '新约束', reply)
    assert result['removed_message_ids'] == [user, reply]
    assert result['user_message']['id'] > reply
    assert [r['content'] for r in db.get_recent_messages(conversation_id=identifier)] == ['新约束']
    assert db.get_structured_memory(saved['id'])['status'] == 'archived'
    with pytest.raises(ValueError, match='拒绝迟到'):
        memory.save_memory_item(layer='L1', category='plan', memory_key='late', content='旧结论',
            source_conversation_id=identifier, source_message_id=user, confidence=.95)
    with pytest.raises(ValueError, match='拒绝迟到'):
        memory.save_memory_candidate(layer='L1', category='plan', memory_key='late', content='旧推测',
            source_conversation_id=identifier, source_message_id=user, confidence=.6)
    with pytest.raises(ValueError, match='拒绝迟到'):
        db.save_message('assistant', '迟到回答', conversation_id=identifier, request_id='original')
    with pytest.raises(ValueError, match='拒绝迟到'):
        db.replace_memory('conversation_summary', f'旧摘要<!-- last_message_id:{reply} -->', tags=identifier)


def test_regenerate_claim_and_compaction_boundary(isolated):
    identifier, user, reply = conversation()
    with pytest.raises(ValueError, match='对话已更新'):
        state.rewind(identifier, user, expected_latest_message_id=user)
    assert len(db.get_recent_messages(conversation_id=identifier)) == 2
    db.replace_memory('conversation_summary', f'摘要<!-- last_message_id:{user} -->', tags=identifier)
    with pytest.raises(ValueError, match='压缩摘要'):
        state.rewind(identifier, user)
    db.delete_memory('conversation_summary', tags=identifier)
    snapshot = state.capture_source(identifier)
    result = state.rewind(identifier, user)
    assert not state.source_is_current(snapshot)
    assert result['user_message']['id'] == user
    assert state.claim_replay(identifier, user, 'replay')['content'] == '旧约束'
    assert state.claim_replay(identifier, user, 'replay')['id'] == user
    with pytest.raises(ValueError, match='已经开始'):
        state.claim_replay(identifier, user, 'another')


def test_manual_memory_correction_survives_history_edit(isolated):
    from app.memory_revision_service import revise
    identifier, user, reply = conversation()
    saved = memory.save_memory_item(layer='L1', category='plan', memory_key='test', content='旧约束',
        source_conversation_id=identifier, source_message_id=user, confidence=.95)
    corrected = revise(saved['id'], 'correct', content='用户确认的独立事实')
    state.rewind(identifier, user, 'edit', '新约束')
    assert db.get_structured_memory(corrected)['status'] == 'active'


def test_rewind_http_and_user_only_turn_index(isolated):
    from app.routes.conversations import router as conversation_router
    identifier, user, reply = conversation()
    app = FastAPI(); app.include_router(conversation_router, prefix='/api/agent')
    with TestClient(app) as client:
        endpoint = f'/api/agent/conversations/{identifier}'
        turns = client.get(endpoint + '/turn-index?limit=1').json()
        assert turns['turns'] == [{'id': user, 'role': 'user', 'content': '旧约束'}]
        result = client.post(endpoint + '/rewind', json={'anchor_message_id': user, 'mode': 'edit',
            'content': '新内容', 'expected_latest_message_id': reply})
        assert result.status_code == 200
        assert result.json()['user_message']['content'] == '新内容'


def test_replay_reloads_images_text_and_references_safely(isolated):
    import io
    from PIL import Image
    from app.conversation_service import archive_attachment
    identifier = 'desktop_replay'
    db.create_agent_conversation(identifier, '附件回放')
    raw = io.BytesIO(); Image.new('RGB', (2, 2), (255, 0, 0)).save(raw, format='PNG')
    image = archive_attachment('sample.png', raw.getvalue(), 'image/png', 'image')
    text = archive_attachment('sample.txt', '原附件文字'.encode('utf-8'), 'text/plain', 'text')
    user = db.save_message('user', '查看附件', conversation_id=identifier,
        attachments_json=json.dumps([image, text]))
    prepared = state.read_replay_attachments(identifier, user)
    assert prepared['images'][0].content
    assert prepared['text_attachments'][0].content == '原附件文字'
    with pytest.raises(ValueError, match='来源已失效'):
        state.read_replay_attachments('desktop_other', user)
    # Archive paths must never escape their private attachment root.
    bad = dict(image, url='/agent-files/../../../secret.png')
    other = db.save_message('user', '无效附件', conversation_id=identifier, attachments_json=json.dumps([bad]))
    with pytest.raises((ValueError, OSError)):
        state.read_replay_attachments(identifier, other)


def test_draft_persists_and_does_not_count_as_answer(isolated):
    project = workspace.save_project('草稿项目')
    session = learning.create_session(project['id'], '试题')
    item = learning.add_item(project['id'], session['id'], '什么是原子操作？', '事务')
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        endpoint = f"/api/workspace/projects/{project['id']}/learning/{session['id']}"
        saved = client.put(endpoint + '/draft', json={'answers': {item['id']: '写到一半'},
            'flagged_item_ids': [item['id']], 'active_item_id': item['id'], 'expected_revision': 0})
        assert saved.status_code == 200
        current = client.get(endpoint).json()
        assert current['draft']['answers'][item['id']] == '写到一半'
        assert current['progress']['answered'] == 0
        conflict = client.put(endpoint + '/draft', json={'answers': {}, 'expected_revision': 0})
        assert conflict.status_code == 400
    learning.answer(project['id'], item['id'], '事务')
    assert learning.session(project['id'], session['id'])['draft']['answers'] == {}


def test_references_revalidate_grants_versions_and_projects(isolated, tmp_path):
    root = tmp_path / 'authorized'; root.mkdir()
    source = root / '带 空格.md'; source.write_text('独立资料', encoding='utf-8')
    files.roots([str(root)])
    result = references.resolve([{'kind': 'file', 'target': str(source)}])
    entry = result['resolved'][0]
    assert entry['preview']['text'] == '独立资料'
    source.write_text('更新内容', encoding='utf-8')
    assert references.resolve([{'kind': 'file', 'target': str(source), 'version': entry['version']}])['blocked']
    files.roots([])
    assert references.resolve([{'kind': 'file', 'target': str(source)}])['blocked']
    identifier, _, _ = conversation()
    project = workspace.save_project('隔离项目')
    assert references.resolve([{'kind': 'conversation', 'target': identifier}], project['id'])['blocked']
    workspace.bind_conversation(project['id'], identifier)
    assert references.resolve([{'kind': 'conversation', 'target': identifier}], project['id'])['resolved']


def test_optional_hybrid_calls_local_service_and_falls_back(isolated, tmp_path):
    root = tmp_path / 'search'; root.mkdir()
    source = root / 'transport.md'; source.write_text('automobile repair', encoding='utf-8')
    files.roots([str(root)])
    project = workspace.save_project('检索项目')
    knowledge.index_document(project['id'], str(source))
    second = root / 'rail.md'; second.write_text('railway maintenance', encoding='utf-8')
    knowledge.index_document(project['id'], str(second))
    calls = []
    rerank_available = [False]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(self.path)
            if self.path.endswith('/embeddings'):
                body = {'data': [{'index': index, 'embedding': [1.0, 0.0]} for index, _ in enumerate(data['input'])]}
                self.send_response(200)
            elif rerank_available[0]:
                body = {'results': [{'index': index, 'relevance_score': float(index)} for index, _ in enumerate(data['documents'])]}
                self.send_response(200)
            else:
                body = {'error': 'no reranker installed'}
                self.send_response(404)
            encoded = json.dumps(body).encode()
            self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(encoded)))
            self.end_headers(); self.wfile.write(encoded)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        options({'enabled': True, 'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'embedding_model': 'synthetic'})
        result = knowledge.search_for_agent(project['id'], 'car')
        assert any(item['content'] == 'automobile repair' for item in result['results'])
        assert '/v1/embeddings' in calls
        options({'rerank_model': 'synthetic-rerank'})
        fallback_rerank = knowledge.search(project['id'], 'car')
        assert fallback_rerank['retrieval']['mode'] == 'hybrid'
        assert fallback_rerank['retrieval']['fallback'] and not fallback_rerank['retrieval']['reranked']
        rerank_available[0] = True
        assert knowledge.search(project['id'], 'car')['retrieval']['reranked']
        source.write_text('new source', encoding='utf-8')
        assert all(item['content'] != 'automobile repair' for item in knowledge.search_for_agent(project['id'], 'car')['results'])
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=2)
    fallback = knowledge.search(project['id'], 'automobile')
    assert fallback['retrieval']['mode'] == 'full_text'
    assert fallback['retrieval']['fallback']
    with pytest.raises(ValueError, match='仅支持本机'):
        options({'base_url': 'https://example.com/v1'})
