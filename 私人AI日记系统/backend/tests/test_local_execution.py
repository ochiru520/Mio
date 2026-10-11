"""Real local execution on disposable projects; never use the owner's workspace."""
import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import db, workspace_service as workspace, agent_task_service as tasks
from app import code_workspace_service as code, terminal_service as terminal, learning_tools, routine_service as routines
from app import desktop_control_service as desktop, artifact_service as artifacts
from app.agent_tool_service import ToolExecutionContext
from app.tool_registry import tool_registry
from test_runtime_safety_v2 import isolated


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'source'; root.mkdir()
    item = workspace.save_project('临时执行项目')
    conversation = 'desktop_agent_execution_test'
    db.create_agent_conversation(conversation)
    workspace.bind_conversation(item['id'], conversation)
    config = code.configure(item['id'], str(root), True, True, True, {'test': "Write-Output 'verified'; exit 0"}, 0)
    context = ToolExecutionContext('run_execution', 'req_execution', '', conversation, 0, '临时测试', 0)
    return root, item, config, context


def test_exact_patch_preserves_indentation_encoding_and_restores(project):
    root, _, config, context = project
    original = '\ufeffdef f():\r\n    return 1\r\n'.encode('utf-16-be')
    (root / 'sample.py').write_bytes(original)
    read = code.read_file(context, 'sample.py')
    payload = tool_registry.require('code_apply_patch').validate_arguments({'workspace_revision':config['revision'], 'changes':[
        {'path':'sample.py','expected_sha256':read['sha256'],'edits':[{'old_text':'    return 1','new_text':'    return 2'}]}]})
    result = code.apply_patch(context, **payload)
    assert (root / 'sample.py').read_bytes() == original.replace('1'.encode('utf-16-be'),'2'.encode('utf-16-be'))
    assert '+    return 2' in code.diff(context, result['change_id'])['files'][0]['diff']
    code.restore(context, result['change_id'],config['revision'])
    assert (root / 'sample.py').read_bytes() == original


def test_multifile_patch_is_preflighted_and_restore_refuses_later_edit(project):
    root, _, config, context = project
    for name in ('a.js','b.vue'): (root / name).write_text('first\n',encoding='utf-8')
    def change(name): return {'path':name,'expected_sha256':code.read_file(context,name)['sha256'],'edits':[{'old_text':'first','new_text':'second'}]}
    changes = [change('a.js'),change('b.vue')]; changes[1]['expected_sha256'] = '0'*64
    with pytest.raises(ValueError,match='其他程序修改'): code.apply_patch(context,changes,config['revision'])
    assert (root/'a.js').read_text() == 'first\n'
    result = code.apply_patch(context,[change('a.js')],config['revision'])
    (root/'a.js').write_text('owner edit\n')
    with pytest.raises(ValueError,match='后续修改'): code.restore(context,result['change_id'],config['revision'])
    assert (root/'a.js').read_text() == 'owner edit\n'


@pytest.mark.parametrize('path',['../outside.py','/outside.py','C:/outside.py','x.py:secret','.env','.git/config','node_modules/x.js','private.db'])
def test_path_boundaries_reject_before_disk_access(project,path):
    _,_,config,_ = project
    with pytest.raises(ValueError): code.resolve(config,path,allow_missing=True)


def test_windows_junction_is_rejected(project,tmp_path):
    if os.name != 'nt': pytest.skip('Windows junction')
    root,_,config,_ = project
    outside=tmp_path/'outside';outside.mkdir();(outside/'x.py').write_text('private')
    junction=root/'link'
    subprocess.run(['cmd','/c','mklink','/J',str(junction),str(outside)],check=True,capture_output=True)
    try:
        with pytest.raises(ValueError,match='junction'): code.resolve(config,'link/x.py')
    finally: junction.rmdir()


def test_revoke_revision_remote_task_and_private_root_are_blocked(project):
    root,item,config,context = project
    code.configure(item['id'],str(root),False,False,False,expected_revision=config['revision'])
    with pytest.raises(ValueError): code.project_context(context,'write',config['revision'])
    with pytest.raises(ValueError): code.configure(item['id'],str(db.settings.data_dir),True)
    with pytest.raises(ValueError): code.project_context(SimpleNamespace(conversation_id=context.conversation_id,source='wechat',task_id=''))


def test_code_paging_and_new_file_recovery(project):
    root,_,config,context = project
    (root/'long.ts').write_text('\n'.join(f'line {i}' for i in range(710)),encoding='utf-8')
    page=code.read_file(context,'long.ts',300,300)
    assert page['has_more'] and page['lines'][0]['number']==301 and page['next_offset']==600
    result=code.apply_patch(context,[{'path':'nested/new.ts','create':True,'content':'  const x = 1;\n'}],config['revision'])
    code.restore(context,result['change_id'],config['revision'])
    assert not (root/'nested/new.ts').exists()


