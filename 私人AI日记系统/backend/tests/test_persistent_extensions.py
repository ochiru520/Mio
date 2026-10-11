"""Synthetic-only lifecycle, permission and process-stop regression coverage."""
import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from unittest.mock import AsyncMock

from app import extension_service as extensions, agent_skill_service as skills, agent_file_service as files
from app.extension_tools import dispatch
from app.extension_worker import validate_source
from app.routes.extensions import router
from test_runtime_safety_v2 import isolated


def tool(version='1.0.0', identifier='', prefix='处理:'):
    source='def handle(arguments):\n    return {"outputs": [{"name": "result.txt", "content": '+repr(prefix)+' + arguments["files"]["path"]}]}\n'
    manifest={'id':identifier,'name':'合成文本转换','description':'合成文件处理工具','kind':'python','version':version,
              'parameters':{'type':'object','properties':{'path':{'type':'string','minLength':1}},'required':['path'],'additionalProperties':False},
              'permissions':['file.read','artifact.write'],'file_inputs':['path']}
    tests=[{'arguments':{'path':'synthetic.txt'},'files':{'path':'合成内容'},
            'expected':{'outputs':[{'name':'result.txt','content':prefix+'合成内容'}]}}]
    return manifest,source,tests


def validated(manifest,source='',tests=None,expected_revision=0):
    item=extensions.save(manifest,source,tests,expected_revision)
    item=asyncio.run(extensions.validate(item['id'],expected_revision=item['revision']))
    assert item['validation']['valid'],item
    return item


def activate(item):
    return extensions.enabled(item['id'],True,item['revision'])


def context(monkeypatch):
    from app import agent_task_service as tasks
    ctx=SimpleNamespace(task_id='task_extension_test',conversation_id='desktop_agent_extension_test',source='agent_task')
    monkeypatch.setattr(tasks,'get',lambda identifier:{'id':identifier,'conversation_id':ctx.conversation_id,'source':'desktop','status':'running'})
    return ctx


def test_python_files_persistence_disable_and_real_rollback(isolated,tmp_path,monkeypatch):
    ctx=context(monkeypatch)
    folder=tmp_path/'authorized';folder.mkdir(); files.roots([str(folder)])
    source=folder/'source.txt';source.write_text('真实合成文件',encoding='utf-8')
    item=activate(validated(*tool()))
    result=asyncio.run(extensions.invoke(item['id'],{'path':str(source)},ctx,version='1.0.0'))
    assert result['artifacts'][0]['verified']
    assert Path(result['artifacts'][0]['path']).read_text(encoding='utf-8')=='处理:真实合成文件'
    assert source.read_text(encoding='utf-8')=='真实合成文件'
    # Module reload drops runtime objects, then revalidates the immutable bundle.
    importlib.reload(extensions)
    assert extensions.read(item['id'])['active_version']=='1.0.0'
    new=activate(validated(*tool('2.0.0',item['id'],'新版:'),expected_revision=item['revision']))
    with pytest.raises(ValueError,match='版本已变化'):
        asyncio.run(extensions.invoke(item['id'],{'path':str(source)},ctx,version='1.0.0'))
    reverted=extensions.rollback(item['id'],new['revision'])
    assert reverted['active_version']=='1.0.0'
    with pytest.raises(ValueError):
        asyncio.run(extensions.invoke(item['id'],{'path':str(source),'files':{}},ctx,version='1.0.0'))
    stopped=extensions.enabled(item['id'],False,reverted['revision'])
    with pytest.raises(ValueError,match='停用'):
        asyncio.run(extensions.invoke(item['id'],{'path':str(source)},ctx,version='1.0.0'))
    assert not stopped['enabled']


