"""Isolated HTTP and persistence contracts for cache semantics and input recovery."""
import asyncio
from contextlib import closing
import json
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from app import db, llm, model_runtime as runtime
from app import context_input_policy as policy
from app.repositories import prompt_metrics_repository as metrics
from app.repositories import context_offload_repository as offloads
from app.model_registry import ModelProfile
from test_runtime_safety_v2 import isolated


def profile(api_mode='chat_completions', window=32768):
    return ModelProfile(id='synthetic', provider_id='synthetic-provider', provider_name='Synthetic',
        display_name='Synthetic', model='synthetic-model', base_urls=('https://synthetic.invalid/v1',),
        api_key='not-a-key', api_mode=api_mode, context_window_tokens=window, context_window_source='configured')


def call(handler, messages=None, selected=None, stream=False, **kwargs):
    selected = selected or profile()
    constructor = httpx.AsyncClient
    route = llm.CompletionRoute(selected.base_urls[0], '', 'synthetic', api_mode=selected.api_mode)
    async def run():
        callback = llm.call_chat_completion_stream_result if stream else llm.call_chat_completion_result
        with patch.object(llm, 'get_model_profile', return_value=selected), patch.object(llm, 'resolve_model_id', return_value='synthetic'), patch.object(llm, '_completion_routes', return_value=[route]), patch.object(llm.httpx, 'AsyncClient', side_effect=lambda **options: constructor(transport=httpx.MockTransport(handler), trust_env=False)), patch.object(llm.asyncio, 'sleep', AsyncMock()):
            return await callback(messages or [{'role': 'user', 'content': 'synthetic'}], model_id='synthetic', request_id='cache-context-request', **kwargs)
    return asyncio.run(run())


def response(usage=None):
    return httpx.Response(200, json={'model': 'synthetic-model', 'choices': [{'message': {'content': 'synthetic'}}], **({'usage': usage} if usage is not None else {})})


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('api_mode', ['chat_completions', 'responses'])
@pytest.mark.parametrize('cache', [None, 0, 800])
def test_explicit_zero_and_unknown_survive_real_adapter_and_ledger(cache, api_mode, stream):
    def handler(request):
        usage = {'input_tokens': 1000, 'output_tokens': 10}
        if cache is not None:
            usage['input_tokens_details'] = {'cached_tokens': cache}
        if api_mode == 'responses':
            body = {'status': 'completed', 'model': 'synthetic-model', 'usage': usage, 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'synthetic'}]}]}
            events = [{'type': 'response.completed', 'response': body}]
        else:
            body = {'model': 'synthetic-model', 'choices': [{'message': {'content': 'synthetic'}}], 'usage': usage}
            events = [{'choices': [{'delta': {'content': 'synthetic'}, 'finish_reason': 'stop'}], 'usage': usage}]
        if not stream:
            return httpx.Response(200, json=body)
        return httpx.Response(200, text=''.join('data: '+json.dumps(event)+'\n\n' for event in events)+'data: [DONE]\n\n', headers={'content-type': 'text/event-stream'})
    result = call(handler, selected=profile(api_mode), stream=stream)
    assert result.input_tokens_reported
    assert result.cached_tokens_reporting == ('unknown' if cache is None else 'reported')
    with closing(db.get_conn()) as conn:
        row = dict(conn.execute('SELECT * FROM runtime_model_calls').fetchone())
    assert row['cached_prompt_tokens'] == cache
    assert row['cached_tokens_reporting'] == ('unknown' if cache is None else 'reported_zero' if cache == 0 else 'reported_positive')
    summary = metrics.usage_summary('2000', runtime.MODES)['totals']
    assert summary['calls'] == 1 and summary['input_usage_coverage'] == 1
    assert summary['cache_reporting_coverage'] == (0 if cache is None else 1)
    assert summary['cache_hit_ratio'] == (None if cache is None else cache/1000)


def test_real_attempts_include_retry_with_unknown_cost_without_double_counting():
    sent = []
    def handler(request):
        sent.append(request)
        return httpx.Response(503, json={'error': 'synthetic'}) if len(sent) == 1 else response({'prompt_tokens': 100, 'completion_tokens': 10, 'prompt_cache_hit_tokens': 0})
    call(handler, retry_attempts=2)
    summary = metrics.usage_summary('2000', runtime.MODES)
    assert summary['logical_calls'] == 1
    assert summary['totals']['calls'] == 2 and summary['totals']['retries'] == 1
    assert summary['totals']['prompt_tokens'] == 100
    assert summary['totals']['input_usage_coverage'] == .5
    assert summary['totals']['unknown_cost_calls'] == 2
    assert summary['totals']['cache_hit_ratio'] == 0
    assert any(item['model_id'] == 'synthetic-model' for item in summary['routes'])


