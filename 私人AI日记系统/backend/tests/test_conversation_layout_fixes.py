from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import db, code_workspace_service as code, workspace_service as workspace
from app.routes import agent
from test_runtime_safety_v2 import isolated


def test_empty_project_root_never_authorizes_process_working_directory():
    item=workspace.save_project('旧项目')
    with pytest.raises(ValueError,match='空目录'):
        code.configure(item['id'],'',True,True,True,{},0)
    state=code.configuration(item['id'])
    assert state['revision']>=1 and state['read_enabled'] and state['root']==''
    from types import SimpleNamespace
    cid='desktop_agent_empty_project';db.create_agent_conversation(cid);workspace.bind_conversation(item['id'],cid)
    with pytest.raises(ValueError,match='先选择项目文件夹'):
        code.project_context(SimpleNamespace(conversation_id=cid,source='desktop',task_id=''))


def test_history_cursor_loads_older_rows_without_overlap_or_other_conversations():
    cid='desktop_agent_history_layout'
    db.create_agent_conversation(cid,'合成历史')
    ids=[db.save_message('user' if i%2==0 else 'assistant',f'合成记录{i}',conversation_id=cid) for i in range(137)]
    db.save_message('user','其他会话',conversation_id='default')
    app=FastAPI();app.include_router(agent.router)
    with TestClient(app) as client:
        recent=client.get('/api/agent/messages',params={'conversation_id':cid,'limit':120}).json()
        assert [row['id'] for row in recent]==ids[-120:]
        older=client.get('/api/agent/messages',params={'conversation_id':cid,'limit':120,'before_id':recent[0]['id']}).json()
        assert [row['id'] for row in older]==ids[:17]
        assert client.get('/api/agent/messages',params={'conversation_id':cid,'before_id':older[0]['id']}).json()==[]


def test_directory_save_and_permission_flags_reload_real_local_state(tmp_path):
    item=workspace.save_project('项目目录')
    root=tmp_path/'project';root.mkdir()
    saved=code.configure(item['id'],str(root),False,False,False,{},0)
    assert saved['root']==str(root) and saved['revision']==1
    saved=code.configure(item['id'],str(root),True,True,True,{'test':'python -m pytest'},1)
    assert workspace.require_project(item['id'])['directory']==str(root)
    assert code.configuration(item['id'])==saved
