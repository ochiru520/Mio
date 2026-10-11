import json
from unittest.mock import patch

from app import agent_file_service as files, db, knowledge_service as knowledge, workspace_service as workspace
from app.agent_citation_service import decode_attachments
from app.chat_service import ChatResult, persist_generated_chat_result
from test_runtime_safety_v2 import isolated


def test_deferred_reply_saves_citations_for_each_bubble_and_preserves_original_excerpt(isolated, tmp_path):
    folder = tmp_path / 'sources'
    folder.mkdir()
    path = folder / 'source.md'
    path.write_text('星桥核验编号为DOC-7391。完成数量是42。', encoding='utf-8')
    files.roots([str(folder)])
    project = workspace.save_project('引用持久化')
    index = knowledge.index_document(project['id'], str(path))
    evidence = knowledge.search_for_agent(project['id'], '星桥')
    replies = ['星桥核验编号为DOC-7391。', '完成数量是42。']
    result = ChatResult(reply=' '.join(replies), replies=replies, request_id='citation-split',
                        agent_run_id='run-citation', tool_receipts=({'tool_name': 'knowledge_search', 'result': evidence},))
    with patch('app.chat_service.commit_deferred_final_response'):
        persist_generated_chat_result('查询星桥资料', result, conversation_id='desktop_agent_citation', source='desktop')
    rows = [row for row in db.get_recent_messages(conversation_id='desktop_agent_citation') if row['role'] == 'assistant']
    assert len(rows) == 2
    for row, text in zip(rows, replies):
        manifest = decode_attachments(row['attachments_json'])[0]
        assert manifest['kind'] == 'citation_manifest'
        assert manifest['sentences'][0]['text'] == text and manifest['sentences'][0]['source_keys']
        assert manifest['source_snapshot'][0]['document_id'] == index['id']
        assert manifest['source_snapshot'][0]['sha256'] == index['sha256']
    original = [row['attachments_json'] for row in rows]
    path.write_text('修改后的源文件。', encoding='utf-8')
    restored = [row for row in db.get_recent_messages(conversation_id='desktop_agent_citation') if row['role'] == 'assistant']
    assert [row['attachments_json'] for row in restored] == original
    assert 'DOC-7391' in json.dumps([decode_attachments(row['attachments_json']) for row in restored])