async def completed(context,job,timeout_seconds=45):
    # Windows PowerShell cold startup on shared runners can exceed a few seconds.
    # Wait for the watcher as well as the receipt so fixture teardown cannot race
    # a worker still using this test's database and process handles.
    deadline=time.monotonic()+timeout_seconds
    while time.monotonic()<deadline:
        current=terminal.read(context,job['job_id'])
        if current['status']!='running' and job['job_id'] not in terminal._running: return current
        await asyncio.sleep(.05)
    raise AssertionError(f'command did not terminate within {timeout_seconds}s: {current}')


@pytest.mark.parametrize('command,status,exit_code',[("Write-Output 'hello'; exit 0",'completed',0),("[Console]::Error.WriteLine('bad'); exit 7",'failed',7),("Start-Sleep -Seconds 10",'timed_out',None)])
def test_real_terminal_success_failure_timeout(project,command,status,exit_code):
    _,_,config,context=project
    async def run():
        job=await terminal.start(context,command,workspace_revision=config['revision'],timeout_seconds=1 if status=='timed_out' else 30)
        current=await completed(context,job)
        assert current['status']==status
        if exit_code is not None: assert current['exit_code']==exit_code
        if status=='completed': assert 'hello' in current['stdout']
        if status=='failed': assert 'bad' in current['stderr']
    asyncio.run(run())


def test_terminal_cancel_revoke_and_foreign_job_are_safe(project):
    root,item,config,context=project
    async def run():
        job=await terminal.start(context,'Start-Sleep -Seconds 30',workspace_revision=config['revision'])
        other=SimpleNamespace(**{**vars(context),'conversation_id':'desktop_agent_other'})
        with pytest.raises(ValueError): terminal.read(other,job['job_id'])
        terminal.stop(context,job['job_id']);assert (await completed(context,job))['status']=='cancelled'
        job=await terminal.start(context,'Start-Sleep -Seconds 30',workspace_revision=config['revision'])
        code.configure(item['id'],str(root),True,True,False,expected_revision=config['revision'])
        assert (await completed(context,job))['status']=='cancelled'
    asyncio.run(run())


def test_verification_evidence_detects_later_file_changes(project):
    root,_,config,context=project
    result=code.apply_patch(context,[{'path':'x.py','create':True,'content':'x=1\n'}],config['revision'])
    async def run():
        job=await terminal.verify(context,'test',config['revision'],result['change_id'])
        current=await completed(context,job)
        assert current['status']=='completed',current
        assert 'verified' in current['stdout'] and current['exit_code']==0
        assert current['evidence']['files_unchanged']
        (root/'x.py').write_text('x=2\n')
        assert not terminal.read(context,job['job_id'])['evidence']['files_unchanged']
    asyncio.run(run())


def test_terminal_restart_does_not_replay_and_task_resumes_after_exit(project):
    _,_,config,context=project
    task=tasks.prepare(run_id=context.run_id,request_id=context.request_id,conversation_id=context.conversation_id,source='desktop',user_message='test',model_id='test',reasoning_level='off',allowed_tools=[],context=[])
    ctx=ToolExecutionContext(**{**vars(context),'task_id':task['id']})
    async def run():
        job=await terminal.start(ctx,"Write-Output 'done'",workspace_revision=config['revision'])
        assert not tasks.ready_to_continue(tasks.get(task['id']))
        current=await completed(ctx,job)
        assert current['status']=='completed' and 'done' in current['stdout'],current
        assert tasks.ready_to_continue(tasks.get(task['id']))
        with closing(db.get_conn()) as conn,conn: conn.execute("UPDATE terminal_jobs SET status='running' WHERE id=?",(job['job_id'],))
        terminal._initialized.discard(str(db.settings.db_path));terminal.initialize()
        assert terminal.read(ctx,job['job_id'])['status']=='unknown'
        assert not tasks.ready_to_continue(tasks.get(task['id']))
        assert tasks.get(task['id'])['status']=='waiting_user'
    asyncio.run(run())


def test_learning_tool_round_trip_uses_shared_records(project):
    _,_,_,context=project
    async def run():
        session=await learning_tools.dispatch('learning_create_session',{'title':'练习'},context)
        item=await learning_tools.dispatch('learning_add_question',{'session_id':session['id'],'prompt':'解释版本','answer_key':'hash'},context)
        result=await learning_tools.dispatch('learning_submit_answer',{'item_id':item['id'],'answer':'hash'},context)
        assert result['score']==1
        current=await learning_tools.dispatch('learning_read_session',{'session_id':session['id']},context)
        assert current['progress']['answered']==1
    asyncio.run(run())


