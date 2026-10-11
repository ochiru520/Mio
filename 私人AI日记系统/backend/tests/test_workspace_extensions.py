import asyncio
import io
from contextlib import closing
from unittest.mock import patch
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from app import db, workspace_service as workspace, knowledge_service as knowledge
from app import routine_service as routines, agent_task_service as tasks, agent_file_service as files
from test_runtime_safety_v2 import isolated


@pytest.fixture(autouse=True)
def setup(isolated):
    workspace.initialize()
    with patch('app.model_runtime.model_selection', return_value=('test-model', 'low')):
        yield


def test_project_goal_idempotency_and_followup():
    project = workspace.save_project('测试项目', instructions='模型自主规划')
    template = workspace.save_template('报告','整理 {{subject}}','保留来源',project['id'])
    task = workspace.enqueue_goal('',project['id'],template['id'],{'subject':'测试'},request_key='once')
    assert task['status'] == 'ready'
    assert workspace.enqueue_goal('',project['id'],template['id'],{'subject':'测试'},request_key='once')['id'] == task['id']
    with pytest.raises(ValueError): workspace.enqueue_goal('different',request_key='once')
    tasks.update(task['id'],status='completed')
    async def check():
        result=await workspace.follow_up(task['id'],'增加摘要','feedback')
        assert result['status']=='ready'
        again=await workspace.follow_up(task['id'],'增加摘要','feedback')
        assert again['status']=='ready'
        assert again['snapshot']['updates'].count('增加摘要')==1
    asyncio.run(check())
    assert workspace.detail(project['id'])['tasks'][0]['id']==task['id']


def test_selected_artifact_keeps_task_provenance(tmp_path):
    from app import artifact_service
    import hashlib

    project = workspace.save_project('成果归属')
    task = workspace.enqueue_goal('选择一张成果', project['id'])
    root = tmp_path / 'source'
    root.mkdir()
    source = root / 'chosen.txt'
    source.write_text('selected output', encoding='utf-8')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    artifact = artifact_service.import_file(
        source,
        source_root=root,
        producer='test',
        expected_sha256=digest,
        task_id=task['id'],
        conversation_id=task['conversation_id'],
        name=source.name,
        mime_type='text/plain',
    )

    selected = workspace.select_result(project['id'], artifact_id=artifact['id'], task_id=task['id'])
    assert selected['task_id'] == task['id']
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT task_id FROM workspace_selections WHERE id=?', (selected['id'],)).fetchone()
    assert row['task_id'] == task['id']
    assert task['id'] in workspace.project_context(task['conversation_id'])


def test_knowledge_cjk_sources_updates_and_revocation(tmp_path):
    project=workspace.save_project('知识库')
    root=tmp_path/'authorized';root.mkdir()
    files.roots([str(root)])
    path=root/'资料.md';path.write_text('项目说明\n星空测试与蓝色封面。',encoding='utf-8')
    doc=knowledge.index_document(project['id'],str(path))
    hits=knowledge.search(project['id'],'星空测试')['results']
    assert hits and hits[0]['citation'].startswith('资料.md') and not hits[0]['stale']
    path.write_text('更新后的海洋方案',encoding='utf-8')
    assert knowledge.search(project['id'],'星空')['results'][0]['stale']
    assert knowledge.index_document(project['id'],str(path))['id']==doc['id']
    assert not knowledge.search(project['id'],'星空')['results']
    assert knowledge.search(project['id'],'海洋')['results']
    files.roots([])
    assert not knowledge.search(project['id'],'海洋')['results']


def test_workspace_upload_accepts_structured_spreadsheets(isolated):
    from app.routes.workspace import router

    project = workspace.save_project('上传表格')
    workbook = Workbook()
    workbook.active.append(['标题', '数值'])
    workbook.active.append(['证据', 42])
    payload = io.BytesIO()
    workbook.save(payload)
    workbook.close()

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(
            f"/api/workspace/projects/{project['id']}/upload?name=证据.xlsx",
            content=payload.getvalue(),
            headers={'Content-Type': 'application/octet-stream'},
        )
    assert response.status_code == 200, response.text
    assert response.json()['name'] == '证据.xlsx'


