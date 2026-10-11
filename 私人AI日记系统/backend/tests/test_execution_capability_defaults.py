"""Global capability inheritance with explicit project grants and scope preserved."""
import asyncio
import importlib
import json
import time
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, workspace_service as workspace, code_workspace_service as code
from app import desktop_control_service as desktop, execution_capability_defaults_service as defaults
from app import execution_policy_service as policy, terminal_service as terminal
from app.tool_registry import tool_registry
from test_runtime_safety_v2 import isolated


async def completed(context,job_id,timeout_seconds=45):
    deadline=time.monotonic()+timeout_seconds
    while time.monotonic()<deadline:
        result=terminal.read(context,job_id)
        if result['status']!='running' and job_id not in terminal._running:
            return result
        await asyncio.sleep(.05)
    raise AssertionError(f'command did not terminate within {timeout_seconds}s: {result}')


def inherited_project(tmp_path):
    root = tmp_path / 'source'; root.mkdir()
    project = workspace.save_project('默认能力合成测试', directory=str(root))
    conversation = 'desktop_agent_defaults_test'
    db.create_agent_conversation(conversation)
    workspace.bind_conversation(project['id'], conversation)
    context = SimpleNamespace(conversation_id=conversation, source='desktop', task_id='', run_id='defaults')
    return root, project, context


def test_new_project_inherits_without_per_project_save_and_survives_reload(tmp_path):
    root, project, context = inherited_project(tmp_path)
    (root / 'source.py').write_text('x=1\n', encoding='utf-8')
    config = code.configuration(project['id'])
    control = desktop.configuration(project['id'])
    assert config['permission_source'] == control['permission_source'] == 'global'
    assert all(config[key] for key in ('read_enabled', 'write_enabled', 'execute_enabled'))
    assert control['enabled'] and control['windows'] == control['executables'] == []
    assert 1 <= config['revision'] < 2**53
    assert code.read_file(context, 'source.py')['content'] == 'x=1'
    from app import agent_file_service as files
    with policy.scope(context):
        assert files.read_document(str(root / 'source.py'))['text'] == 'x=1\n'
    result = code.apply_patch(context, [{'path':'added.js','create':True,'content':'const x = 1;\n'}], config['revision'])
    assert (root / 'added.js').exists() and result['change_id']
    importlib.reload(defaults)
    assert code.configuration(project['id']) == config
    assert desktop.configuration(project['id']) == control
    with closing(db.get_conn()) as conn:
        assert conn.execute('SELECT count(*) FROM code_workspaces').fetchone()[0] == 0


def test_unconfigured_existing_project_also_inherits_but_explicit_off_wins(tmp_path):
    root, project, context = inherited_project(tmp_path)
    inherited = code.configuration(project['id'])
    saved = code.configure(project['id'], str(root), False, False, False, expected_revision=inherited['revision'])
    control = desktop.configuration(project['id'])
    revoked = desktop.configure(project['id'], False, expected_revision=control['revision'])
    defaults.configure(expected_revision=1, read_enabled=False, write_enabled=False, execute_enabled=False, desktop_enabled=False)
    defaults.configure(expected_revision=2, **defaults.DEFAULTS)
    importlib.reload(defaults)
    assert code.configuration(project['id']) == saved
    assert desktop.configuration(project['id']) == revoked
    with pytest.raises(ValueError, match='尚未启用'):
        code.project_context(context, 'write')
    with pytest.raises(ValueError, match='尚未启用'):
        desktop.project_context(context)


def test_global_changes_do_not_change_explicit_grants(tmp_path):
    root, project, context = inherited_project(tmp_path)
    explicit = code.configure(project['id'], str(root), True, False, True, expected_revision=0)
    default = defaults.configuration()
    defaults.configure(expected_revision=default['revision'], **dict.fromkeys(defaults.DEFAULTS, False))
    assert code.configuration(project['id']) == explicit
    assert code.project_context(context, 'execute', explicit['revision'])['execute_enabled']