def test_bad_versions_unsupported_permissions_tamper_and_schema(isolated):
    manifest,source,tests=tool()
    invalid=extensions.save({**manifest,'api_version':999},source,tests)
    assert not invalid['validation']['valid']
    with pytest.raises(ValueError,match='不兼容'):
        extensions.enabled(invalid['id'],True,invalid['revision'])
    invalid=asyncio.run(extensions.validate(invalid['id'],expected_revision=invalid['revision']))
    assert not invalid['validation']['valid']
    for code in ['import os\ndef handle(arguments):\n    return {}',
                 'def handle(arguments):\n    return {"x": open("secret.txt").read()}',
                 'def handle(arguments):\n    return {"x": arguments.__class__}',
                 'def handle(arguments):\n    return {"x": str.format("{0.__class__}", arguments)}']:
        # Unknown builtins such as open are denied by worker execution; reflection/import at AST validation.
        try:
            validate_source(code)
        except ValueError:
            continue
        item=extensions.save({**manifest,'id':'','version':'1.0.0'},code,[{'arguments':{'path':'x'},'files':{'path':'x'},'expected':{}}])
        checked=asyncio.run(extensions.validate(item['id'],expected_revision=item['revision']))
        assert not checked['validation']['valid']
    with pytest.raises(ValueError):
        extensions.save({**manifest,'permissions':['filesystem.all']},source,tests)
    item=activate(validated(*tool()))
    bundle,row=extensions._bundle(item['id'],'1.0.0')
    path=extensions.root()/row['bundle_path'];path.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='已变化'):
        extensions.read(item['id'])
    assert not next(i for i in extensions.listing() if i['id']==item['id'])['enabled']
    stopped=extensions.enabled(item['id'],False,item['revision'])
    assert not stopped['enabled']
    for schema in ({'type':[]},{'type':'object','required':[{}]}, {'type':'object','additionalProperties':1}):
        draft=extensions.save({**manifest,'parameters':schema},source,tests)
        assert not draft['validation']['valid']


def test_damaged_latest_version_recovers_previous_active_bundle(isolated):
    item=activate(validated(*tool()))
    newer=activate(validated(*tool('2.0.0',item['id'],'新版:'),expected_revision=item['revision']))
    _,row=extensions._bundle(item['id'],'2.0.0')
    (extensions.root()/row['bundle_path']).write_text('{}',encoding='utf-8')
    restored=extensions.rollback(item['id'],newer['revision'])
    assert restored['enabled'] and restored['active_version']=='1.0.0'


def test_panel_api_conflicts_no_html_or_script_and_restart(isolated):
    app=FastAPI();app.include_router(router)
    payload={'manifest':{'name':'合成小面板','kind':'panel','version':'1.0.0','panel':{'title':'能力状态','blocks':[
        {'type':'text','text':'合成正文'},{'type':'metric','label':'数量','value':3},{'type':'table','columns':['名称'],'rows':[['合成']]}]}}}
    with TestClient(app) as client:
        saved=client.post('/api/extensions',json=payload).json();url='/api/extensions/'+saved['id']
        assert client.post(url+'/enabled',json={'enabled':True,'expected_revision':1}).status_code==400
        checked=client.post(url+'/validate',json={'expected_revision':1}).json()
        assert checked['validation']['valid']
        assert client.post(url+'/enabled',json={'enabled':True,'expected_revision':1}).status_code==409
        active=client.post(url+'/enabled',json={'enabled':True,'expected_revision':2}).json()
        assert client.get(url+'/panel').json()['panel']['title']=='能力状态'
        importlib.reload(extensions)
        assert client.get(url+'/panel').json()['version']=='1.0.0'
        assert client.post(url+'/enabled',json={'enabled':False,'expected_revision':active['revision']}).status_code==200
        assert client.get(url+'/panel').status_code==400
        for block in [{'type':'html','text':'<script>test</script>'},{'type':'text','text':'test','onclick':'do()'}]:
            bad=json.loads(json.dumps(payload));bad['manifest']['panel']['blocks']=[block]
            assert client.post('/api/extensions',json=bad).status_code==422