def test_routine_schedule_once_and_file_baseline(tmp_path):
    project=workspace.save_project('定时任务')
    routine=routines.save('日报',project['id'],'schedule',{'mode':'interval','minutes':10},'总结进展',enabled=True)
    due=datetime.fromisoformat(routine['next_run_at'])
    routines.tick(due);routines.tick(due)
    assert len(tasks.list_tasks())==1
    root=tmp_path/'inputs';root.mkdir();files.roots([str(root)])
    (root/'old.txt').write_text('existing')
    routines.save('新资料',project['id'],'files',{'path':str(root),'pattern':'*.txt'},'索引新增文件',enabled=True)
    routines.tick(due)
    assert len(tasks.list_tasks())==1
    (root/'new.txt').write_text('new')
    routines.tick(due);routines.tick(due)
    assert len(tasks.list_tasks())==2


def test_desktop_api_and_paired_mobile_allowlist(monkeypatch):
    from app.routes.workspace import router
    from app import mobile_gateway as mobile
    monkeypatch.setattr(mobile,'_pair',{});monkeypatch.setattr(mobile,'_attempts',{})
    app=FastAPI();app.include_router(router)
    with TestClient(app) as client:
        project=client.post('/api/workspace/projects',json={'name':'手机项目'}).json()
        response=client.post('/api/workspace/goals',json={'goal':'总结资料','project_id':project['id'],'request_key':'mobile-goal'})
        assert response.status_code==200,response.text
        task=response.json()
        assert client.get('/api/workspace').status_code==200
        assert client.get('/api/workspace/projects/'+project['id']).status_code==200
    mobile.configure(True,8443)
    code=mobile.new_pairing()['code']
    headers={'Origin':'https://localhost:8443','X-Mio-Mobile':'1'}
    with TestClient(mobile.create_app(),base_url='https://localhost:8443') as phone:
        assert phone.get('/api/workspace').status_code==401
        assert phone.post('/api/mobile/pair',json={'code':code}).status_code==403
        paired=phone.post('/api/mobile/pair',json={'code':code},headers=headers)
        assert paired.status_code==200,paired.text
        assert 'HttpOnly' in paired.headers['set-cookie'] and 'Secure' in paired.headers['set-cookie']
        assert phone.post('/api/mobile/pair',json={'code':code},headers=headers).status_code==401
        assert phone.get('/api/workspace').json()['projects'][0]['id']==project['id']
        assert phone.get('/api/workspace/projects/'+project['id']+'/learning/review').status_code==200
        assert phone.get('/api/workspace/pending').status_code==200
        assert phone.get('/api/agent/work/'+task['id']+'/citations').status_code==200
        assert phone.get('/api/agent/work/'+task['id']+'/runs').status_code==200
        from app import agent_file_service as files
        output = files.write_document('mobile-result.md', '# 手机成果\n\n可核对的合成正文。', task['id'])
        artifact_path = '/api/artifacts/' + output['artifact_id']
        assert phone.get(artifact_path + '/preview').json()['content'].startswith('# 手机成果')
        assert phone.get(artifact_path + '/content').status_code == 200
        assert phone.post(artifact_path + '/favorite', json={'favorite': True}, headers=headers).status_code == 200
        assert phone.get('/api/workspace/stickers').status_code == 404
        moments_path = '/api/workspace/projects/' + project['id'] + '/moments'
        moment_response = phone.post(moments_path, json={'content': '手机合成动态'}, headers=headers)
        assert moment_response.status_code == 404, moment_response.text
        assert phone.get(moments_path).status_code == 404
        assert phone.get('/api/workspace/external-roots').status_code == 403
        assert phone.get('/api/workspace/execution/desktop/windows').status_code == 403
        assert phone.post('/api/workspace/music/scan', json={'path': 'D:/'}, headers=headers).status_code == 403
        assert phone.get('/api/settings/runtime').status_code==403
        assert phone.put('/api/agent/work/file-roots',json={'paths':['D:/']},headers=headers).status_code==403
        assert phone.post('/api/workspace/projects/'+project['id']+'/knowledge',json={'path':'D:/secret'},headers=headers).status_code==403
        assert phone.post('/api/agent/work/'+task['id']+'/pause',headers=headers).status_code==200
        assert tasks.get(task['id'])['status']=='paused'
        assert phone.post('/api/workspace/tasks/'+task['id']+'/follow-up',json={'message':'只要摘要','request_key':'phone-feedback'},headers=headers).status_code==200
        assert phone.post('/api/mobile/logout',headers=headers).status_code==200
        assert phone.get('/api/workspace').status_code==401


