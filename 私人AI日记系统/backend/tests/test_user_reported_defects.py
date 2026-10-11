import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import db,workspace_service as workspace,code_workspace_service as code,desktop_control_service as desktop
from app import conversation_state_service as sidebar,conversation_service,llm
from app.tool_registry import tool_registry
from app import tool_discovery
from app.routes import workspace as workspace_routes,conversations,models
from test_runtime_safety_v2 import isolated
from test_agent_live_progress import transport_context,GatedStream,encoded

def test_real_folder_creation_and_reload_inherits_default_capabilities(tmp_path):
    folder=tmp_path/'new-project'
    item=workspace.save_project('真实文件夹',directory=str(folder),create_directory=True)
    assert folder.is_dir() and Path(item['directory'])==folder
    assert workspace.projects()[0]['directory']==str(folder)
    assert code.configuration(item['id'])['root']==str(folder)
    assert code.configuration(item['id'])['write_enabled']
    assert code.configuration(item['id'])['permission_source']=='global'
    state=code.configure(item['id'],str(folder),True,True,True,{},0)
    assert code.configuration(item['id'])['execute_enabled'] and state['revision']==1
    desktop.configure(item['id'],enabled=True)
    assert desktop.configuration(item['id'])['enabled']
    desktop.configure(item['id'],enabled=False,expected_revision=1)
    assert not desktop.configuration(item['id'])['enabled']

def test_changing_code_root_cannot_transfer_previous_authorization(tmp_path):
    first=tmp_path/'first';first.mkdir()
    second=tmp_path/'second';second.mkdir()
    item=workspace.save_project('目录授权隔离',directory=str(first))
    authorized=code.configure(item['id'],str(first),True,True,True,{},0)
    with pytest.raises(ValueError,match='不能沿用旧目录授权'):
        code.configure(item['id'],str(second),True,True,True,{},authorized['revision'])
    assert code.configuration(item['id'])==authorized
    moved=code.configure(item['id'],str(second),False,False,False,{},authorized['revision'])
    assert moved['root']==str(second)
    assert not moved['read_enabled'] and not moved['write_enabled'] and not moved['execute_enabled']
    reauthorized=code.configure(item['id'],str(second),True,False,False,{},moved['revision'])
    assert reauthorized['read_enabled'] and not reauthorized['write_enabled']

def test_existing_folder_is_bound_and_invalid_parent_never_creates_metadata(tmp_path):
    folder=tmp_path/'existing';folder.mkdir()
    item=workspace.save_project('已存在',directory=str(folder))
    assert Path(item['directory'])==folder
    with pytest.raises((ValueError,OSError)): workspace.save_project('不存在',directory=str(tmp_path/'absent'/'child'),create_directory=True)
    assert len(workspace.projects())==1

def test_project_api_creates_folder_and_old_project_migrates(tmp_path):
    old=workspace.save_project('旧项目');assert old['directory']==''
    app=FastAPI();app.include_router(workspace_routes.router)
    with TestClient(app) as c:
        response=c.post('/api/workspace/projects',json={'name':'新项目','directory':str(tmp_path/'api-folder'),'create_directory':True})
    assert response.status_code==200 and (tmp_path/'api-folder').is_dir()

def test_sidebar_state_and_fork_attachments_survive_source_delete():
    source='desktop_agent_fork_source';db.create_agent_conversation(source,'原对话')
    attachment=conversation_service.archive_attachment('example.txt',b'owned by branch','text/plain','text')
    db.save_message('user','原始问题',conversation_id=source,attachments_json=json.dumps([attachment,{'kind':'citation_manifest'}]))
    sidebar.update(source,pinned=True,unread=True,archived=True)
    listed=next(i for i in conversation_service.list_conversations() if i['id']==source)
    assert listed['pinned'] and listed['unread'] and listed['archived']
    branch=sidebar.fork(source)
    row=db.get_recent_messages(conversation_id=branch['id'])[0]
    cloned=json.loads(row['attachments_json']);assert len(cloned)==1 and cloned[0]['url']!=attachment['url']
    from app.config import settings
    copied=settings.agent_attachment_dir/cloned[0]['url'].removeprefix('/agent-files/')
    asyncio.run(conversations.delete_conversation(source))
    assert copied.read_bytes()==b'owned by branch' and sidebar.markdown(branch['id']).endswith('原始问题')

def test_discovery_keeps_all_capabilities_available_but_reduces_first_schema_payload():
    names={d.name for d in tool_registry.list()}
    selected=tool_discovery.exposed(names,[])
    full=sum(len(json.dumps(d.native_schema())) for d in tool_registry.list())
    small=sum(len(json.dumps(tool_registry.require(n).native_schema())) for n in selected)
    assert small < full*.3 and 'code_apply_patch' in tool_discovery.catalog(names)
    expanded=tool_discovery.exposed(names,[{'tool_name':'agent_discover_tools','status':'completed','result':{'enabled_tools':['code_apply_patch','terminal_start']}}])
    assert {'code_apply_patch','terminal_start'}<=expanded

def test_discovery_respects_explicit_tool_authorization():
    with patch('app.agent_task_service.get',return_value={'allowed_tools':['get_self_state']}):
        value=tool_discovery.discover(['get_self_state','code_apply_patch'],SimpleNamespace(task_id='t'))
    assert value['enabled_tools']==['get_self_state'] and value['unavailable_tools']==['code_apply_patch']


