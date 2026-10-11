"""Real local Docker checks against synthetic data only; no paid services."""
import asyncio
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app import db,execution_policy_service as policy,terminal_sandbox as sandbox,terminal_service as terminal
from app import workspace_service as workspace,code_workspace_service as code,agent_task_service as tasks
from app.agent_tool_service import ToolExecutionContext
from test_runtime_safety_v2 import isolated


@pytest.fixture
def project(tmp_path):
    root=tmp_path/'project';root.mkdir()
    item=workspace.save_project('真实Docker合成验收')
    conversation='desktop_agent_docker_synthetic'
    db.create_agent_conversation(conversation);workspace.bind_conversation(item['id'],conversation)
    config=code.configure(item['id'],str(root),True,True,True,expected_revision=0)
    context=ToolExecutionContext('docker_run','docker_req','',conversation,0,'合成隔离测试',0)
    return root,config,context


@pytest.fixture
def real_docker(project):
    info=sandbox.status(refresh=True)
    if not info['available']:
        pytest.skip(info['message'])
    policy.configure(expected_revision=0,terminal_mode='sandbox',network_enabled=False)
    yield project
    for identifier in list(sandbox._active):
        sandbox.remove(identifier)


async def completed(context,job_id):
    for _ in range(150):
        if job_id not in terminal._running:
            return terminal.read(context,job_id)
        await asyncio.sleep(.1)
    raise AssertionError('Synthetic Docker job did not stop')


def test_real_shell_copy_network_and_host_secret_absence(real_docker,monkeypatch):
    root,config,context=real_docker
    (root/'input.txt').write_text('synthetic input\n',encoding='utf-8')
    (root/'.env').write_text('secret fixture')
    (root/'private.key').write_text('secret fixture')
    (root/'credentials.json').write_text('secret fixture')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-host-key')
    command="""python -c 'import os,socket,pathlib; assert not os.environ.get("OPENAI_API_KEY"); assert not pathlib.Path(".env").exists(); assert not pathlib.Path("private.key").exists(); assert not pathlib.Path("credentials.json").exists(); s=socket.socket();s.settimeout(1);assert s.connect_ex(("1.1.1.1",443)) != 0;pathlib.Path("output.txt").write_text(pathlib.Path("input.txt").read_text()+"processed\\n");print("NETWORK_BLOCKED_SECRET_ABSENT")'"""
    async def run():
        started=await terminal.start(context,command,'bash',config['revision'])
        name=sandbox._active[started['job_id']]['name']
        result=await completed(context,started['job_id'])
        assert result['status']=='completed',result
        assert 'NETWORK_BLOCKED_SECRET_ABSENT' in result['stdout']
        assert result['evidence']['sandbox_result']['published']
        assert (root/'output.txt').read_text()=='synthetic input\nprocessed\n'
        assert (root/'.env').read_text()=='secret fixture'
        check=sandbox._run(['docker','inspect',name]);assert check.returncode!=0
        assert started['job_id'] not in sandbox._active
    asyncio.run(run())


def test_real_shell_cas_does_not_overwrite_owner_edit(real_docker):
    root,config,context=real_docker
    target=root/'input.txt';target.write_text('baseline\n')
    async def run():
        started=await terminal.start(context,"sleep 1; printf 'container edit\\n' > input.txt",'bash',config['revision'])
        target.write_text('owner edit\n')
        result=await completed(context,started['job_id'])
        assert result['status']=='failed' and '未覆盖' in result['error'],result
        assert target.read_text()=='owner edit\n'
        assert started['job_id'] not in sandbox._active
    asyncio.run(run())


def test_real_policy_revocation_and_stop_cleanup_containers(real_docker):
    root,config,context=real_docker
    async def run():
        started=await terminal.start(context,"sleep 15; printf 'not allowed' > revoked.txt",'bash',config['revision'])
        policy.configure(expected_revision=1,mode='read_only')
        result=await completed(context,started['job_id'])
        assert result['status']=='cancelled',result
        assert not (root/'revoked.txt').exists() and started['job_id'] not in sandbox._active
        policy.configure(expected_revision=2,mode='assisted')
        started=await terminal.start(context,'sleep 15','bash',config['revision'])
        stopped=await asyncio.to_thread(terminal.stop,context,started['job_id'])
        assert stopped['status']=='cancelled'
        result=await completed(context,started['job_id'])
        assert result['status']=='cancelled' and started['job_id'] not in sandbox._active
    asyncio.run(run())


def test_prepare_failure_removes_only_new_stage_and_empty_receipt(project,monkeypatch,tmp_path):
    root,config,context=project
    monkeypatch.setattr(sandbox,'status',lambda *a,**k:{'available':True,'image_id':'sha256:synthetic'})
    policy.configure(expected_revision=0,terminal_mode='sandbox')
    def fail_copy(target):
        yield Path('first.txt'),root/'first.txt'
        raise ValueError('synthetic oversized project')
    (root/'first.txt').write_text('safe')
    monkeypatch.setattr(sandbox,'_files',fail_copy)
    with pytest.raises(ValueError,match='oversized'):
        asyncio.run(terminal.start(context,'echo safe','bash',config['revision']))
    receipts=db.settings.db_path.parent/'终端回执'
    assert not list(receipts.iterdir())
    assert (root/'first.txt').read_text()=='safe'
    assert not sandbox._active