def test_skill_draft_activation_is_persistent_cas_and_rollback(isolated):
    ctx=SimpleNamespace()
    draft=asyncio.run(dispatch('skill_save_draft',{'name':'自然语言技能','description':'合成指导','content':'按用户目标整理资料'},ctx))
    assert len(skills.listing())==4
    importlib.reload(skills)
    saved=asyncio.run(dispatch('skill_activate',{'draft_id':draft['id']},ctx))
    assert saved['enabled'] and saved['revision']==1
    assert skills.activate_draft(draft['id'])['id']==saved['id']
    stale=skills.save_draft('自然语言技能','','待激活正文',skill_id=saved['id'],expected_revision=1)
    newer=skills.save('自然语言技能','','用户新正文',skill_id=saved['id'],expected_revision=1)
    with pytest.raises(ValueError,match='其他编辑'):
        skills.activate_draft(stale['id'])
    restored=skills.rollback(saved['id'],1,newer['revision'])
    assert restored['content']=='按用户目标整理资料' and restored['revision']==3


def test_file_grant_cannot_be_expanded_remote_sources_and_parameter_types(isolated,tmp_path,monkeypatch):
    ctx=context(monkeypatch)
    item=activate(validated(*tool()))
    secret=tmp_path/'ungranted.txt';secret.write_text('合成秘密')
    for arguments in ({'path':str(secret)},{'path':3},{'path':''},{'path':'x','extra':1}):
        with pytest.raises(ValueError):
            asyncio.run(extensions.invoke(item['id'],arguments,ctx,version='1.0.0'))
    for source,conversation in [('qq','desktop_agent_extension_test'),('agent_task','qq_group_test')]:
        remote=SimpleNamespace(task_id=ctx.task_id,conversation_id=conversation,source=source)
        with pytest.raises(ValueError,match='本机'):
            asyncio.run(extensions.invoke(item['id'],{'path':'x'},remote,version='1.0.0'))
    assert not extensions._running


def test_round_catalog_freezes_new_activation_and_revocation_before_write(isolated,tmp_path,monkeypatch):
    ctx=context(monkeypatch);ctx.run_id='run_extension_freeze';ctx.round_index=0
    folder=tmp_path/'authorized';folder.mkdir();files.roots([str(folder)])
    source=folder/'source.txt';source.write_text('合成正文',encoding='utf-8')
    extensions.freeze_round(ctx.run_id,0)
    item=activate(validated(*tool()))
    with pytest.raises(ValueError,match='本轮清单'):
        asyncio.run(extensions.invoke(item['id'],{'path':str(source)},ctx,version='1.0.0'))
    ctx.round_index=1;extensions.freeze_round(ctx.run_id,1)
    actual_worker=extensions._run_worker
    async def revoke_before_result(*args,**kwargs):
        result=await actual_worker(*args,**kwargs)
        files.roots([])
        return result
    monkeypatch.setattr(extensions,'_run_worker',revoke_before_result)
    with pytest.raises(ValueError,match='授权'):
        asyncio.run(extensions.invoke(item['id'],{'path':str(source)},ctx,version='1.0.0'))
    assert not (files.workspace()/ctx.task_id).exists()
    extensions.release_run(ctx.run_id)
    assert all(k[0]!=ctx.run_id for k in extensions._round_catalogs)


def test_running_process_stop_timeout_and_cancellation(isolated):
    bundle={'manifest':{'id':'ext_stop','timeout_seconds':10},'source':'def handle(arguments):\n    while True:\n        pass\n'}
    async def run():
        pending=asyncio.create_task(extensions._run_worker(bundle,{}))
        for _ in range(100):
            if extensions._running: break
            await asyncio.sleep(0.01)
        assert extensions._running
        processes=[entry[0] for entry in extensions._running.values()]
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert all(process.poll() is not None for process in processes)
        assert not extensions._running
        with pytest.raises(ValueError,match='超时'):
            await extensions._run_worker(bundle,{},timeout=.1)
        pending=asyncio.create_task(extensions._run_worker(bundle,{}))
        for _ in range(100):
            if extensions._running: break
            await asyncio.sleep(0.01)
        extensions.stop_all()
        with pytest.raises(ValueError,match='停止'):
            await pending
        assert not extensions._running
    asyncio.run(run())