def test_mobile_certificate_contains_current_addresses():
    from app import mobile_gateway as mobile
    from cryptography import x509
    cert,key=mobile.certificate()
    parsed=x509.load_pem_x509_certificate(cert.read_bytes())
    names=parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert set(mobile.addresses()) <= {str(ip) for ip in names.get_values_for_type(x509.IPAddress)}
    assert key.exists()


def test_model_can_repeat_stateful_browser_actions_without_keyword_gate():
    import json
    from unittest.mock import AsyncMock
    from app.agent_loop_service import run_agent_loop
    from app.llm import ToolCall
    from tests.test_agent_model_first import _completion
    project=workspace.save_project('自主规划');task=workspace.enqueue_goal('继续向下看',project['id'])
    planner=AsyncMock(side_effect=[_completion(ToolCall('a','browser_action',json.dumps({'page_id':'page_test','operation':'scroll'}))),
                                  _completion(ToolCall('b','browser_action',json.dumps({'page_id':'page_test','operation':'scroll'}))),_completion()])
    dispatch=AsyncMock(return_value={'page_id':'page_test','text':'Next section'})
    async def run():
        with patch('app.agent_loop_service.call_chat_completion_result',planner),patch('app.browser_service.action',dispatch):
            result=await run_agent_loop(conversation_id=task['conversation_id'],source='desktop',user_message='继续向下看',source_message_id=0,
                  request_id='repeat-browser',trace_id='repeat-browser',model_id='test-model',reasoning_level='low',allowed_tool_names={'browser_action'},task_id=task['id'])
        assert dispatch.await_count==2
        assert len([o for o in result.observations if o.status=='completed'])==2
    asyncio.run(run())


def test_batch_serial_retry_cancel_and_recovery(monkeypatch):
    from app import creative_batch_service as batch, creation_service as creation
    from types import SimpleNamespace
    project=workspace.save_project('队列测试');task=workspace.enqueue_goal('生成两版',project['id'])
    context=SimpleNamespace(task_id=task['id'],conversation_id=task['conversation_id'],source_message_id=1,creation_workflow_id='')
    created={};calls=[]
    monkeypatch.setattr(creation,'_effective_spec',lambda spec:(spec.model_dump(),{}))
    def new_job(spec,**kwargs):
        if spec.idempotency_key in created:return created[spec.idempotency_key],False
        job={'id':'fake_'+str(len(created)),'status':'running','error':''}
        created[spec.idempotency_key]=job;calls.append(job)
        return job,True
    monkeypatch.setattr(creation,'create_job',new_job)
    monkeypatch.setattr(creation,'get_job',lambda key:next((v for v in created.values() if v['id']==key),None))
    async def cancel_job(key):
        creation.get_job(key)['status']='cancelled'
    monkeypatch.setattr(creation,'cancel_job',cancel_job)
    async def run():
        first=batch.create('两版',[{'prompt':'a'},{'prompt':'b'}],context)['batch']
        assert batch.create('两版',[{'prompt':'a'},{'prompt':'b'}],context)['batch']['id']==first['id']
        tasks.update(task['id'],status='waiting_jobs',snapshot={'waiting_batches':[first['id']]})
        await batch.tick();await batch.tick()
        assert len(calls)==1
        # Simulated restart between creating the job and recording its id.
        with closing(db.get_conn()) as conn,conn:conn.execute("UPDATE creative_batch_items SET job_id='' WHERE batch_id=? AND ordinal=0",(first['id'],))
        await batch.tick();assert len(calls)==1
        calls[0]['status']='completed';await batch.tick();assert len(calls)==2
        calls[1]['status']='failed';await batch.tick()
        assert batch.get(first['id'])['status']=='needs_attention'
        assert tasks.ready_to_continue(tasks.get(task['id']))
        tasks.update(task['id'],status='completed')
        await batch.control(first['id'],'retry',1)
        assert tasks.get(task['id'])['status']=='waiting_jobs'
        await batch.tick();assert len(calls)==3
        assert batch.get(first['id'])['items'][0]['job_id']==calls[0]['id']
        await tasks.pause(task['id'],cancel_jobs=True)
        assert batch.get(first['id'])['status']=='cancelled' and calls[-1]['status']=='cancelled'
    asyncio.run(run())


