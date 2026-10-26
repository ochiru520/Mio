"""Schema/service separation, using synthetic state in temporary databases."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app import db, agent_skill_service as skills, agent_task_service as tasks
from app import extension_service as extensions, daily_state_analysis_service as analysis
from app.routes.chat import api_analyze_today_state as analyze_today_state
from test_runtime_safety_v2 import isolated


def test_central_bootstrap_upgrades_populated_domains_without_reset(isolated):
    item=skills.save('合成技能','升级测试','旧库内容')
    item=skills.save('合成技能','升级测试','更新内容',skill_id=item['id'],expected_revision=1)
    with db.get_conn() as conn:
        conn.execute('DROP TABLE agent_skill_drafts')
        conn.execute('DROP TABLE extension_activations')
    db.init_db()
    db.init_db()
    assert skills.read(item['id'])['content']=='更新内容'
    assert skills.read(item['id'],revision=1)['content']=='旧库内容'
    with db.get_conn() as conn:
        tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'agent_skill_drafts','extension_activations','agent_tasks','agent_task_runs','agent_task_settings'} <= tables
        assert conn.execute("SELECT count(*) FROM agent_skill_versions WHERE skill_id='skill_research'").fetchone()[0]==1


def test_ordinary_domain_calls_never_run_schema_or_default_seeding(isolated,monkeypatch):
    statements=[]
    connect=db.get_conn
    def traced():
        conn=connect()
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(db,'get_conn',traced)
    monkeypatch.setattr(db,'init_db',lambda: pytest.fail('ordinary call attempted schema initialization'))
    assert len(skills.listing())==4
    draft=skills.save_draft('合成草稿','边界测试','仅指导')
    item=skills.activate_draft(draft['id'])
    assert skills.activate_draft(draft['id'])['id']==item['id']
    skills.enabled(item['id'],False,item['revision'])
    task=tasks.prepare(run_id='schema-test-run',request_id='schema-test-request',conversation_id='desktop_schema_test',
        source='desktop',user_message='合成目标',model_id='synthetic',reasoning_level='low',allowed_tools=[],context=[])
    tasks.update(task['id'],status='paused')
    assert tasks.list_tasks('desktop_schema_test')[0]['status']=='paused'
    assert tasks.limits()['model_calls']==8
    panel=extensions.save({'name':'合成面板','kind':'panel','version':'1.0.0',
        'panel':{'title':'合成指标','blocks':[{'type':'text','text':'测试'}]}})
    panel=asyncio.run(extensions.validate(panel['id'],expected_revision=panel['revision']))
    assert extensions.enabled(panel['id'],True,panel['revision'])['enabled']
    assert extensions.freeze_round('synthetic-run',1)=={}
    assert len(extensions.listing())==1
    assert not any(sql.lstrip().upper().startswith(('CREATE ','ALTER ','DROP ')) for sql in statements)
    assert not any('INSERT OR IGNORE INTO agent_skills' in sql for sql in statements)


def test_shared_analysis_preserves_http_failures_and_cancellation(isolated,monkeypatch):
    monkeypatch.setattr(analysis,'require_configured',lambda: None)
    with pytest.raises(HTTPException) as empty:
        asyncio.run(analyze_today_state())
    assert empty.value.status_code==400
    db.save_message('user','合成状态素材',source='desktop',conversation_id='desktop_state_test')
    monkeypatch.setattr(analysis,'call_chat_completion',AsyncMock(side_effect=RuntimeError('synthetic provider failure')))
    with pytest.raises(HTTPException) as failed:
        asyncio.run(analyze_today_state())
    assert failed.value.status_code==502
    assert '今日状态判定失败' in failed.value.detail
    monkeypatch.setattr(analysis,'call_chat_completion',AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(analyze_today_state())
