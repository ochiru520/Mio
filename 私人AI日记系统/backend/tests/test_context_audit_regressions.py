"""Real adapter payload and attempt-ledger regressions; synthetic services only."""
from contextlib import closing
from dataclasses import replace
import json

import httpx
import pytest

from app import db, desktop_control_service as desktop
from app import context_input_policy as policy
from app.agent_loop_service import source_context_messages
from test_cache_context_contracts import call, images, profile
from test_runtime_safety_v2 import isolated


def success(api_mode, streaming=False):
    usage = {'prompt_tokens': 100, 'completion_tokens': 10, 'prompt_cache_hit_tokens': 80}
    if api_mode == 'responses':
        body = {'status': 'completed', 'model': 'synthetic-model', 'usage': usage,
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'synthetic'}]}]}
        event = {'type': 'response.completed', 'response': body}
    else:
        body = {'model': 'synthetic-model', 'choices': [{'message': {'content': 'synthetic'}}], 'usage': usage}
        event = {'choices': [{'delta': {'content': 'synthetic'}, 'finish_reason': 'stop'}], 'usage': usage}
    if streaming:
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n',
                              headers={'content-type': 'text/event-stream'})
    return httpx.Response(200, json=body)


def input_messages(payload):
    return payload.get('messages', payload.get('input', []))


@pytest.mark.parametrize('api_mode', ['chat_completions', 'responses'])
@pytest.mark.parametrize('streaming', [False, True])
def test_resent_picture_is_current_even_after_same_bytes_were_omitted(api_mode, streaming):
    sent = []
    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        if len(sent) == 1:
            return httpx.Response(400, json={'error': {'code': 'context_length_exceeded', 'message': 'image token budget exceeded'}})
        return success(api_mode, streaming)
    call(handler, messages=images(), selected=profile(api_mode), stream=streaming)
    assert input_messages(sent[-1])[0]['content'][0]['type'] == 'text'
    resent = [*images(), {'role': 'assistant', 'content': 'synthetic'},
              {'role': 'user', 'content': [{'type': 'text', 'text': 'Inspect the resent picture'}, images()[0]['content'][0]]}]
    call(handler, messages=resent, selected=profile(api_mode), stream=streaming)
    replay = input_messages(sent[-1])
    assert replay[0]['content'][0]['type'] == 'text'  # Historical occurrence stays omitted.
    assert replay[-1]['content'][-1] == images()[0]['content'][0]  # New occurrence is sent.


@pytest.mark.parametrize('api_mode', ['chat_completions', 'responses'])
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('same_prefix', [False, True])
def test_latest_source_and_window_picture_survive_agent_runtime_envelopes(api_mode, streaming, same_prefix, tmp_path, monkeypatch):
    screenshot = tmp_path / 'synthetic-observation.png'
    screenshot.write_bytes(b'synthetic-observation-only')
    selected = replace(profile(api_mode), supports_vision=True)
    monkeypatch.setattr(desktop, '_latest', {'synthetic-conversation:synthetic-task': {'observation_id': 'synthetic'}})
    monkeypatch.setattr(desktop, 'image', lambda _identifier: screenshot)
    monkeypatch.setattr('app.model_registry.get_model_profile', lambda _model: selected)
    observed = desktop.model_image_message('synthetic-conversation', 'synthetic-task', 'synthetic')
    source = source_context_messages([*images(), {'role': 'assistant', 'content': 'source acknowledgment'}])
    messages = [*source, observed, {'role': 'user', 'content': '本次规划的当前运行信息：synthetic'},
                {'role': 'user', 'content': '持久任务状态与先前观察（合成资料）'}]
    if same_prefix:
        messages, _, _, strategy = policy.summary_request(
            [{'role': 'system', 'content': 'summarize'}, {'role': 'user', 'content': 'facts'}],
            original_messages=messages, strategy='same_prefix', profile=selected, original_model_id=selected.id)
        assert strategy == 'same_prefix'
    sent = []
    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        assert '_mio_current_image' not in json.dumps(payload)
        if len(sent) == 1:
            return httpx.Response(400, json={'error': {'code': 'context_length_exceeded', 'message': 'image token budget exceeded'}})
        return success(api_mode, streaming)
    call(handler, messages=messages, selected=selected, stream=streaming)
    assert len(sent) == 2
    final = input_messages(sent[-1])
    assert final[1]['content'][0]['type'] == 'text'  # Old source picture can be relieved.
    assert final[3]['content'][-1]['type'] == 'image_url'  # Latest actual user source.
    assert final[5]['content'][-1]['type'] == 'image_url'  # Latest window observation.


@pytest.mark.parametrize('api_mode', ['chat_completions', 'responses'])
@pytest.mark.parametrize('rejected_status', [400, 422])
def test_stream_compatibility_usage_belongs_to_successful_http_attempt(api_mode, rejected_status):
    selected = replace(profile(api_mode), model='gpt-5-synthetic')
    sent = []
    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        rejected = 'stream_options' in payload if api_mode == 'chat_completions' else 'summary' in payload.get('reasoning', {})
        if rejected:
            parameter = 'stream_options' if api_mode == 'chat_completions' else 'reasoning.summary'
            return httpx.Response(rejected_status, json={'error': {'param': parameter, 'message': parameter + ' unsupported'}})
        return success(api_mode, True)
    call(handler, selected=selected, stream=True)
    assert len(sent) == 2
    with closing(db.get_conn()) as conn:
        attempts = [dict(row) for row in conn.execute('SELECT * FROM runtime_model_attempts ORDER BY attempt_index')]
    assert [(row['http_status'], row['status'], row['prompt_tokens']) for row in attempts] == [
        (rejected_status, 'failed', None), (200, 'completed', 100)]
    assert attempts[0]['cached_prompt_tokens'] is None
    assert attempts[1]['cached_prompt_tokens'] == 80
    assert attempts[1]['completion_tokens'] == 10
    assert attempts[0]['finished_at'] <= attempts[1]['started_at']