def test_one_time_schedule_and_past_draft(project):
    _,item,_,_=project
    now=datetime.fromisoformat(db.now_iso());future=now+timedelta(days=1)
    config={'mode':'once','date':future.date().isoformat(),'time':future.strftime('%H:%M')}
    row=routines.save('一次',item['id'],'schedule',config,'test',enabled=True)
    with patch.object(routines,'fire') as fire: routines.tick(future+timedelta(minutes=1));assert fire.call_count==1
    assert not next(r for r in routines.listing() if r['id']==row['id'])['enabled']
    old={'mode':'once','date':(now-timedelta(days=1)).date().isoformat(),'time':'10:00'}
    row=routines.save('旧草稿',item['id'],'schedule',old,'test',enabled=False)
    with pytest.raises(ValueError,match='晚于当前'): routines.set_enabled(row['id'],True)


def test_surface_picture_and_artifact_preview_and_favorite(project,tmp_path):
    from app.routes.agent_surface import router as surface_router
    from app.routes.artifacts import router as artifact_router
    app=FastAPI();app.include_router(surface_router);app.include_router(artifact_router)
    image=io.BytesIO();Image.new('RGB',(32,20)).save(image,format='PNG')
    with TestClient(app) as client:
        assert client.get('/api/agent/surface').json()['hero_shape'] == 'original'
        response=client.post('/api/agent/surface/images/hero',json={'data_url':'data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()})
        assert response.status_code==200 and response.json()['hero_url']
        assert client.get(response.json()['hero_url']).status_code==200
        assert client.delete('/api/agent/surface/images/hero').json()['hero_url']==''
        assert client.put('/api/agent/surface',json={'hero_shape':'invalid'}).status_code==422
        assert client.put('/api/agent/surface',json={'hero_shape':'circle'}).status_code == 200
        assert client.get('/api/agent/surface').json()['hero_shape'] == 'circle'
        path=tmp_path/'test.pdf';path.write_bytes(b'%PDF-1.4 synthetic')
        _,_,_,ctx=project
        item=artifacts.import_file(path,source_root=tmp_path,name='test.pdf',producer='test',conversation_id=ctx.conversation_id,expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        assert client.post(f"/api/artifacts/{item['id']}/favorite",json={'favorite':True}).status_code==200
        assert client.get('/api/artifacts?favorite=true&q=test&kind=document').json()['artifacts'][0]['id']==item['id']
        preview=client.get(f"/api/artifacts/{item['id']}/preview").json()
        assert preview['kind']=='pdf'
        assert client.get(preview['url']).headers['content-disposition'].startswith('inline')


def test_execution_permission_tiers_and_shortcuts_are_balanced():
    from app.tool_types import ToolPermission
    for name in ('code_apply_patch','code_restore','terminal_start','code_verify','desktop_act','desktop_launch'):
        assert tool_registry.require(name).permission==ToolPermission.HIGH_RISK_WRITE
    if os.name=='nt':
        from app import desktop_executor as worker
        with pytest.raises(ValueError,match='完整释放'): worker._send_keys('{VK_CONTROL down}')
        with pytest.raises(ValueError,match='非执行器'): worker._send_keys('{VK_CONTROL up}')
        assert not worker._owned_keys


def test_project_conversation_modes_and_unbinding(project):
    _,item,_,context=project
    workspace.save_conversation_mode(context.conversation_id,'learn')
    assert workspace.conversation_index()['modes'][context.conversation_id]=='learn'
    workspace.unbind_conversation(context.conversation_id)
    assert workspace.project_for_conversation(context.conversation_id) is None


def test_desktop_stale_permission_and_remote_task_are_rejected(project):
    _,item,_,context=project
    with patch.object(desktop,'rpc',return_value={'windows':[{'hwnd':101,'pid':202,'created':'1','title':'test'}]}):
        config=desktop.configure(item['id'],True,[{'hwnd':101,'pid':202,'created':'1'}],[],0)
    desktop.configure(item['id'],False,[],[],config['revision'])
    with pytest.raises(ValueError): desktop.project_context(context,config['revision'])
    assert asyncio.run(desktop.stop(context))['user_app_closed'] is False


def test_code_patch_waits_for_concrete_approval_and_runs_only_once(project):
    from app import execution_policy_service as policy
    policy.configure(expected_revision=0, confirm_project_edits=True)
    from unittest.mock import AsyncMock
    from app.agent_loop_service import run_agent_loop
    from app.companion_action_service import approve_companion_action
    from app.llm import ToolCall
    from test_agent_model_first import _completion
    root,_,config,ctx=project
    arguments={'workspace_revision':config['revision'],'changes':[{'path':'approved.js','create':True,'content':'  const accepted = true;\n'}]}
    message_id=db.save_message('user','在项目中新建这个代码文件',conversation_id=ctx.conversation_id)
    async def run():
        with patch('app.agent_loop_service.call_chat_completion_result',AsyncMock(return_value=_completion(ToolCall('patch','code_apply_patch',json.dumps(arguments))))):
            result=await run_agent_loop(conversation_id=ctx.conversation_id,source='desktop',user_message='新建代码文件',source_message_id=message_id,request_id='approval_test',trace_id='approval_test',model_id='test-model',reasoning_level='off',allowed_tool_names={'code_apply_patch'},model_first_creation=True)
        assert result.status=='waiting_confirmation'
        assert not (root/'approved.js').exists()
        action_id=result.observations[0].action_id
        approved=await approve_companion_action(action_id)
        assert approved['status']=='executed'
        assert (root/'approved.js').read_text()=='  const accepted = true;\n'
        with pytest.raises(ValueError): await approve_companion_action(action_id)
    asyncio.run(run())


def test_windows_command_cancel_kills_owned_descendants(project):
    if os.name!='nt': pytest.skip('Windows Job Object')
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    root,_,config,ctx=project
    shell_executable=terminal.shells()['powershell']
    # CreateProcess with explicit paths/cwd avoids Start-Process's ShellExecute
    # and desktop-session defaults. The terminal still owns the real process tree:
    # PowerShell -> Python parent -> Python child.
    (root/'parent.py').write_text(
        'import pathlib, subprocess, sys\n'
        'root = pathlib.Path(__file__).resolve().parent\n'
        'print("PARENT_STARTED", flush=True)\n'
        'child = subprocess.Popen([sys.executable, str(root / "child.py")], '
        'cwd=str(root), creationflags=subprocess.CREATE_NO_WINDOW)\n'
        'print("CHILD_SPAWNED " + str(child.pid), flush=True)\n'
        'raise SystemExit(child.wait())\n',encoding='utf-8')
    (root/'child.py').write_text(
        'import os, pathlib, time\n'
        'root = pathlib.Path(__file__).resolve().parent\n'
        '(root / "child-pid.txt").write_text(str(os.getpid()), encoding="utf-8")\n'
        'print("CHILD_READY", flush=True)\n'
        '(root / "child-ready.txt").write_text("ready", encoding="utf-8")\n'
        'while not (root / "child-release.txt").exists():\n'
        '    time.sleep(0.1)\n'
        '(root / "child-marker.txt").write_text("alive", encoding="utf-8")\n',encoding='utf-8')
    def diagnostics(job):
        current=terminal.read(ctx,job['job_id'])
        return json.dumps({'shell_executable':shell_executable,**{key:current[key] for key in ('status','pid','exit_code','error','stdout','stderr')}},ensure_ascii=False)+\
            '; files='+json.dumps(sorted(path.name for path in root.iterdir()),ensure_ascii=False)
    command="& '"+sys.executable.replace("'","''")+"' '"+str(root/'parent.py').replace("'","''")+"'"
    async def run():
        job=await terminal.start(ctx,command,workspace_revision=config['revision'])
        child_handle=None
        try:
            deadline=time.monotonic()+30
            while not (root/'child-ready.txt').exists() and time.monotonic()<deadline:
                assert terminal.read(ctx,job['job_id'])['status']=='running',diagnostics(job)
                await asyncio.sleep(.1)
            assert (root/'child-ready.txt').exists(),diagnostics(job)
            child_pid=int((root/'child-pid.txt').read_text(encoding='utf-8-sig').strip())
            child_handle=kernel.OpenProcess(0x00100000,False,child_pid)  # SYNCHRONIZE
            assert child_handle,ctypes.get_last_error()
            assert kernel.WaitForSingleObject(child_handle,0)==258, 'Child must be alive before cancellation'
            terminal.stop(ctx,job['job_id'])
            assert (await completed(ctx,job,timeout_seconds=10))['status']=='cancelled'
            deadline=time.monotonic()+10
            while kernel.WaitForSingleObject(child_handle,0)==258 and time.monotonic()<deadline:
                await asyncio.sleep(.05)
            assert kernel.WaitForSingleObject(child_handle,0)==0, 'Owned descendant survived cancellation'
            (root/'child-release.txt').write_text('release',encoding='utf-8')
            assert not (root/'child-marker.txt').exists()
        finally:
            if job['job_id'] in terminal._running:
                terminal.stop(ctx,job['job_id']);await completed(ctx,job,timeout_seconds=10)
            if child_handle: kernel.CloseHandle(child_handle)
    asyncio.run(run())