def test_real_media_assembly_and_artifact_hash(tmp_path):
    from app import media_compose_service as media, artifact_service
    from types import SimpleNamespace
    from PIL import Image
    project=workspace.save_project('合成测试')
    task=workspace.enqueue_goal('制作视频',project['id'])
    root=tmp_path/'media';root.mkdir();files.roots([str(root)])
    Image.new('RGB',(256,256),'#476c60').save(root/'image.png')
    async def run():
        spec={'name':'验收.mp4','clips':[{'path':str(root/'image.png'),'kind':'image','duration':0.5}],
              'width':256,'height':256,'subtitles':[{'start':0,'end':0.5,'text':'Mio test'}]}
        result=media.create(spec,SimpleNamespace(task_id=task['id']))
        await asyncio.gather(*list(media._running.values()))
        assembly=media.get(result['assembly']['id'])
        assert assembly['status']=='completed',assembly['error']
        path,artifact=artifact_service.file(assembly['artifact_id'])
        assert path.stat().st_size>100 and artifact['mime_type']=='video/mp4'
        assert media.create(spec,SimpleNamespace(task_id=task['id']))['assembly']['id']==assembly['id']
    asyncio.run(run())


def test_real_browser_read_fill_confirmation_and_download(tmp_path,monkeypatch):
    from app import browser_service as browser
    from types import SimpleNamespace
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading
    posts=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/download':
                data=b'test download';self.send_response(200);self.send_header('Content-Disposition','attachment; filename="test.txt"');self.end_headers();self.wfile.write(data);return
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
            self.wfile.write(b'<html><title>Fixture</title><body><form action="/submit" method="post"><input name="name"><button>Submit</button></form><a href="/download">Download</a></body></html>')
        def do_POST(self):
            posts.append(self.rfile.read(int(self.headers.get('Content-Length','0'))));self.send_response(200);self.end_headers();self.wfile.write(b'<html><body>Confirmed</body></html>')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    project=workspace.save_project('浏览器测试');task=workspace.enqueue_goal('填写示例表单',project['id'])
    context=SimpleNamespace(task_id=task['id'],conversation_id=task['conversation_id'])
    async def allow_fixture(*args,**kwargs):return None
    async def run():
        browser.initialize()
        # Only this synthetic HTTP server is allowed in the test; production rejects loopback.
        with patch.object(browser,'check_url',side_effect=allow_fixture):
            try:
                page=await browser.open_page(f'http://127.0.0.1:{server.server_port}',context)
                assert page['title']=='Fixture'
                await browser.action(page['page_id'],'fill',context,selector='input',value='Mio')
                pending=await browser.action(page['page_id'],'click',context,selector='button',commit=True,purpose='Submit fixture')
                assert not posts
                assert (await browser.confirm(pending['browser_confirmation']))['status']=='completed'
                assert len(posts)==1
                with pytest.raises(ValueError):await browser.confirm(pending['browser_confirmation'])
                await browser.action(page['page_id'],'navigate',context,url=f'http://127.0.0.1:{server.server_port}')
                shot=await browser.action(page['page_id'],'screenshot',context)
                assert shot['downloads'][0]['mime_type']=='image/png'
                stale=await browser.action(page['page_id'],'click',context,selector='button',commit=True)
                await browser.action(page['page_id'],'fill',context,selector='input',value='changed')
                with pytest.raises(ValueError,match='内容发生变化'):await browser.confirm(stale['browser_confirmation'])
                assert len(posts)==1
                await browser.action(page['page_id'],'click',context,selector='a')
                if browser._downloads:await asyncio.gather(*list(browser._downloads))
                assert any(a['name']=='test.txt' for a in (await browser.read_page(page['page_id']))['downloads'])
            finally:await browser.shutdown()
    try:asyncio.run(run())
    finally:server.shutdown();server.server_close()


