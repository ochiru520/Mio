"""Behavioral checks for owner policy, approvals and fail-closed isolation."""
import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, execution_policy_service as policy, terminal_sandbox as sandbox
from app import workspace_service as workspace, code_workspace_service as code
from app import agent_file_service as files
from app.agent_tool_service import ToolExecutionContext, execute_tool_call
from app.tool_registry import tool_registry
from test_runtime_safety_v2 import isolated


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    item = workspace.save_project('执行设置合成测试')
    conversation = 'desktop_agent_policy_test'
    db.create_agent_conversation(conversation)
    workspace.bind_conversation(item['id'], conversation)
    config = code.configure(item['id'], str(root), True, True, True, expected_revision=0)
    context = ToolExecutionContext('policy_run', 'policy_req', '', conversation, 0, 'synthetic', 0)
    return root, item, config, context


def test_partial_policy_save_preserves_other_settings_and_stale_write_conflicts():
    from app.routes.execution_policy import router
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        one = client.put('/api/execution/policy', json={'expected_revision': 0, 'read_scope':'folders',
            'network_enabled':True,'confirm_project_edits':True}).json()
        two = client.put('/api/execution/policy', json={'expected_revision':one['revision'], 'mode':'read_only'}).json()
        assert two['read_scope']=='folders' and two['network_enabled'] and two['confirm_project_edits']
        assert two['mode']=='read_only'
        stale = client.put('/api/execution/policy', json={'expected_revision':one['revision'], 'mode':'autonomous'})
        assert stale.status_code==409
        assert client.get('/api/execution/policy').json()['mode']=='read_only'


def test_policy_rejects_mutation_but_keeps_stop_available(project):
    root, _, config, context = project
    policy.configure(expected_revision=0, mode='read_only')
    for name in ('code_apply_patch','extension_save_draft','self_upgrade_start','desktop_act','terminal_start'):
        assert policy.decision(tool_registry.require(name), {}, context)[0]=='deny'
    for name in ('code_read_file','terminal_stop','desktop_stop','self_upgrade_stop'):
        assert policy.decision(tool_registry.require(name), {}, context)[0]=='allow'
    with pytest.raises(ValueError, match='只查看'):
        code.apply_patch(context, [{'path':'x.py','create':True,'content':'x=1'}], config['revision'])
    assert not (root/'x.py').exists()


def test_local_policy_does_not_grant_remote_execution(project):
    _, _, config, context = project
    policy.configure(expected_revision=0, mode='autonomous', terminal_mode='host')
    assert policy.decision(tool_registry.require('terminal_start'), {'workspace_revision':config['revision']}, context)[0]=='allow'
    remote = replace(context, source='wechat')
    assert policy.decision(tool_registry.require('terminal_start'), {}, remote)[0]=='ask'
    with pytest.raises(ValueError): code.project_context(remote, 'execute', config['revision'])


def test_autonomy_selects_host_and_assisted_restores_command_confirmation():
    context=ToolExecutionContext('r','q','','desktop_agent_policy',0,'synthetic',0)
    definition=tool_registry.require('self_upgrade_build')
    policy.configure(expected_revision=0,mode='autonomous')
    assert policy.configuration()['terminal_mode']=='host'
    assert policy.decision(definition,{},context)[0]=='allow'
    policy.configure(expected_revision=1,mode='assisted')
    assert policy.configuration()['terminal_mode']=='confirm'
    assert policy.decision(definition,{},context)[0]=='ask'


def test_project_read_scope_and_explicit_folder_scope(project, tmp_path):
    root, _, _, context = project
    other=tmp_path/'documents';other.mkdir()
    (root/'sample.py').write_text('x=1',encoding='utf-8')
    (other/'note.txt').write_text('synthetic',encoding='utf-8')
    files.roots([str(other)])
    policy.configure(expected_revision=0, read_scope='project')
    with policy.scope(context):
        assert files.read_document(str(root/'sample.py'))['text']=='x=1'
        with pytest.raises(ValueError): files.read_document(str(other/'note.txt'))
    policy.configure(expected_revision=1, read_scope='folders')
    assert files.read_document(str(other/'note.txt'))['text']=='synthetic'
    with pytest.raises(ValueError): files.read_document(str(root/'sample.py'))


def test_computer_read_scope_excludes_credentials_and_private_data(project, tmp_path, monkeypatch):
    root, _, _, context = project
    (root/'read.txt').write_text('synthetic')
    (root/'private.key').write_text('synthetic secret')
    monkeypatch.setattr(policy, 'local_roots', lambda:[tmp_path])
    original=policy.protected
    monkeypatch.setattr(policy, 'protected', lambda path,**kwargs: original(Path(path).relative_to(tmp_path),**kwargs))
    policy.configure(expected_revision=0, read_scope='computer')
    with policy.scope(context):
        assert files.read_document(str(root/'read.txt'))['text']=='synthetic'
        with pytest.raises(ValueError): files.resolve_read(str(root/'private.key'))
        with pytest.raises(ValueError): files.resolve_read(str(db.settings.db_path))


