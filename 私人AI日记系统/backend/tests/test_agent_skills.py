import asyncio
from contextlib import closing
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import agent_skill_service as skills, agent_file_service as files, agent_result_context as results, db
from app.routes.workspace import router
from app.workspace_tools import dispatch
from test_runtime_safety_v2 import isolated


def test_revisions_paging_toggle_conflict_and_persistence(isolated):
    assert len(skills.listing()) == 4
    content = '合成指导'*7000+'末尾证据'
    saved = skills.save('长技能','合成测试',content)
    chunks, offset = [], 0
    while True:
        page = skills.read(saved['id'],revision=1,offset=offset,max_chars=1000)
        chunks.append(page['content'])
        if not page['has_more']: break
        offset=page['next_offset']
    assert ''.join(chunks) == content
    stopped = skills.enabled(saved['id'],False,1)
    assert stopped['revision'] == 2 and not stopped['enabled']
    with pytest.raises(ValueError,match='其他编辑'):
        skills.save('旧草稿','',content,skill_id=saved['id'],expected_revision=1)
    with pytest.raises(ValueError,match='停用'):
        skills.read(saved['id'],active_only=True)
    resumed = skills.enabled(saved['id'],True,2)
    assert resumed['revision'] == 3
    assert skills.read(saved['id'],revision=1)['content_hash'] == resumed['content_hash']
    with pytest.raises(ValueError,match='版本已变化'):
        skills.read(saved['id'],revision=1,active_only=True)
    assert results.current_result('skill_read',saved)['source_status'] == 'unavailable'
    with closing(db.get_conn()) as conn:
        assert conn.execute('SELECT count(*) FROM agent_skill_versions WHERE skill_id=?',(saved['id'],)).fetchone()[0] == 3


def test_import_rechecks_source_authorization_and_local_edit_detaches(isolated,tmp_path):
    root=tmp_path/'authorized'; root.mkdir(); files.roots([str(root)])
    source=root/'guidance.md'; source.write_text('# 合成技能\n原始指导',encoding='utf-8')
    imported=skills.import_file(str(source))
    payload=asyncio.run(dispatch('skill_read',{'skill_id':imported['id']},SimpleNamespace(task_id='')))
    assert '原始指导' in payload['content'] and payload['source_status']=='current'
    source.write_text('# 合成技能\n更新内容',encoding='utf-8')
    assert next(item for item in skills.listing() if item['id']==imported['id'])['source_status']=='stale'
    assert '原始指导' not in json.dumps(results.current_result('skill_read',payload),ensure_ascii=False)
    with pytest.raises(ValueError,match='来源已变化'):
        skills.read(imported['id'],active_only=True)
    updated=skills.import_file(str(source),skill_id=imported['id'],expected_revision=1)
    assert updated['revision']==2 and '更新内容' in updated['content']
    files.roots([])
    assert skills.read(imported['id'])['source_status']=='unavailable'
    with pytest.raises(ValueError,match='授权'):
        skills.read(imported['id'],active_only=True)
    local=skills.save('独立技能','','用户编辑',skill_id=imported['id'],expected_revision=2)
    assert local['source_path']=='' and local['source_status']=='local'


def test_api_does_not_overwrite_conflicting_draft_and_rejects_foreign_fields(isolated):
    app=FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        payload={'name':'合成技能','content':'用户指导'}
        saved=client.post('/api/workspace/skills',json=payload).json()
        url='/api/workspace/skills/'+saved['id']
        assert client.post(url+'/enabled',json={'enabled':False,'expected_revision':1}).json()['revision']==2
        assert client.put(url,json={**payload,'expected_revision':1}).status_code==400
        assert client.get(url).json()['enabled']==0
        assert client.put(url,json={**payload,'expected_revision':2,'source_path':'C:/private'}).status_code==422
        assert client.get(url+'?revision=1').json()['content']=='用户指导'


def test_phone_skill_edit_requires_pairing_and_cannot_import_local_paths(isolated,monkeypatch):
    from app import mobile_gateway as mobile
    monkeypatch.setattr(mobile,'_pair',{}); monkeypatch.setattr(mobile,'_attempts',{})
    mobile.configure(True,8443); code=mobile.new_pairing()['code']
    headers={'Origin':'https://localhost:8443','X-Mio-Mobile':'1'}
    payload={'name':'手机技能','content':'合成指导'}
    with TestClient(mobile.create_app(),base_url='https://localhost:8443') as phone:
        assert phone.post('/api/workspace/skills',json=payload,headers=headers).status_code==401
        assert phone.post('/api/mobile/pair',json={'code':code},headers=headers).status_code==200
        assert phone.post('/api/workspace/skills',json=payload).status_code==403
        saved=phone.post('/api/workspace/skills',json=payload,headers=headers).json()
        url='/api/workspace/skills/'+saved['id']
        assert phone.get(url).json()['content']=='合成指导'
        assert phone.put(url,json={**payload,'expected_revision':1},headers={**headers,'Origin':'https://foreign.invalid'}).status_code==403
        assert phone.post('/api/workspace/skills/import',json={'path':'C:/private.md'},headers=headers).status_code==403
        assert phone.post(url+'/enabled',json={'enabled':False,'expected_revision':1},headers=headers).json()['revision']==2