def test_python_worker_memory_bound_and_no_undeclared_artifact_write(isolated):
    manifest,source,tests=tool()
    manifest['permissions']=['file.read']
    invalid=extensions.save(manifest,source,tests)
    checked=asyncio.run(extensions.validate(invalid['id'],expected_revision=invalid['revision']))
    assert not checked['validation']['valid'] and '权限' in checked['validation']['errors'][0]
    bundle={'manifest':{'id':'ext_memory','timeout_seconds':5},'source':'def handle(arguments):\n    return {"text": "x" * 500000000}\n'}
    with pytest.raises(ValueError):
        asyncio.run(extensions._run_worker(bundle,{}))
    assert not extensions._running


def test_console_free_file_ipc_without_standard_streams(tmp_path,monkeypatch):
    from app import extension_worker as worker
    request=tmp_path/'request.json';response=tmp_path/'response.json'
    request.write_text(json.dumps({'source':'def handle(arguments):\n    return {"value": arguments["value"] + 1}',
                                  'arguments':{'value':6}}),encoding='utf-8')
    monkeypatch.setattr(worker.sys,'argv',['Mio.exe','--extension-worker','--extension-request',str(request),'--extension-response',str(response)])
    monkeypatch.setattr(worker.sys,'stdin',None);monkeypatch.setattr(worker.sys,'stdout',None)
    worker.main()
    assert json.loads(response.read_text(encoding='utf-8'))=={'ok':True,'result':{'value':7}}


def test_mocked_model_discovers_builds_activates_and_calls_next_round(isolated,tmp_path,monkeypatch):
    from app import db,agent_loop_service as loop,execution_policy_service as policy
    from app.llm import ToolCall
    from test_agent_model_first import _completion
    conversation='desktop_agent_extension_model'
    goal='给自己添加一个文本处理工具，验证后启用并处理合成文件'
    message_id=db.save_message('user',goal,conversation_id=conversation)
    folder=tmp_path/'authorized';folder.mkdir();files.roots([str(folder)])
    # A local Agent uses the configured read scope; folder grants do not widen project scope.
    policy.configure(expected_revision=policy.configuration()['revision'],read_scope='folders')
    source=folder/'input.txt';source.write_text('模型闭环合成内容',encoding='utf-8')
    manifest,python,tests=tool(identifier='ext_model_created')
    def call(name,arguments):
        return _completion(ToolCall(name,name,json.dumps(arguments,ensure_ascii=False)))
    planner=AsyncMock(side_effect=[
        call('agent_discover_tools',{'names':['extension_save_draft','extension_validate','extension_set_enabled','extension_read','extension_call']}),
        call('extension_save_draft',{'manifest':manifest,'source':python,'tests':tests}),
        call('extension_validate',{'extension_id':manifest['id'],'expected_revision':1}),
        call('extension_set_enabled',{'extension_id':manifest['id'],'expected_revision':2,'enabled':True}),
        call('extension_read',{'extension_id':manifest['id']}),
        call('extension_call',{'extension_id':manifest['id'],'version':'1.0.0','arguments':{'path':str(source)}}),
        _completion(),
    ])
    monkeypatch.setattr(loop,'call_chat_completion_result',planner)
    result=asyncio.run(loop.run_agent_loop(conversation_id=conversation,source='desktop',user_message=goal,
        source_message_id=message_id,request_id='extension-model-loop',trace_id='synthetic',model_id='test-model',reasoning_level='low',model_first_creation=True))
    assert result.status=='completed',result
    assert planner.await_count==7
    assert all(o.status=='completed' for o in result.observations),result.observations
    invoked=next(o.result for o in result.observations if o.tool_name=='extension_call')
    assert Path(invoked['artifacts'][0]['path']).read_text(encoding='utf-8')=='处理:模型闭环合成内容'
    assert all(key[0]!=result.run_id for key in extensions._round_catalogs)
    schemas={t['function']['name'] for t in planner.await_args_list[0].kwargs['tools']}
    assert 'extension_call' not in schemas
    schemas={t['function']['name'] for t in planner.await_args_list[1].kwargs['tools']}
    assert 'extension_call' in schemas