def test_default_revocation_invalidates_receipts_and_stale_project_save(tmp_path):
    root, project, context = inherited_project(tmp_path)
    previous = code.configuration(project['id'])
    previous_desktop = desktop.configuration(project['id'])
    defaults.configure(expected_revision=1, write_enabled=False, execute_enabled=False, desktop_enabled=False)
    with pytest.raises(ValueError, match='版本已改变'):
        code.project_context(context, 'write', previous['revision'])
    with pytest.raises(ValueError, match='尚未启用'):
        desktop.project_context(context, previous_desktop['revision'])
    with pytest.raises(ValueError, match='授权已改变'):
        code.configure(project['id'], str(root), True, True, True, expected_revision=previous['revision'])
    with pytest.raises(ValueError, match='权限已改变'):
        desktop.configure(project['id'], True, expected_revision=previous_desktop['revision'])
    defaults.configure(expected_revision=2, write_enabled=True, execute_enabled=True, desktop_enabled=True)
    assert code.configuration(project['id'])['revision'] != previous['revision']


def test_default_capabilities_do_not_grant_empty_root_arbitrary_windows_or_remote_access(tmp_path):
    project = workspace.save_project('没有文件夹的项目')
    conversation = 'desktop_agent_defaults_empty'
    db.create_agent_conversation(conversation); workspace.bind_conversation(project['id'], conversation)
    context = SimpleNamespace(conversation_id=conversation, source='desktop', task_id='', run_id='empty')
    with pytest.raises(ValueError, match='先选择项目文件夹'):
        code.list_files(context)
    policy.configure(expected_revision=0, read_scope='project')
    with policy.scope(context):
        assert '' not in policy.document_roots([])
    config = desktop.configuration(project['id'])
    with pytest.raises(ValueError, match='尚未授权'):
        desktop._authorized(config, 123)
    with pytest.raises(ValueError, match='本机代码'):
        code.project_context(SimpleNamespace(**{**vars(context), 'source':'wechat'}))
    with pytest.raises(ValueError, match='仅供本机'):
        desktop.project_context(SimpleNamespace(**{**vars(context), 'source':'wechat'}))


def test_directory_change_invalidates_inherited_revision_and_explicit_scope_stays_closed(tmp_path):
    root, project, context = inherited_project(tmp_path)
    previous = code.configuration(project['id'])
    second = tmp_path / 'second'; second.mkdir()
    workspace.save_project('默认能力合成测试', project_id=project['id'], directory=str(second))
    assert code.configuration(project['id'])['root'] == str(second)
    with pytest.raises(ValueError, match='版本已改变'):
        code.project_context(context, 'write', previous['revision'])
    code.configure(project['id'], str(second), False, False, False, expected_revision=0)
    workspace.save_project('默认能力合成测试', project_id=project['id'], directory=str(root))
    assert code.configuration(project['id'])['root'] == str(second)
    assert not code.configuration(project['id'])['read_enabled']


def test_partial_global_api_save_conflict_and_policy_approvals_remain(tmp_path):
    from app.routes.execution import router
    app = FastAPI(); app.include_router(router)
    _, project, context = inherited_project(tmp_path)
    with TestClient(app) as client:
        initial = client.get('/api/workspace/execution/defaults').json()
        saved = client.put('/api/workspace/execution/defaults', json={'expected_revision':initial['revision'], 'desktop_enabled':False})
        assert saved.status_code == 200
        assert saved.json()['read_enabled'] and saved.json()['write_enabled'] and saved.json()['execute_enabled']
        stale = client.put('/api/workspace/execution/defaults', json={'expected_revision':initial['revision'], 'desktop_enabled':True})
        assert stale.status_code == 409
    config = code.configuration(project['id'])
    assert policy.decision(tool_registry.require('terminal_start'), {'workspace_revision':config['revision']}, context)[0] == 'allow'
    policy.configure(expected_revision=0, mode='assisted')
    assert policy.decision(tool_registry.require('terminal_start'), {'workspace_revision':config['revision']}, context)[0] == 'ask'
    policy.configure(expected_revision=1, mode='read_only')
    assert policy.decision(tool_registry.require('code_apply_patch'), {}, context)[0] == 'deny'
    assert policy.decision(tool_registry.require('terminal_stop'), {}, context)[0] == 'allow'


def test_running_inherited_command_stops_when_defaults_revoked(tmp_path):
    _, project, context = inherited_project(tmp_path)
    config = code.configuration(project['id'])
    async def run():
        shell = 'powershell' if 'powershell' in terminal.shells() else 'bash'
        command = 'Start-Sleep -Seconds 30' if shell == 'powershell' else 'sleep 30'
        job = await terminal.start(context, command, shell, config['revision'])
        defaults.configure(expected_revision=1, execute_enabled=False)
        result=await completed(context,job['job_id'],timeout_seconds=10)
        assert result['status'] == 'cancelled'
    asyncio.run(run())


