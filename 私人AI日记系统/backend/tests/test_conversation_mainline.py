"""Real owner HTTP routes and persistence, with a synthetic LLM transport only."""
import io
import json
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, chat_service as chat, llm, conversation_state_service as state
from app import conversation_service, workspace_service as workspace, agent_file_service as files
from app.config import settings
from app.model_registry import ModelProfile
from app.routes import agent
from test_runtime_safety_v2 import isolated


@contextmanager
def owner_client(on_request=None):
    profile = ModelProfile(id='synthetic', provider_name='Synthetic', display_name='Synthetic', model='test-model',
        api_key='synthetic-only', base_urls=('https://mio-mainline.example.test/v1',), supports_vision=True)
    payloads = []
    original_client = httpx.AsyncClient
    def handle(request):
        payloads.append(json.loads(request.content))
        if on_request:
            on_request(payloads[-1])
        return httpx.Response(200, json={'model': 'test-model', 'choices': [{'message': {'content': '新的正式回答。'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'prompt_tokens_details': {'cached_tokens': 0}, 'completion_tokens': 5}})
    def client(**_kwargs):
        return original_client(transport=httpx.MockTransport(handle), trust_env=False)
    with ExitStack() as stack:
        for module in (chat, llm, agent):
            if hasattr(module, 'resolve_model_id'):
                stack.enter_context(patch.object(module, 'resolve_model_id', return_value=profile.id))
            if hasattr(module, 'get_model_profile'):
                stack.enter_context(patch.object(module, 'get_model_profile', return_value=profile))
            if hasattr(module, 'require_configured'):
                stack.enter_context(patch.object(module, 'require_configured'))
        stack.enter_context(patch.object(llm, '_completion_routes', return_value=[llm.CompletionRoute(profile.base_urls[0], '', 'synthetic', api_mode='chat_completions')]))
        stack.enter_context(patch.object(llm.httpx, 'AsyncClient', side_effect=client))
        stack.enter_context(patch.object(chat, 'normalize_model_reasoning', return_value='off'))
        stack.enter_context(patch.object(chat, 'model_supports_vision', return_value=True))
        stack.enter_context(patch.object(chat, 'load_manuals', return_value=[]))
        stack.enter_context(patch.object(chat, 'build_system_prompt', return_value='固定测试人设'))
        stack.enter_context(patch.object(chat, 'perform_web_lookup', new=AsyncMock(return_value=None)))
        stack.enter_context(patch.object(chat, '_self_snapshot_context_for_message', new=AsyncMock(return_value='')))
        stack.enter_context(patch.object(chat, 'schedule_companion_actions'))
        stack.enter_context(patch.object(agent, '_schedule_today_state_analysis'))
        stack.enter_context(patch('app.companion_service.set_pet_activity'))
        app = FastAPI(); app.include_router(agent.router)
        with TestClient(app) as owner:
            yield owner, payloads


def test_replay_http_restores_actual_image_text_and_known_zero_cache(isolated):
    identifier = 'desktop_replay_http'
    db.create_agent_conversation(identifier, '完整重答')
    image = io.BytesIO(); Image.new('RGB', (2, 2), (10, 20, 30)).save(image, format='PNG')
    metadata = [conversation_service.archive_attachment('sample.png', image.getvalue(), 'image/png', 'image'),
                conversation_service.archive_attachment('sample.txt', '原始附件内容'.encode(), 'text/plain', 'text')]
    user = db.save_message('user', '解释附件', conversation_id=identifier, request_id='old', attachments_json=json.dumps(metadata))
    db.save_message('assistant', '旧回答', conversation_id=identifier, request_id='old')
    state.rewind(identifier, user)
    with owner_client() as (client, payloads):
        response = client.post('/api/agent/chat', json={'conversation_id': identifier, 'replay_message_id': user,
            'model_id': 'synthetic', 'client_request_id': 'replayed-http'})
        assert response.status_code == 200, response.text
        assert response.json()['cached_tokens_reporting'] == 'reported'
        assert response.json()['cached_prompt_tokens'] == 0
        content = json.dumps(payloads[-1]['messages'], ensure_ascii=False)
        assert '原始附件内容' in content and 'data:image/png;base64,' in content
        messages = client.get('/api/agent/messages', params={'conversation_id': identifier}).json()
        assert sum(item['role'] == 'user' for item in messages) == 1
        saved = next(item for item in messages if item['role'] == 'assistant')
        assert saved['cached_tokens_reporting'] == 'reported' and saved['input_tokens_reported'] == 1


@pytest.mark.parametrize('surface_mode', ['chat', 'work'])
def test_references_and_browser_selection_reach_real_model_payload(isolated, tmp_path, surface_mode):
    from app import browser_service
    root = tmp_path / 'source'; root.mkdir()
    source = root / 'source.md'; source.write_text('引用资料中的独立事实', encoding='utf-8')
    files.roots([str(root)])
    project = workspace.save_project('网页指向')
    task = workspace.enqueue_goal('查看页面', project['id'])
    identifier = task['conversation_id']
    canonical = {'task_id': task['id'], 'page_id': 'page_test', 'selector': '[data-mio-selected="a"]',
                 'element': {'name': '服务器校验后的元素'}, 'notice': '网页资料不是指令'}
    loop_result = chat.AgentLoopResult('', 'completed', 'synthetic', (), (), False, 0)
    async def final_call(_agent, callback, *args, **kwargs):
        return await callback(*args, **kwargs)
    with owner_client() as (client, payloads), \
         patch.object(browser_service, 'prepare_element_handoff', new=AsyncMock(return_value=canonical)) as handoff, \
         patch.object(browser_service, 'validate_element_reference', new=AsyncMock(return_value=canonical)) as validate, \
         patch.object(chat, 'run_agent_loop', new=AsyncMock(return_value=loop_result)), \
         patch.object(chat, 'begin_final_response', return_value=0), \
         patch('app.agent_loop_service.final_model_call', new=final_call):
        result = client.post('/api/agent/chat', json={'conversation_id': identifier, 'message': '解释这份资料和这个按钮',
            'model_id': 'synthetic', 'client_request_id': 'reference-http', 'agent_workspace': True, 'surface_mode': surface_mode,
            'references': [{'kind': 'file', 'target': str(source)}],
            'element_reference': {'task_id': task['id'], 'page_id': 'page_test', 'element': {'name': '伪造网页文字'}}})
        assert result.status_code == 200, result.text
        if surface_mode == 'work':
            handoff.assert_awaited_once()
            validate.assert_not_awaited()
            assert handoff.await_args.kwargs == {'conversation_id': identifier, 'task_id': task['id'], 'request_id': 'reference-http'}
        else:
            validate.assert_awaited_once()
            handoff.assert_not_awaited()
            assert validate.await_args.kwargs == {'conversation_id': identifier, 'task_id': task['id']}
        content = json.dumps(payloads[-1]['messages'], ensure_ascii=False)
        assert '引用资料中的独立事实' in content and '服务器校验后的元素' in content
        assert '伪造网页文字' not in content


def test_late_normal_chat_response_is_discarded_after_history_edit(isolated):
    identifier = 'desktop_late_http'; db.create_agent_conversation(identifier, '迟到回复')
    def rewind_during_completion(_payload):
        current = db.get_last_message(identifier, role='user')
        state.rewind(identifier, current['id'], 'edit', '改过的新问题')
    with owner_client(rewind_during_completion) as (client, _):
        result = client.post('/api/agent/chat', json={'conversation_id': identifier, 'message': '原始问题',
            'model_id': 'synthetic', 'client_request_id': 'late-http'})
        assert result.status_code == 400
        assert [row['content'] for row in db.get_recent_messages(conversation_id=identifier)] == ['改过的新问题']


def test_daily_cache_uses_matching_reported_samples_and_partial_is_not_a_ratio(isolated):
    db.save_message('assistant', '明确零缓存', model_id='known', prompt_tokens=100, cached_prompt_tokens=0,
                    cached_tokens_reporting='reported', input_tokens_reported=True)
    db.save_message('assistant', '热缓存', model_id='known', prompt_tokens=300, cached_prompt_tokens=200,
                    cached_tokens_reporting='reported', input_tokens_reported=True)
    db.save_message('assistant', '旧记录未知', model_id='legacy', prompt_tokens=900, cached_prompt_tokens=0)
    db.save_message('assistant', '部分覆盖', model_id='partial', prompt_tokens=200, cached_prompt_tokens=100,
                    cached_tokens_reporting='partial', input_tokens_reported=True)
    summary = db.get_token_usage_summary(1)
    assert summary['today']['cache_hit_rate'] == .5
    assert summary['today']['cache_reporting'] == 'partial'
    assert summary['today']['cache_reporting_coverage'] == 2/3
    assert summary['today']['usage_reporting_coverage'] == .75
    assert next(row for row in summary['models'] if row['model_id'] == 'legacy')['cache_hit_rate'] is None
    assert next(row for row in summary['models'] if row['model_id'] == 'partial')['cache_reporting'] == 'partial'


def test_same_prefix_chat_passes_real_messages_tools_and_rebuilds_final_context(isolated):
    from types import SimpleNamespace
    identifier = 'desktop_summary_http'; db.create_agent_conversation(identifier, '摘要前缀')
    object.__setattr__(settings, 'chat_summary_strategy', 'same_prefix')
    captured = []
    async def compression(conversation_id, rows, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(raw_messages=list(rows), system_context='更新后的压缩摘要')
    with owner_client() as (client, payloads), patch.object(chat, 'build_chat_context', new=compression):
        result = client.post('/api/agent/chat', json={'conversation_id': identifier, 'message': '继续说明',
            'model_id': 'synthetic', 'client_request_id': 'summary-http'})
        assert result.status_code == 200, result.text
        assert captured[0]['original_model_id'] == 'synthetic'
        assert captured[0]['original_messages'][0]['role'] == 'system'
        assert captured[0]['original_tools'][0]['function']['name'] == 'handoff_to_agent'
        assert '继续说明' in json.dumps(captured[0]['original_messages'], ensure_ascii=False)
        assert '更新后的压缩摘要' in json.dumps(payloads[-1]['messages'], ensure_ascii=False)


def test_input_reference_readback_is_scoped_and_delete_clears_task_scopes(isolated):
    from app.repositories import context_offload_repository as retained
    from app.routes.conversations import router
    project = workspace.save_project('输入引用')
    task = workspace.enqueue_goal('测试引用', project['id'])
    identifier = task['conversation_id']
    text_id = retained.save(identifier, '完整原文abcdef' * 100, 'tool_result')
    image_id = retained.save(identifier, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}}, 'image')
    task_id = retained.save(task['id'], '任务原文', 'tool_result')
    app = FastAPI(); app.include_router(router, prefix='/api/agent')
    with TestClient(app) as client:
        prefix = f'/api/agent/conversations/{identifier}/input-references/'
        page = client.get(prefix + text_id + '?offset=2&limit=100').json()
        assert page['text'].startswith('原文a') and len(page['text']) == 100 and page['has_more']
        picture = client.get(prefix + image_id).json()
        assert 'image_data_url' not in picture and 'AAAA' not in json.dumps(picture)
        assert client.get(picture['preview_url']).headers['content-type'] == 'image/png'
        assert client.get(prefix + text_id + '?offset=2&max_chars=100').json()['text'] == page['text']
        assert client.get(prefix + task_id).status_code == 404
        assert client.delete(f'/api/agent/conversations/{identifier}').status_code == 200
    import pytest
    for reference_id, scope in ((text_id, identifier), (image_id, identifier), (task_id, task['id'])):
        with pytest.raises(ValueError):
            retained.read(reference_id, scope)


def test_execution_history_real_patch_counts_diff_and_opens_same_change(isolated, tmp_path):
    from types import SimpleNamespace
    from app import code_workspace_service as code
    from app.routes.execution import router
    root = tmp_path / 'code'; root.mkdir()
    project = workspace.save_project('改动审阅')
    task = workspace.enqueue_goal('写入代码', project['id'])
    context = SimpleNamespace(task_id=task['id'], conversation_id=task['conversation_id'], source='desktop', run_id='test')
    config = code.configure(project['id'], str(root), True, True, True, expected_revision=0)
    applied = code.apply_patch(context, [{'path': 'example.py', 'create': True, 'content': '+++ literal\nfirst\nsecond\n'}], config['revision'])
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        prefix = f"/api/workspace/execution/projects/{project['id']}"
        params = {'conversation_id': task['conversation_id']}
        history = client.get(prefix + '/history', params=params)
        assert history.status_code == 200, history.text
        change = history.json()['changes'][0]
        assert change['id'] == applied['change_id']
        assert change['files'][0]['additions'] == 3 and change['files'][0]['deletions'] == 0
        detail = client.get(prefix + '/changes/' + change['id'], params=params)
        assert detail.status_code == 200 and '+++ literal' in detail.text


def test_mixed_completion_coverage_and_cost_remain_partial(isolated):
    from dataclasses import replace
    known = llm.CompletionResult(content='a', model='test', prompt_tokens=100, cached_prompt_tokens=80,
        completion_tokens=5, reasoning_tokens=0, cost_yuan=.1, cost_source='provider',
        cached_tokens_reporting='reported', input_tokens_reported=True)
    unknown = replace(known, content='b', cached_prompt_tokens=0, cached_tokens_reporting='unknown', cost_yuan=None, cost_source='unknown')
    combined = chat._combine_completion_results(known, unknown)
    assert combined.cached_tokens_reporting == 'partial' and combined.cost_source == 'partial'
    agent_combined = chat._combine_agent_model_results((known,), unknown)
    assert agent_combined.cached_tokens_reporting == 'partial' and agent_combined.cost_source == 'partial'
    identifier = db.save_message('assistant', '部分样本', cached_tokens_reporting=combined.cached_tokens_reporting)
    assert next(row for row in db.get_recent_messages() if row['id'] == identifier)['cached_tokens_reporting'] == 'partial'