def test_missing_usage_is_unknown_not_free():
    result = call(lambda request: response())
    assert result.cost_yuan is None and not result.input_tokens_reported
    summary = metrics.usage_summary('2000', runtime.MODES)['totals']
    assert summary['input_usage_coverage'] == 0 and summary['cache_hit_ratio'] is None
    assert summary['unknown_cost_calls'] == 1


def test_mixed_cache_samples_use_same_reported_denominator_and_expose_coverage():
    for tokens, cache, cost in [(100, 0, '.1'), (5000, None, None), (1000, 800, '.2')]:
        def handler(request):
            usage = {'prompt_tokens': tokens, 'completion_tokens': 10}
            if cache is not None:
                usage['prompt_cache_hit_tokens'] = cache
            result = response(usage)
            if cost is not None:
                result.headers['x-request-cost-cny'] = cost
            return result
        call(handler)
    total = metrics.usage_summary('2000', runtime.MODES)['totals']
    assert total['calls'] == 3 and total['prompt_tokens'] == 6100
    assert total['cache_hit_ratio'] == pytest.approx(800/1100)
    assert total['cache_reporting_coverage'] == pytest.approx(2/3)
    assert total['cache_input_token_coverage'] == pytest.approx(1100/6100)
    assert total['known_cost_yuan'] == pytest.approx(.3) and total['unknown_cost_calls'] == 1
    assert total['cache_write_tokens'] is None


def test_compatibility_fallback_is_two_real_attempts():
    sent = []
    def handler(request):
        payload = json.loads(request.content); sent.append(payload)
        if len(sent) == 1:
            return httpx.Response(400, json={'error': {'param': 'reasoning.summary', 'message': 'summary unsupported'}})
        return httpx.Response(200, json={'status': 'completed', 'model': 'synthetic-model', 'usage': {'input_tokens': 10, 'output_tokens': 1}, 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'synthetic'}]}]})
    # The normal payload for models supporting reasoning summaries is reused.
    with patch.object(llm, 'model_reasoning_config', return_value={'parameter': 'reasoning_effort'}):
        call(handler, selected=profile('responses'))
    assert len(sent) == 2 and 'summary' not in sent[1]['reasoning']
    total = metrics.usage_summary('2000', runtime.MODES)['totals']
    assert total['calls'] == 2 and total['retries'] == 1 and total['prompt_tokens'] == 10


def images():
    return [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://synthetic.invalid/old.png'}}]},
            {'role': 'assistant', 'content': 'old image received'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'latest'}, {'type': 'image_url', 'image_url': {'url': 'https://synthetic.invalid/latest.png'}}]}]


def test_image_overflow_repairs_once_persists_choice_and_keeps_latest_original():
    sent = []
    def handler(request):
        payload = json.loads(request.content); sent.append(payload)
        if len(sent) == 1:
            return httpx.Response(400, json={'error': {'code': 'context_length_exceeded', 'message': 'image token budget exceeded'}})
        assert payload['messages'][0]['content'][0]['type'] == 'text'
        assert '未发送给模型' in payload['messages'][0]['content'][0]['text']
        assert payload['messages'][-1]['content'][-1]['type'] == 'image_url'
        return response({'prompt_tokens': 10})
    call(handler, messages=images())
    assert len(sent) == 2
    # Same conversation/request scope reuses the durable decision on next input.
    replay = []
    call(lambda request: (replay.append(json.loads(request.content)) or response()), messages=images())
    assert replay[0]['messages'][0]['content'][0]['type'] == 'text'
    with closing(db.get_conn()) as conn:
        row = dict(conn.execute('SELECT * FROM context_input_offloads').fetchone())
    stored = offloads.read(row['id'], row['scope'])
    assert stored['original'] == images()[0]['content'][0]
    with pytest.raises(ValueError):
        offloads.read(row['id'], 'different-scope')


def test_no_progress_overflow_does_not_retry_or_change_model_selection():
    sent = []
    def handler(request):
        sent.append(request)
        return httpx.Response(400, json={'error': {'code': 'context_length_exceeded', 'message': 'maximum context window exceeded'}})
    with pytest.raises(llm.ModelRequestError):
        call(handler, retry_attempts=3)
    assert len(sent) == 1


def test_repaired_overflow_is_limited_to_one_retry():
    sent = []
    def handler(request):
        sent.append(request)
        return httpx.Response(400, json={'error': {'code': 'context_length_exceeded', 'message': 'image token budget exceeded'}})
    with pytest.raises(llm.ModelRequestError):
        call(handler, messages=images())
    assert len(sent) == 2