def test_autonomy_api_unifies_mode_terminal_and_edits_without_auto_approving_pending(tmp_path):
    from app.routes.execution_policy import router
    app = FastAPI(); app.include_router(router)
    root, project, context = inherited_project(tmp_path)
    config = code.configuration(project['id'])
    with TestClient(app) as client:
        assisted = client.put('/api/execution/policy', json={'expected_revision':0,'mode':'assisted','confirm_project_edits':True}).json()
        assert assisted['terminal_mode'] == 'confirm'
        autonomous = client.put('/api/execution/policy', json={'expected_revision':assisted['revision'],'mode':'autonomous'}).json()
        assert autonomous['terminal_mode'] == 'host' and not autonomous['confirm_project_edits']
        assert policy.decision(tool_registry.require('terminal_start'), {'workspace_revision':config['revision']}, context)[0] == 'allow'
        assert policy.decision(tool_registry.require('code_apply_patch'), {'workspace_revision':config['revision']}, context)[0] == 'allow'
        code.configure(project['id'], str(root), True, False, False, expected_revision=config['revision'])
        explicit = code.configuration(project['id'])
        assert policy.decision(tool_registry.require('terminal_start'), {'workspace_revision':explicit['revision']}, context)[0] == 'deny'
        confirm = client.put('/api/execution/policy', json={'expected_revision':autonomous['revision'],'terminal_mode':'confirm'}).json()
        assert confirm['mode'] == 'assisted'
        conflict = client.put('/api/execution/policy', json={'expected_revision':confirm['revision'],'mode':'autonomous','terminal_mode':'confirm'})
        assert conflict.status_code == 400


def test_legacy_autonomous_confirm_migration_is_persistent_and_versioned():
    policy.initialize()
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR REPLACE INTO execution_policy VALUES(1,?,?,?)',
                     (9,json.dumps({**policy.DEFAULTS,'mode':'autonomous','terminal_mode':'confirm'}),db.now_iso()))
    updated = policy.configuration()
    assert updated['terminal_mode'] == 'host' and updated['revision'] == 10
    importlib.reload(policy)
    assert policy.configuration() == updated
    assert policy.configure(expected_revision=10, mode='assisted')['terminal_mode'] == 'confirm'
    assert policy.configuration()['revision'] == 11


def test_autonomy_preserves_selected_sandbox():
    with patch('app.terminal_sandbox.status', return_value={'available':True,'message':'synthetic'}):
        saved = policy.configure(expected_revision=0, mode='assisted', terminal_mode='sandbox')
        updated = policy.configure(expected_revision=saved['revision'], mode='autonomous')
    assert updated['terminal_mode'] == 'sandbox'


def test_new_autonomous_tool_executes_without_confirmation_old_pending_is_preserved(tmp_path):
    from app.agent_tool_service import ToolExecutionContext, execute_tool_call
    _, project, linked = inherited_project(tmp_path)
    config = code.configuration(project['id'])
    shell = 'powershell' if 'powershell' in terminal.shells() else 'bash'
    command = "Write-Output 'default-run'" if shell == 'powershell' else "printf 'default-run'"
    arguments = dict(command=command, shell=shell, workspace_revision=config['revision'])
    first = ToolExecutionContext('defaults_pending', 'defaults_pending_req', '', linked.conversation_id, 0, 'synthetic', 0)
    second = ToolExecutionContext('defaults_direct', 'defaults_direct_req', '', linked.conversation_id, 0, 'synthetic', 0)
    for context in (first, second):
        db.create_agent_run(context.run_id, context.request_id, conversation_id=context.conversation_id, source='desktop')
    policy.configure(expected_revision=0, mode='assisted')
    async def run():
        pending = await execute_tool_call('terminal_start', arguments, first)
        assert pending.status == 'needs_confirmation'
        policy.configure(expected_revision=1, mode='autonomous')
        dispatched = await execute_tool_call('terminal_start', arguments, second)
        assert dispatched.status == 'completed', dispatched.error
        result=await completed(second,dispatched.result['job_id'])
        assert result['status'] == 'completed' and 'default-run' in result['stdout']
        with closing(db.get_conn()) as conn:
            old = conn.execute('SELECT status FROM companion_actions WHERE id=?', (pending.action_id,)).fetchone()
        assert old['status'] == 'needs_confirmation'
    asyncio.run(run())