def test_secret_environment_not_inherited(monkeypatch):
    for key in ('OPENAI_API_KEY','MIO_DESKTOP_RPC_TOKEN','ANTHROPIC_API_KEY','MODEL_TOKEN','HTTP_PROXY'):
        monkeypatch.setenv(key,'synthetic secret')
    env=sandbox.environment()
    assert not any(key in env for key in ('OPENAI_API_KEY','MIO_DESKTOP_RPC_TOKEN','ANTHROPIC_API_KEY','MODEL_TOKEN','HTTP_PROXY'))
    assert env['PYTHONUTF8']=='1'


def test_sandbox_unavailable_refuses_configuration_and_start(project, tmp_path):
    root, _, _, _ = project
    with patch.object(sandbox,'status',return_value={'available':False,'message':'synthetic unavailable'}):
        with pytest.raises(ValueError,match='不可用'): policy.configure(expected_revision=0,terminal_mode='sandbox')
        assert policy.configuration()['revision']==0
        with pytest.raises(ValueError,match='不会降级'):
            sandbox.prepare('test',root,tmp_path,dict(sandbox_image='python:3.12-alpine',network_enabled=False,revision=0),'bash','echo hello')


def test_pending_patch_cannot_execute_after_policy_revocation(project):
    from app.companion_action_service import approve_companion_action
    root, _, config, context = project
    policy.configure(expected_revision=0,confirm_project_edits=True)
    db.create_agent_run(context.run_id,context.request_id,conversation_id=context.conversation_id,source='desktop',model_id='test-model',reasoning_level='off')
    async def run():
        pending=await execute_tool_call('code_apply_patch',{'workspace_revision':config['revision'],
            'changes':[{'path':'revoked.py','create':True,'content':'x=1'}]},context)
        assert pending.status=='needs_confirmation'
        policy.configure(expected_revision=1,mode='read_only')
        result=await approve_companion_action(pending.action_id)
        assert result['status']=='failed'
        assert not (root/'revoked.py').exists()
    asyncio.run(run())


def test_self_upgrade_approval_dispatches_bound_job(project):
    from app.companion_action_service import approve_companion_action
    from app import self_upgrade_service as upgrade
    _, _, _, context = project
    policy.configure(expected_revision=0, mode='assisted')
    job_id='upgrade_'+'a'*32
    db.create_agent_run(context.run_id,context.request_id,conversation_id=context.conversation_id,source='desktop')
    async def run():
        pending=await execute_tool_call('self_upgrade_validate',{'job_id':job_id},context)
        assert pending.status=='needs_confirmation'
        with patch.object(upgrade,'get',return_value={'conversation_id':context.conversation_id,'task_id':''}), \
             patch.object(upgrade,'validate',return_value={'status':'checking','job_id':job_id}) as validate:
            result=await approve_companion_action(pending.action_id)
        assert result['status']=='executed'
        validate.assert_called_once_with(job_id=job_id,backend_tests=['tests/test_self_upgrade.py'])
    asyncio.run(run())


def test_sandbox_copy_excludes_secrets_and_publishes_with_cas(project, tmp_path, monkeypatch):
    root, _, config, context = project
    (root/'sample.py').write_text('x=1\n',encoding='utf-8')
    (root/'.env').write_text('synthetic secret')
    folder=tmp_path/'receipt';folder.mkdir()
    monkeypatch.setattr(sandbox,'status',lambda *a,**k:{'available':True,'image_id':'sha256:synthetic'})
    argv,record=sandbox.prepare('synthetic',root,folder,dict(sandbox_image='python:3.12-alpine',network_enabled=False,revision=0),'bash','echo hello')
    try:
        assert argv[argv.index('--network')+1]=='none'
        assert not (Path(record['stage'])/'.env').exists()
        (Path(record['stage'])/'sample.py').write_text('x=2\n',encoding='utf-8')
        assert sandbox.publish('synthetic',context,config['revision'])['changed_files']==1
        assert (root/'sample.py').read_text()=='x=2\n'
        (root/'sample.py').write_text('owner edit\n')
        (Path(record['stage'])/'sample.py').write_text('x=3\n')
        with pytest.raises(ValueError,match='未覆盖'): sandbox.publish('synthetic',context,config['revision'])
        assert (root/'sample.py').read_text()=='owner edit\n'
    finally:
        sandbox._active.pop('synthetic',None)