def test_preflight_preserves_expected_budget_but_checks_real_window_and_output():
    sent = []
    with pytest.raises(llm.ModelRequestError, match='真实窗口'):
        call(lambda request: (sent.append(request) or response()), messages=[{'role': 'user', 'content': '中'*10000}], selected=profile(window=4096))
    assert sent == []
    summary = metrics.usage_summary('2000', runtime.MODES)
    assert summary['totals']['calls'] == 0 and summary['not_sent_calls'] == 1
    budget = policy.route_budget(profile(window=32768))
    assert budget['configured_max_tokens'] == 272000
    assert budget['effective_input_tokens'] + budget['output_reserved_tokens'] == 32768


def test_deterministic_tool_pruning_retains_errors_pairs_and_durable_original():
    content = json.dumps({'status': 'failed', 'error': 'synthetic failure', 'body': 'x'*40000})
    source = [{'role': 'assistant', 'tool_calls': [{'id': 'call-1'}]}, {'role': 'tool', 'tool_call_id': 'call-1', 'content': content}, {'role': 'assistant', 'content': 'later'}, {'role': 'user', 'content': 'latest'}]
    output, changes = policy.prune_messages(source, scope='synthetic-task')
    assert len(output[1]['content']) < len(content) and output[1]['tool_call_id'] == 'call-1'
    assert json.loads(output[1]['content'])['preserved_result_fields']['error'] == 'synthetic failure'
    assert offloads.read(changes[0]['reference'], 'synthetic-task')['original'] == content
    assert source[1]['content'] == content


def test_same_prefix_summary_is_opt_in_same_model_bounded_and_disables_tools():
    original = [{'role': 'system', 'content': 'persona'}, {'role': 'user', 'content': 'old'}]
    independent = [{'role': 'system', 'content': 'summarize'}, {'role': 'user', 'content': 'facts'}]
    tools = [{'type': 'function', 'function': {'name': 'synthetic'}}]
    result = policy.summary_request(independent, original_messages=original, original_tools=tools, profile=profile(), original_model_id='synthetic')
    assert result[0] is independent and result[3] == 'independent'
    result = policy.summary_request(independent, original_messages=original, original_tools=tools, profile=profile(), original_model_id='synthetic', strategy='same_prefix')
    assert result[0][:-1] == original and result[1] == tools and result[2] == 'none'
    assert policy.summary_request(independent, original_messages=original, profile=profile(), original_model_id='different', strategy='same_prefix')[3] == 'independent'
    routes = [llm.CompletionRoute('https://a.invalid', '', 'a'), llm.CompletionRoute('https://b.invalid', '', 'b')]
    with policy.summary_route(profile(), 'same_prefix'):
        assert policy.restrict_summary_routes(profile(), routes) == routes[:1]
    assert policy.restrict_summary_routes(profile(), routes) == routes


def test_placeholder_window_remains_unknown_and_catalog_capacity_is_explicit():
    from app import model_registry, model_provider_service
    unknown = ModelProfile(id='unknown', provider_name='Synthetic', display_name='Synthetic', model='synthetic', base_urls=(), api_key='')
    budget = policy.route_budget(unknown)
    assert budget['model_window_tokens'] is None and budget['effective_input_tokens'] == 272000
    assert model_registry.context_window_source({'context_window_tokens': 32768}) == 'unverified_default'
    assert model_registry.context_window_source({'context_window_tokens': 64000}) == 'configured'
    assert model_provider_service._catalog_context_window({'id': 'large-name'})['context_window_source'] == 'unverified_default'
    assert model_provider_service._catalog_context_window({'context_length': 128000}) == {'context_window_tokens': 128000, 'context_window_source': 'provider_catalog'}


def test_summary_strategy_is_saved_and_rejects_unknown_values():
    from app.config import settings, save_runtime_settings, runtime_settings_snapshot
    assert settings.chat_summary_strategy == settings.agent_summary_strategy == 'independent'
    save_runtime_settings({'chat_summary_strategy': 'same_prefix', 'agent_summary_strategy': 'same_prefix'})
    assert runtime_settings_snapshot()['settings']['chat_summary_strategy'] == 'same_prefix'
    assert settings.agent_summary_strategy == 'same_prefix'
    with pytest.raises(ValueError):
        save_runtime_settings({'chat_summary_strategy': 'unsafe'})


def test_deleted_conversation_cannot_recreate_offloaded_bytes():
    identifier = offloads.save('desktop_deleted', 'synthetic original', 'tool_result')
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT INTO deleted_conversations VALUES(?)', ('desktop_deleted',))
    offloads.delete_scope('desktop_deleted')
    assert offloads.save('desktop_deleted', 'late original', 'tool_result') is None
    assert offloads.find('desktop_deleted', 'synthetic original', 'tool_result') is None
    assert offloads.list_scope('desktop_deleted') == []
    assert identifier


def test_deleted_task_scope_cannot_be_created_by_a_late_request():
    assert offloads.save('task_missing', 'synthetic original', 'tool_result') is None
    assert offloads.list_scope('task_missing') == []