@pytest.mark.parametrize('docker_available', [True, False])
def test_container_remove_failure_is_unknown_and_retains_retry_record(project,monkeypatch,tmp_path,docker_available):
    _,_,context=project
    identifier='terminal_cleanup_fixture'
    sandbox._active[identifier]={'name':'mio-cleanup-fixture'}
    monkeypatch.setattr(sandbox.shutil,'which',lambda name:'synthetic-docker' if docker_available else None)
    monkeypatch.setattr(sandbox,'_run',lambda *a,**k:SimpleNamespace(returncode=1,stdout='',stderr='synthetic engine unavailable'))
    with pytest.raises(sandbox.SandboxCleanupError,match='无法确认'):
        sandbox.remove(identifier)
    assert identifier in sandbox._active
    sandbox._active.pop(identifier)


def test_watcher_cancelled_removes_real_container(real_docker):
    _,config,context=real_docker
    async def run():
        started=await terminal.start(context,'sleep 15','bash',config['revision'])
        candidates=[worker for worker in terminal._workers if worker.get_coro().cr_frame.f_locals.get('job_id')==started['job_id']]
        assert len(candidates)==1
        candidates[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await candidates[0]
        result=await completed(context,started['job_id'])
        assert result['status']=='cancelled'
        assert started['job_id'] not in sandbox._active and started['job_id'] not in terminal._running
    asyncio.run(run())


def test_real_container_remove_failure_not_reported_as_cancelled(real_docker,monkeypatch):
    _,config,context=real_docker
    async def run():
        started=await terminal.start(context,'sleep 15','bash',config['revision'])
        name=sandbox._active[started['job_id']]['name']
        original=sandbox._run
        def fail_remove(argv,**kwargs):
            if 'rm' in argv:
                return SimpleNamespace(returncode=1,stdout='',stderr='synthetic unavailable for rm')
            return original(argv,**kwargs)
        with monkeypatch.context() as mock:
            mock.setattr(sandbox,'_run',fail_remove)
            result=terminal.stop(context,started['job_id'])
            assert result['status']=='unknown' and '无法确认' in result['error']
            finished=await completed(context,started['job_id'])
            assert finished['status']=='unknown'
            assert started['job_id'] in sandbox._active
        sandbox.remove(started['job_id'])
        assert original(['docker','inspect',name]).returncode!=0
    asyncio.run(run())


def test_preparation_cancel_waits_for_copy_and_cleans_owned_receipt(project,monkeypatch):
    root,config,context=project
    original=sandbox.prepare
    started=threading.Event();release=threading.Event()
    monkeypatch.setattr(sandbox,'status',lambda *a,**k:{'available':True,'image_id':'sha256:synthetic'})
    original_which=sandbox.shutil.which
    monkeypatch.setattr(sandbox.shutil,'which',lambda name:'synthetic-docker' if name=='docker' else original_which(name))
    removals=[]
    def absent_container(argv,**kwargs):
        # prepare only builds the invocation; cancellation happens before Popen.
        assert argv[:3]==['synthetic-docker','rm','-f']
        removals.append(argv)
        return SimpleNamespace(returncode=1,stdout='',stderr='Error: No such container')
    monkeypatch.setattr(sandbox,'_run',absent_container)
    def unexpected_spawn(*args,**kwargs):
        pytest.fail('Cancelling sandbox preparation must not launch a process')
    monkeypatch.setattr(terminal.subprocess,'Popen',unexpected_spawn)
    policy.configure(expected_revision=0,terminal_mode='sandbox')
    def delayed(*args,**kwargs):
        started.set();release.wait(2)
        return original(*args,**kwargs)
    monkeypatch.setattr(sandbox,'prepare',delayed)
    (root/'input.txt').write_text('safe')
    async def run():
        job=asyncio.create_task(terminal.start(context,'echo safe','bash',config['revision']))
        assert await asyncio.to_thread(started.wait,2)
        job.cancel();release.set()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert not sandbox._active
        assert not list((db.settings.db_path.parent/'终端回执').iterdir())
        assert len(removals)==1 and removals[0][3].startswith('mio-terminal-')
        assert (root/'input.txt').read_text()=='safe'
    asyncio.run(run())


def test_verify_selects_container_shell_and_records_real_validation(real_docker):
    root,config,context=real_docker
    config=code.configure(config['project_id'],str(root),True,True,True,
                          verification={'test':'python -c "print(41+1)"'},expected_revision=config['revision'])
    async def run():
        started=await terminal.verify(context,'test',config['revision'])
        result=await completed(context,started['job_id'])
        assert result['status']=='completed' and result['stdout'].strip()=='42'
        assert result['shell']=='bash' and result['evidence']['kind']=='test'
    asyncio.run(run())
