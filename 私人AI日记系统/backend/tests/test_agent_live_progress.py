"""Durable amendments, approval boundaries and resumable streams without real accounts."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, llm, agent_loop_service as loop, agent_input_service as inputs
from app import agent_task_service as tasks, workspace_service as workspace
from app import agent_progress_service as progress, browser_service as browser
from app.agent_result_context import AgentCheckpointError
from app.agent_tool_service import ToolExecutionContext, execute_tool_call
from app.companion_action_service import approve_companion_action
from app.model_registry import ModelProfile
from app.routes.agent_tasks import router
from test_runtime_safety_v2 import isolated


def make_task(run='live-run', conversation='live-conversation', task_id=''):
    db.create_agent_run(run, run+'-request', conversation_id=conversation, model_id='synthetic')
    return tasks.prepare(run_id=run, request_id=run+'-request', conversation_id=conversation,
        source='desktop', user_message='完成合成目标', model_id='synthetic', reasoning_level='low',
        allowed_tools=[], context=[], task_id=task_id)


def completion(content='', calls=()):
    return llm.CompletionResult(content=content, model='synthetic', prompt_tokens=3,
        cached_prompt_tokens=0, completion_tokens=2, reasoning_tokens=0,
        cost_yuan=.001, cost_source='synthetic', tool_calls=calls)


def agent(task, status='completed'):
    return loop.AgentLoopResult('live-run',status,'native',(),(),False,1,task_id=task['id'])


def test_amendments_are_idempotent_full_and_task_scoped():
    t=make_task(); other=make_task('other','other-conversation')
    for i in range(34): inputs.append(t['id'],f'{i}:'+('正文'*3000),str(i))
    inputs.initialize()  # reopening the service does not rely on a process queue
    assert inputs.append(t['id'],'0:'+('正文'*3000),'0')['version']==1
    with pytest.raises(ValueError): inputs.append(t['id'],'changed','0')
    assert inputs.read(other['id'])['inputs']==[]
    found=[]; cursor=0
    while True:
        page=inputs.read(t['id'],after_version=cursor,max_chars=12000)
        found+=page['inputs']; cursor=page['next_version']
        if not page['has_more']: break
    assert len(found)==34 and found[0]['content'].endswith('正文'*3000)
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE delivery_key LIKE 'task-input:%'").fetchone()[0]==34
    with pytest.raises(ValueError): inputs.consume(t['id'],found,'other',0)
    inputs.consume(t['id'],found[:2],'live-run',2)
    assert inputs.read(t['id'],pending_only=True)['inputs'][0]['version']==3
    tasks.update(t['id'],status='cancelled')
    with pytest.raises(ValueError): inputs.append(t['id'],'cancelled','last')


def test_running_write_is_not_cancelled_by_followup():
    async def run():
        t=make_task(); started=asyncio.Event(); release=asyncio.Event(); effects=[]
        async def write():
            tasks.bind(t['id']); started.set(); await release.wait(); effects.append('once'); tasks.unbind(t['id'])
        process=asyncio.create_task(write()); await started.wait()
        updated=await workspace.follow_up(t['id'],'改用新的标题','amendment')
        assert updated['status']=='running' and not process.cancelled() and inputs.has_pending(t['id'])
        release.set(); await process; assert effects==['once']
    asyncio.run(run())


def test_new_input_expires_pending_confirmation_and_replans():
    async def run():
        t=make_task()
        context=ToolExecutionContext(run_id='live-run',request_id='live-run-request',trace_id='live',
            conversation_id='live-conversation',source_message_id=0,user_message='synthetic',
            step_index=1,task_id=t['id'])
        # This test exercises approval invalidation, not provider configuration.
        # It must run on a clean machine without borrowing a user's API key.
        with patch('app.agent_tool_service.require_configured'):
            result=await execute_tool_call('edit_today_diary',{'instruction':'待确认的正文'},context)
        assert result.status=='needs_confirmation'
        tasks.update(t['id'],status='waiting_confirmation')
        browser.initialize()
        with db.get_conn() as conn:
            conn.execute('INSERT INTO workspace_browser_pending VALUES(?,?,?,?,?,?,?,?,?)',
                ('pending',t['id'],'page','https://example.invalid','hash','{}','pending',db.now_iso(),'{}'))
        updated=await workspace.follow_up(t['id'],'不要删除，只查看','changed')
        assert updated['status']=='ready'
        assert db.get_companion_action(result.action_id)['status']=='cancelled'
        assert db.get_agent_run_step(result.step_id)['status']=='cancelled'
        with pytest.raises(ValueError): await approve_companion_action(result.action_id)
        with db.get_conn() as conn:
            assert conn.execute("SELECT status FROM workspace_browser_pending WHERE id='pending'").fetchone()[0]=='expired'
    asyncio.run(run())


def test_unknown_browser_write_blocks_restart_but_preserves_input():
    async def run():
        t=make_task(); tasks.update(t['id'],status='waiting_user'); browser.initialize()
        with db.get_conn() as conn:
            conn.execute('INSERT INTO workspace_browser_pending VALUES(?,?,?,?,?,?,?,?,?)',
                ('unknown',t['id'],'page','https://example.invalid','hash','{}','unknown',db.now_iso(),'{}'))
        updated=await workspace.follow_up(t['id'],'先检查提交结果','new')
        assert updated['status']=='paused' and '浏览器' in updated['snapshot']['blocker']
        assert inputs.read(t['id'])['inputs'][0]['content']=='先检查提交结果'
    asyncio.run(run())


def test_reply_time_input_is_resumed_and_duplicate_retry_does_not_restart():
    async def run():
        t=make_task(); a=agent(t); step=loop.begin_final_response(a)
        async def custom(messages,**kwargs):
            await workspace.follow_up(t['id'],'追加检查','new')
            return completion('已完成原目标')
        with patch.object(llm,'call_chat_completion_stream_result',AsyncMock(side_effect=AssertionError('injected callback must be respected'))):
            await loop.final_model_call(a,custom,[],model_id='synthetic')
        loop.finish_final_response(a,step,reply='已完成原目标')
        assert tasks.get(t['id'])['status']=='ready'
        inputs.consume(t['id'],inputs.read(t['id'])['inputs'],'live-run',1)
        tasks.update(t['id'],status='completed')
        retry=await workspace.follow_up(t['id'],'追加检查','new')
        assert retry['status']=='completed'
    asyncio.run(run())


def test_recorder_initial_failure_pauses_before_provider_call():
    async def run():
        t=make_task(); callback=AsyncMock()
        with patch.object(db,'append_agent_run_event',side_effect=OSError('disk full')):
            with pytest.raises(AgentCheckpointError): await loop.final_model_call(agent(t),callback,[],model_id='synthetic')
        assert tasks.get(t['id'])['status']=='paused'
        callback.assert_not_awaited()
    asyncio.run(run())


def test_deferred_commit_emits_complete_text_and_resumes_amendment():
    t=make_task(); a=agent(t); step=asyncio.run(_begin(a)); text='完整正文'*6000
    source=db.save_message('user','原始目标',source='desktop',conversation_id='live-conversation',request_id='source')
    loop.defer_final_response(a,step,reply=text)
    inputs.append(t['id'],'继续检查','new')
    loop.commit_deferred_final_response('live-run',source)
    loop.commit_deferred_final_response('live-run',source)
    rows=db.list_agent_task_history(t['id'],kind='events',limit=100)
    finals=[r for r in rows if r['event_type']=='final_response_completed']
    assert len(finals)==1 and json.loads(finals[0]['payload_json'])['reply']==text
    assert tasks.get(t['id'])['status']=='ready'


async def _begin(a): return loop.begin_final_response(a)


def test_input_during_planning_defers_old_write_and_provides_full_new_input():
    async def run():
        calls=[]; amendment='更正要求:'+('完整新正文'*1800)
        async def planner(messages,**kwargs):
            calls.append(messages)
            if len(calls)==1:
                t=tasks.list_tasks('plan-live')[0]
                await workspace.follow_up(t['id'],amendment,'late')
                return completion(calls=(llm.ToolCall('old','add_diary_material','{"content":"obsolete"}'),))
            return completion()
        with patch.object(loop,'call_chat_completion_result',new=planner), patch.object(loop,'execute_tool_call',AsyncMock(side_effect=AssertionError('obsolete write'))):
            result=await loop.run_agent_loop(conversation_id='plan-live',source='desktop',user_message='目标',
                source_message_id=0,request_id='planning',trace_id='planning',model_id='synthetic',
                reasoning_level='low',allowed_tool_names={'add_diary_material'})
        assert len(calls)==2 and amendment in calls[1][-1]['content']
        page=inputs.read(result.task_id)
        assert page['inputs'][0]['consumed_round']==1 and not inputs.has_pending(result.task_id)
    asyncio.run(run())


def test_sse_cursor_cross_run_corrupt_event_and_disconnect_are_read_only():
    t=make_task(); make_task('next','live-conversation',t['id'])
    first=db.append_agent_run_event('live-run','a','response_text_delta','{"text":"一"}')
    db.append_agent_run_event('next','b','legacy','bad-json')
    tasks.update(t['id'],status='completed')
    app=FastAPI(); app.include_router(router,prefix='/api/agent')
    with TestClient(app) as client:
        response=client.get(f"/api/agent/work/{t['id']}/stream")
        assert response.status_code==200 and 'bad-json' not in response.text and 'record_error' in response.text
        assert 'event: idle' in response.text and 'record_error' in response.text
        assert client.get(f"/api/agent/work/{t['id']}/stream",headers={'Last-Event-ID':'bad'}).status_code==400
    class Disconnected:
        async def is_disconnected(self): return True
    async def collect(): return [x async for x in progress.stream(t['id'],Disconnected())]
    assert asyncio.run(collect())==['retry: 2000\n\n']
    assert tasks.get(t['id'])['status']=='completed'


def test_cancelled_feature_routes_and_mobile_listener_are_not_registered():
    from app.main import BACKGROUND_TASK_FACTORIES, create_app
    paths = {route.path for route in create_app().routes}
    assert not any(any(term in path for term in ('/music', '/stickers', '/moments', '/api/mobile', '/mobile/')) for path in paths)
    assert 'mobile_gateway_worker' not in BACKGROUND_TASK_FACTORIES
    # The similarly named worker delivers QQ/WeChat task receipts and remains supported.
    assert 'mobile_task_delivery' in BACKGROUND_TASK_FACTORIES
    assert '/api/workspace' in paths


class GatedStream(httpx.AsyncByteStream):
    def __init__(self,chunks,gate=None,fail=False): self.chunks=chunks; self.gate=gate; self.fail=fail
    async def __aiter__(self):
        for index,chunk in enumerate(self.chunks):
            if index and self.gate: await self.gate.wait()
            yield chunk.encode()
        if self.fail: raise httpx.ReadError('synthetic disconnect')


def encoded(value): return 'data: '+json.dumps(value,ensure_ascii=False)+'\n\n'


def transport_context(mode,handler):
    from contextlib import ExitStack
    profile=ModelProfile(id='synthetic',provider_name='Synthetic',display_name='Synthetic',
        model='synthetic',base_urls=('https://invalid.example/v1',),api_key='synthetic',api_mode=mode)
    constructor=httpx.AsyncClient; stack=ExitStack()
    stack.enter_context(patch.object(llm,'require_configured'))
    stack.enter_context(patch.object(llm,'resolve_model_id',return_value='synthetic'))
    stack.enter_context(patch.object(llm,'get_model_profile',return_value=profile))
    stack.enter_context(patch.object(llm,'_completion_routes',return_value=[llm.CompletionRoute('https://invalid.example/v1','','synthetic',api_mode=mode)]))
    stack.enter_context(patch.object(llm.httpx,'AsyncClient',side_effect=lambda **kwargs:constructor(transport=httpx.MockTransport(handler))))
    return stack


@pytest.mark.parametrize('mode',['chat_completions','responses'])
def test_real_sse_first_chunk_before_complete_and_usage(mode):
    async def run():
        gate=asyncio.Event(); arrived=asyncio.Event(); deltas=[]; requests=[]
        if mode=='responses':
            chunks=[encoded({'type':'response.output_text.delta','delta':'你好'}),
                encoded({'type':'response.completed','response':{'model':'provider','output':[{'type':'message','content':[{'type':'output_text','text':'你好世界'}]}],
                    'usage':{'input_tokens':12,'output_tokens':7,'output_tokens_details':{'reasoning_tokens':2}}}})]
        else:
            chunks=[encoded({'choices':[{'delta':{'content':'你好'}}]}),encoded({'choices':[{'delta':{'content':'世界'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':12,'completion_tokens':7,'completion_tokens_details':{'reasoning_tokens':2}}})+'data: [DONE]\n\n']
        def handle(request):
            requests.append(request); assert json.loads(request.content)['stream'] is True
            return httpx.Response(200,stream=GatedStream(chunks,gate),headers={'content-type':'text/event-stream'})
        def delta(piece,total):
            deltas.append(piece); arrived.set()
            future=asyncio.get_running_loop().create_future(); future.set_result(None); return future
        with transport_context(mode,handle):
            process=asyncio.create_task(llm.call_chat_completion_stream_result.__wrapped__([],on_delta=delta,model_id='synthetic'))
            await asyncio.wait_for(arrived.wait(),2); assert not process.done() and deltas==['你好']
            gate.set(); result=await process
        assert result.content=='你好世界' and result.prompt_tokens==12 and result.completion_tokens==7 and result.reasoning_tokens==2
        assert len(requests)==1
    asyncio.run(run())


@pytest.mark.parametrize('kind',['eof','transport','length','provider_failure'])
def test_partial_stream_never_retries_or_claims_completion(kind):
    count=0; deltas=[]
    chunks=[encoded({'choices':[{'delta':{'content':'partial'}}]})]
    if kind=='length': chunks.append(encoded({'choices':[{'delta':{},'finish_reason':'length'}]}))
    if kind=='provider_failure': chunks.append(encoded({'error':{'message':'failed'}}))
    def handle(request):
        nonlocal count
        count+=1; return httpx.Response(200,stream=GatedStream(chunks,fail=kind=='transport'))
    with transport_context('chat_completions',handle), pytest.raises(llm.ModelRequestError):
        asyncio.run(llm.call_chat_completion_stream_result.__wrapped__([],model_id='synthetic',retry_attempts=3,on_delta=lambda p,t:deltas.append(p)))
    assert count==1 and deltas==['partial']


def test_explicit_unsupported_stream_falls_back_once():
    def handle(request): return httpx.Response(400,json={'error':{'param':'stream','message':'unsupported'}})
    fallback=AsyncMock(return_value=completion('一次完整结果')); deltas=[]
    with transport_context('chat_completions',handle),patch.object(llm,'call_chat_completion_result',fallback):
        result=asyncio.run(llm.call_chat_completion_stream_result.__wrapped__([],model_id='synthetic',request_id='stable',on_delta=lambda p,t:deltas.append(p)))
    assert result.content=='一次完整结果' and deltas==['一次完整结果']
    fallback.assert_awaited_once(); assert fallback.call_args.kwargs['request_id']=='stable'