def test_legacy_task_without_discovery_retains_original_tool_schemas():
    names={d.name for d in tool_registry.list()}-{'agent_discover_tools'}
    assert tool_discovery.exposed(names,[])==names


def test_planner_cancel_closes_and_preserves_analysis():
    from app import agent_loop_service as loop
    db.create_agent_run('cancel-analysis','cancel-analysis-request',conversation_id='desktop_agent_cancel')
    async def interrupted(*args,**kwargs):
        await kwargs['on_reasoning']('已开始检查。','已开始检查。')
        raise asyncio.CancelledError()
    async def run():
        with patch.object(llm,'call_chat_completion_stream_result',side_effect=interrupted):
            with pytest.raises(asyncio.CancelledError):
                await loop._plan([],model_id='synthetic',reasoning_level='low',request_id='r',allow_native=True,run_id='cancel-analysis')
    asyncio.run(run())
    rows=db.list_agent_run_events('cancel-analysis')
    assert any(r['event_type']=='analysis_text_delta' and '已开始检查' in r['payload_json'] for r in rows)
    assert any(r['event_type']=='analysis_stream_finished' and 'cancelled' in r['payload_json'] for r in rows)


def test_nonstream_responses_summary_is_used_when_no_delta_is_sent():
    async def run():
        data={'type':'response.completed','response':{'model':'synthetic','output':[
            {'type':'reasoning','summary':[{'type':'summary_text','text':'检查完成。'}],'encrypted_content':'opaque-not-displayable'},
            {'type':'message','content':[{'type':'output_text','text':'结果'}]}]}}
        received=[]
        with transport_context('responses',lambda request:httpx.Response(200,stream=GatedStream([encoded(data)]))):
            result=await llm.call_chat_completion_stream_result.__wrapped__([{'role':'user','content':'检查'}],model_id='synthetic',on_reasoning=lambda text,total:received.append(text))
        assert result.reasoning_text=='检查完成。' and received==['检查完成。']
        assert result.content=='结果' and 'opaque' not in result.reasoning_text
    asyncio.run(run())


def test_summary_rejection_falls_back_once_without_changing_effort_or_tools():
    async def run():
        requests=[]
        def handler(request):
            payload=json.loads(request.content);requests.append(payload)
            if len(requests)==1: return httpx.Response(400,json={'error':{'param':'reasoning.summary','message':'unsupported summary'}})
            return httpx.Response(200,stream=GatedStream([encoded({'type':'response.completed','response':{'output':[{'type':'message','content':[{'type':'output_text','text':'正常回复'}]}]}})]))
        payload={'model':'gpt-5','reasoning':{'effort':'high','summary':'auto'},'tools':[{'type':'function','name':'read'}]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with llm._completion_stream(client,'POST','https://example.invalid/responses',headers={},json=payload) as response:
                assert response.status_code==200
                await response.aread()
        assert len(requests)==2 and requests[1]['reasoning']=={'effort':'high'}
        assert requests[0]['tools']==requests[1]['tools'] and payload['reasoning']['summary']=='auto'
    asyncio.run(run())

def test_native_stream_reasoning_arrives_before_fragmented_tool_call_completes():
    async def run():
        chunks=[encoded({'choices':[{'delta':{'reasoning_content':'先检查当前权限。'}}]}),
          encoded({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_a','function':{'name':'execution_capabilities','arguments':'{'}}]}}]}),
          encoded({'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'}'}}]},'finish_reason':'tool_calls'}], 'usage':{'prompt_tokens':21,'completion_tokens':8}})+'data: [DONE]\n\n']
        received=[]
        def handler(request):
            assert json.loads(request.content)['tools'][0]['function']['name']=='execution_capabilities'
            return httpx.Response(200,stream=GatedStream(chunks))
        with transport_context('chat_completions',handler):
            result=await llm.call_chat_completion_stream_result.__wrapped__([{'role':'user','content':'检查权限'}],
                model_id='synthetic',tools=[tool_registry.require('execution_capabilities').native_schema()],
                on_reasoning=lambda piece,total:received.append(piece))
        assert received==['先检查当前权限。'] and result.tool_calls[0].arguments_json=='{}'
        assert result.protocol_message['tool_calls'][0]['id']=='call_a' and result.prompt_tokens==21
    asyncio.run(run())

def test_responses_stream_summary_and_native_function_receipt():
    async def run():
        chunks=[encoded({'type':'response.reasoning_summary_text.delta','delta':'检查来源。'}),
          encoded({'type':'response.completed','response':{'model':'synthetic','output':[{'type':'function_call','id':'fc','call_id':'c','name':'execution_capabilities','arguments':'{}'}],'usage':{'input_tokens':10,'output_tokens':5}}})]
        def handler(request):return httpx.Response(200,stream=GatedStream(chunks))
        with transport_context('responses',handler):
            result=await llm.call_chat_completion_stream_result.__wrapped__([{'role':'user','content':'检查'}],model_id='synthetic',tools=[tool_registry.require('execution_capabilities').native_schema()])
        assert result.reasoning_text=='检查来源。' and result.tool_calls[0].name=='execution_capabilities'
    asyncio.run(run())
