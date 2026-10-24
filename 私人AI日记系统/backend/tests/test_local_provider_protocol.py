"""Real loopback HTTP fixtures; no installed model, credentials or user data."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import llm, model_registry
from app.config import settings
from app.provider_compat import auth_headers
from app.routes import models


@pytest.fixture
def local_server(monkeypatch, tmp_path):
    requests = []
    state = {'reply': lambda payload: (200, 'text/event-stream', sse(
        {'choices': [{'delta': {'content': '本地回复'}, 'finish_reason': None}]},
        {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}, done=True))}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(('GET', self.path, dict(self.headers), None))
            self.respond(200, 'application/json', json.dumps({'data': [{'id': 'qwen/qwen3.5-9b'}]}))

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(('POST', self.path, dict(self.headers), payload))
            self.respond(*state['reply'](payload))

        def respond(self, status, content_type, body):
            data = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    changes = {'model_profiles_path': tmp_path / 'models.json', 'openai_proxy_mode': 'proxy',
        'openai_proxy_url': 'http://127.0.0.1:1', 'openai_timeout_seconds': 5}
    original = {key: getattr(settings, key) for key in changes}
    for key, value in changes.items():
        object.__setattr__(settings, key, value)
    base_url = f'http://127.0.0.1:{server.server_port}/v1'
    try:
        yield base_url, state, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        for key, value in original.items():
            object.__setattr__(settings, key, value)


def sse(*events, done=False):
    return ''.join('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n' for event in events) + ('data: [DONE]\n\n' if done else '')


def save_local(base_url):
    return asyncio.run(models.create_provider(models.ProviderCreateRequest(
        provider_name='LM Studio fixture', provider_kind='local', base_url=base_url,
        models=[models.ProviderModelRequest(model='qwen/qwen3.5-9b')],
    )))['models'][0]


def stream(profile_id, messages=None, **kwargs):
    return asyncio.run(llm.call_chat_completion_stream_result.__wrapped__(
        messages or [{'role': 'user', 'content': 'fixture request'}], model_id=profile_id,
        retry_attempts=1, **kwargs))


def test_local_discovery_creation_and_stream_bypass_proxy_without_fake_key(local_server):
    base, state, requests = local_server
    discovered = asyncio.run(models.discover_models(models.ModelDiscoveryRequest(
        provider_kind='local', base_url=base, preset_id='lm_studio')))
    assert discovered['models'][0]['model'] == 'qwen/qwen3.5-9b'
    assert discovered['resolved_api_base_url'] == base  # custom LM Studio port preserved
    saved = save_local(base)
    assert saved['supports_tool_calls'] is True
    assert saved['privacy_location'] == 'local_device'
    assert saved['api_key_configured'] is False
    llm.require_configured(saved['id'])
    result = stream(saved['id'])
    assert result.content == '本地回复'
    assert all('Authorization' not in request[2] for request in requests)
    assert auth_headers('') == {}


def test_explicit_stream_options_rejection_retries_only_that_option_and_keeps_tools(local_server):
    base, state, requests = local_server
    tools = [{'type': 'function', 'function': {'name': 'fixture_echo', 'parameters': {'type': 'object'}}}]

    def reply(payload):
        if 'stream_options' in payload:
            return 400, 'application/json', json.dumps({'error': {'param': 'stream_options', 'message': 'unsupported option'}})
        if payload['messages'][-1]['role'] == 'tool':
            return 200, 'text/event-stream', sse({'choices': [{'delta': {'content': '已核对工具结果'}, 'finish_reason': 'stop'}]}, done=True)
        return 200, 'text/event-stream', sse(
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'fixture_call', 'function': {'name': 'fixture_', 'arguments': '{"value":'}}]}, 'finish_reason': None}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'name': 'echo', 'arguments': '"ok"}'}}]}, 'finish_reason': 'tool_calls'}]}, done=True)

    state['reply'] = reply
    saved = save_local(base)
    result = stream(saved['id'], tools=tools)
    assert result.tool_calls[0].name == 'fixture_echo'
    assert json.loads(result.tool_calls[0].arguments_json) == {'value': 'ok'}
    assert len(requests) == 2
    assert all(item[3]['tools'] == tools for item in requests)
    assert all(item[3]['stream'] is True for item in requests)
    followup = stream(saved['id'], messages=[{'role': 'user', 'content': 'fixture'},
        result.protocol_message, {'role': 'tool', 'tool_call_id': 'fixture_call', 'content': '{"value":"ok"}'}], tools=tools)
    assert followup.content == '已核对工具结果'
    assert followup.tool_calls == ()


def test_explicit_stream_rejection_uses_one_nonstream_request(local_server):
    base, state, requests = local_server
    def reply(payload):
        if payload.get('stream'):
            return 400, 'application/json', json.dumps({'error': {'param': 'stream', 'message': 'not supported'}})
        return 200, 'application/json', json.dumps({'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}]})
    state['reply'] = reply
    saved = save_local(base)
    # Keep the real model_call wrapper out of this transport fixture's ledger.
    from unittest.mock import patch
    with patch.object(llm, 'call_chat_completion_result', llm.call_chat_completion_result.__wrapped__):
        assert stream(saved['id']).content == 'OK'
    assert len(requests) == 2
    assert requests[1][3].get('stream') is None


@pytest.mark.parametrize('failure', ['service_error', 'length', 'missing_finish', 'invalid_tool', 'unsupported_tools'])
def test_failure_never_retries_partial_or_drops_tools(local_server, failure):
    base, state, requests = local_server
    error = {'error': {'code': 'model_generation_failed', 'message': 'fixture generation failed'}}
    if failure == 'unsupported_tools':
        state['reply'] = lambda payload: (400, 'application/json', json.dumps({'error': {'param': 'tools', 'message': 'model does not support tools'}}))
    else:
        events = [{'choices': [{'delta': {'content': 'partial'}, 'finish_reason': None}]}]
        if failure == 'service_error':
            events.append(error)
        if failure == 'length':
            events.append({'choices': [{'delta': {}, 'finish_reason': 'length'}]})
        if failure == 'invalid_tool':
            events.append({'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'name': 'fixture_echo', 'arguments': '{'}}]}, 'finish_reason': 'tool_calls'}]})
        state['reply'] = lambda payload: (200, 'text/event-stream', sse(*events, done=failure == 'invalid_tool'))
    saved = save_local(base)
    tools = [{'type': 'function', 'function': {'name': 'fixture_echo'}}]
    with pytest.raises(llm.ModelRequestError) as captured:
        stream(saved['id'], tools=tools)
    assert len(requests) == 1
    assert requests[0][3]['tools'] == tools
    if failure == 'service_error':
        assert captured.value.public_detail()['upstream_code'] == 'model_generation_failed'
        assert 'fixture generation failed' in str(captured.value)


def test_json_response_ignoring_stream_supports_pretty_printed_chat_message(local_server):
    base, state, requests = local_server
    state['reply'] = lambda payload: (200, 'application/json', json.dumps({
        'choices': [{'message': {'content': 'JSON OK'}, 'finish_reason': 'stop'}]}, indent=2))
    assert stream(save_local(base)['id']).content == 'JSON OK'


def test_remote_provider_still_requires_real_key(local_server):
    with pytest.raises(ValueError, match='API Key'):
        model_registry.save_custom_provider({'provider_name': 'remote', 'provider_kind': 'relay',
            'base_url': 'https://provider.example/v1'}, [{'model': 'fixture'}])


def test_old_loopback_relay_profile_can_remain_keyless(local_server):
    base, state, requests = local_server
    _, profiles = model_registry.save_custom_provider({'provider_name': 'legacy localhost',
        'provider_kind': 'relay', 'base_url': base}, [{'model': 'fixture'}])
    assert stream(profiles[0].id).content == '本地回复'


@pytest.mark.parametrize('fail_after_tool', [False, True])
def test_agent_loop_executes_real_read_tool_once_and_never_replans_stream_failure(local_server, tmp_path, fail_after_tool):
    from app import db
    from app.agent_loop_service import run_agent_loop
    base, state, requests = local_server
    original_db = settings.db_path
    object.__setattr__(settings, 'db_path', tmp_path / 'agent-fixture.db')
    try:
        db.init_db()
        saved = save_local(base)
        def reply(payload):
            has_tool_result = any(message.get('role') == 'tool' for message in payload['messages'])
            if has_tool_result:
                if fail_after_tool:
                    return 200, 'text/event-stream', sse({'error': {'code': 'generation_failed', 'message': 'fixture interrupted'}})
                return 200, 'text/event-stream', sse({'choices': [{'delta': {'content': '已读取合成状态'}, 'finish_reason': 'stop'}]}, done=True)
            return 200, 'text/event-stream', sse({'choices': [{'delta': {'tool_calls': [{
                'index': 0, 'id': 'fixture_read_once', 'function': {'name': 'get_today_state', 'arguments': '{}'}}]},
                'finish_reason': 'tool_calls'}]}, done=True)
        state['reply'] = reply
        message_id = db.save_message('user', '读取合成状态', conversation_id='fixture-local-agent', request_id='fixture-local-agent')
        result = asyncio.run(run_agent_loop(conversation_id='fixture-local-agent', source='desktop',
            user_message='读取合成状态', source_message_id=message_id, request_id='fixture-local-agent',
            trace_id='fixture-local-agent', model_id=saved['id'], reasoning_level='standard',
            allowed_tool_names={'get_today_state'}))
        assert len(requests) == 2
        assert all(request[3].get('stream') is True and request[3].get('tools') for request in requests)
        receipts = db.list_tool_execution_receipts(limit=10)
        assert len(receipts) == 1
        assert receipts[0]['tool_name'] == 'get_today_state'
        assert receipts[0]['status'] == 'executed'
        assert result.status == ('failed' if fail_after_tool else 'completed')
    finally:
        object.__setattr__(settings, 'db_path', original_db)


def test_configured_helper_and_auto_router_include_keyless_local_models(local_server):
    from app import auto_router
    from app.provider_compat import model_connection_configured
    from types import SimpleNamespace
    base, state, requests = local_server
    saved = save_local(base)
    assert model_connection_configured(model_registry.get_model_profile(saved['id']))
    assert saved['id'] in [profile.id for profile in auto_router._available_profiles()]
    assert not model_connection_configured(SimpleNamespace(base_urls=['https://remote.example'], api_key=''))
    assert not model_connection_configured(SimpleNamespace(base_urls=[base], api_key='', api_key_error='encrypted key unavailable'))


@pytest.mark.parametrize('finish', ['completed', 'incomplete', 'failed', 'done_only'])
def test_responses_requires_real_completion_and_keeps_failure_diagnostics(local_server, finish):
    base, state, requests = local_server
    _, profiles = model_registry.save_custom_provider({'provider_name': 'Responses fixture',
        'provider_kind': 'local', 'base_url': base, 'default_api_mode': 'responses'}, [{'model': 'fixture'}])
    events = [{'type': 'response.output_text.delta', 'delta': 'partial'}]
    if finish != 'done_only':
        events.append({'type': f'response.{finish}', 'response': {'status': finish,
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'partial'}]}],
            **({'error': {'code': 'fixture_error', 'message': 'fixture failed'}} if finish == 'failed' else {}),
            **({'incomplete_details': {'reason': 'max_output_tokens'}} if finish == 'incomplete' else {})}})
    state['reply'] = lambda payload: (200, 'text/event-stream', sse(*events, done=True))
    if finish == 'completed':
        assert stream(profiles[0].id).content == 'partial'
    else:
        with pytest.raises(llm.ModelRequestError):
            stream(profiles[0].id)
    assert len(requests) == 1