def test_browser_handoff_promotes_headless_and_preserves_session_cookies(tmp_path):
    from app import browser_service as browser
    from types import SimpleNamespace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
            self.wfile.write(b'<html><title>Mio synthetic handoff</title><body><button onclick="document.body.dataset.owner=1">Owner action</button></body></html>')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    project = workspace.save_project('handoff fixture')
    task = workspace.enqueue_goal('handoff fixture', project['id'])
    ctx = SimpleNamespace(task_id=task['id'], conversation_id=task['conversation_id'])
    original_policy = browser.check_url
    async def allow_fixture(value, project_id, **kwargs):
        if value == url or value.startswith(url + '/'): return
        await original_policy(value, project_id, **kwargs)
    async def run():
        browser.initialize()
        with patch.object(browser, 'check_url', side_effect=allow_fixture):
            try:
                first = await browser.open_page(url, ctx)
                old = browser.require_page(first['page_id'])['page'].context
                assert browser._context_visibility[project['id']] is False
                await old.add_cookies([{'name': 'synthetic-session', 'value': 'owner', 'url': url}])
                pending = await browser.action(first['page_id'], 'click', ctx, selector='button', commit=True)
                manual = await browser.open_page(url, ctx, visible=True)
                owner = browser.require_page(manual['page_id'], ctx)
                assert owner['page'].context is not old
                assert browser._context_visibility[project['id']] is True
                assert any(c['name'] == 'synthetic-session' and c['value'] == 'owner' for c in await owner['page'].context.cookies())
                with pytest.raises(ValueError): browser.require_page(first['page_id'], ctx)
                with closing(db.get_conn()) as conn:
                    assert conn.execute('SELECT status FROM workspace_browser_pending WHERE id=?', (pending['browser_confirmation'],)).fetchone()[0] == 'expired'
                with pytest.raises(ValueError): await browser.confirm(pending['browser_confirmation'])
                with pytest.raises(ValueError, match='人工登录'):
                    await browser.action(manual['page_id'], 'click', ctx, selector='button')
                # Test-owned page input simulates its owner; no real login or physical input.
                await owner['page'].locator('button').click()
                assert await owner['page'].get_attribute('body', 'data-owner') == '1'
                model = await browser.open_page(url, ctx)
                assert browser.require_page(model['page_id'])['page'].context is owner['page'].context
                assert browser._context_visibility[project['id']] is True
                another = workspace.save_project('other handoff fixture')
                other_task = workspace.enqueue_goal('other fixture', another['id'])
                other_ctx = SimpleNamespace(task_id=other_task['id'], conversation_id=other_task['conversation_id'])
                isolated = await browser.open_page(url, other_ctx)
                assert not any(c['name'] == 'synthetic-session' for c in await browser.require_page(isolated['page_id'])['page'].context.cookies())
            finally:
                await browser.shutdown()
                assert not browser._contexts and not browser._context_visibility and not browser._pages
    try: asyncio.run(run())
    finally: server.shutdown(); server.server_close()
