"""Regression boundaries only; mocks do not prove native desktop input."""
import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app import desktop_executor as worker, desktop_control_service as desktop
from test_runtime_safety_v2 import isolated


@pytest.mark.skipif(os.name != 'nt', reason='Windows INPUT layout')
def test_literal_input_keeps_punctuation_unicode_and_newline_order():
    from pywinauto import keyboard, win32functions
    text = 'English、{中文} + ^ % ~ (literal)\r\n下一行\n😀\t结束'
    received = []

    def capture(count, array, size):
        received.extend((event.ki.wVk, event.ki.wScan, event.ki.dwFlags) for event in array._obj)
        return count

    with patch.object(keyboard, 'parse_keys', side_effect=AssertionError('Literal text must not use shortcut parsing')), \
         patch.object(win32functions, 'SendInput', side_effect=capture), patch.object(worker.time, 'sleep'):
        worker._send_keys(text, literal=True)
    assert received and len(received) % 2 == 0
    units = []
    for down, up in zip(received[::2], received[1::2]):
        if down[0] == 13:
            assert up[0] == 13 and not down[2] & 2 and up[2] & 2
            units.append(b'\n\x00')
        else:
            assert down[0] == up[0] == 0
            assert down[1] == up[1] and down[2] == 4 and up[2] == 6
            units.append(down[1].to_bytes(2, 'little'))
    assert b''.join(units).decode('utf-16-le') == text.replace('\r\n', '\n')
    assert not worker._owned_keys


def test_uia_surrogates_are_normalized_before_truncation_or_database_write():
    assert worker._uia_text('前\ud83d\ude00后') == '前😀后'
    assert worker._uia_text('坏\ud83d结尾') == '坏\ufffd结尾'
    assert worker._uia_text('字' * 299 + '\ud83d\ude00后')[:300] == '字' * 299 + '😀'


@pytest.mark.skipif(os.name != 'nt', reason='Windows INPUT layout')
def test_literal_focus_loss_after_one_batch_preserves_partial_input_fact():
    from pywinauto import win32functions
    with patch.object(worker, '_assert_foreground', side_effect=[None, ValueError('焦点改变')]), \
         patch.object(win32functions, 'SendInput', side_effect=lambda count, *_: count) as send, \
         patch.object(worker.time, 'sleep'):
        with pytest.raises(worker.InputDeliveryError) as exc:
            worker._send_keys('a' * 65, literal=True, identity=launched_identity())
    assert exc.value.input_sent and send.call_count == 1 and not worker._owned_keys


@pytest.mark.skipif(os.name != 'nt', reason='Windows INPUT layout')
def test_literal_long_text_rejected_before_any_input():
    from pywinauto import win32functions
    with patch.object(win32functions, 'SendInput') as send:
        with pytest.raises(ValueError, match='最多1000'):
            worker._send_keys('字' * 1001, literal=True)
    send.assert_not_called()


def test_stop_is_reexecuted_after_worker_restart_in_same_task():
    from app.tool_registry import tool_registry
    assert not tool_registry.require('desktop_stop').idempotent


@pytest.mark.parametrize('sent', [False, True])
def test_partial_keyboard_failure_retains_input_fact_and_never_retries(sent):
    old = launched_identity()
    rect = SimpleNamespace(left=0, top=0, right=800, bottom=600)
    previous = {'id':'observation-test', 'identity':old, 'time':worker.time.monotonic(),
                'wrappers':{}, 'rect':(0,0,800,600)}
    with patch.dict(worker._observations, {'task-test':previous}, clear=True), \
         patch.object(worker, '_window', return_value=SimpleNamespace(rectangle=lambda:rect)), \
         patch.object(worker, '_identity', return_value=old), patch.object(worker, '_focus_window'), \
         patch.object(worker.ctypes, 'WinDLL', return_value=SimpleNamespace(GetAsyncKeyState=lambda _:0), create=True), \
         patch.object(worker, '_send_keys', side_effect=worker.InputDeliveryError('partial', sent)) as send, \
         patch.object(worker, 'release_inputs') as release, patch.object(worker, 'observe') as observe:
        if sent:
            result = worker.act(old, 'task-test', 'observation-test', 'type', text='synthetic')
            assert result['input_sent'] and result['observation_pending'] and not result['automatic_retry_safe']
        else:
            with pytest.raises(worker.InputDeliveryError):
                worker.act(old, 'task-test', 'observation-test', 'type', text='synthetic')
        assert 'task-test' not in worker._observations
    send.assert_called_once()
    release.assert_called_once()
    observe.assert_not_called()


def launched_identity(pid=20, created='200', title='synthetic.txt - Notepad'):
    return {'hwnd': 101, 'pid': pid, 'created': created, 'title': title}


@pytest.mark.parametrize('candidates,expected_pid', [
    ([launched_identity()], 20),
    ([launched_identity(30, '90'), launched_identity()], 20),
    ([launched_identity(10, '999')], None),
    ([], None),
])
def test_launch_child_ownership_and_no_automatic_retry(candidates, expected_pid):
    kernel = SimpleNamespace(GetSystemTimeAsFileTime=lambda ptr: setattr(ptr._obj, 'value', 100))
    with patch.object(worker.ctypes, 'WinDLL', return_value=kernel, create=True), \
         patch('subprocess.Popen', return_value=SimpleNamespace(pid=10)) as start, \
         patch.object(worker, '_process_created', return_value='150'), \
         patch.object(worker, '_launched_processes', return_value={10, 20}) as tree, \
         patch.object(worker, '_launched_windows', return_value=candidates), \
         patch.object(worker, '_desktop') as uia:
        result = worker.launch('notepad.exe', ['synthetic.txt'], timeout=0)
    start.assert_called_once_with(['notepad.exe', 'synthetic.txt'])
    tree.assert_called_once_with(10, 100, '150')
    uia.assert_not_called()
    assert result['launched'] and result['process_pid'] == 10
    if expected_pid:
        assert result['window']['pid'] == expected_pid
    else:
        assert result['window'] is None
        assert not result['automatic_retry_safe']
        assert result['launch_state'] == 'window_authorization_required'


@pytest.mark.parametrize('stage', ['before_focus', 'during_focus'])
def test_changed_tab_rejects_input_before_any_keyboard_or_mouse(stage):
    old = launched_identity()
    changed = {**old, 'title': 'other-document.txt - Notepad'}
    rect = SimpleNamespace(left=0, top=0, right=800, bottom=600)
    previous = {'id': 'observation-test', 'identity': old, 'time': worker.time.monotonic(),
                'wrappers': {}, 'rect': (0, 0, 800, 600)}
    identities = [changed] if stage == 'before_focus' else [old, changed]
    with patch.dict(worker._observations, {'task-test': previous}, clear=True), \
         patch.object(worker, '_window', return_value=SimpleNamespace(rectangle=lambda: rect)), \
         patch.object(worker, '_identity', side_effect=identities), \
         patch.object(worker, '_focus_window') as focus, \
         patch.object(worker, '_send_keys') as keys, \
         patch('pywinauto.mouse.click') as click:
        with pytest.raises(ValueError, match='标签已改变'):
            worker.act(old, 'task-test', 'observation-test', 'type', text='synthetic only')
    keys.assert_not_called()
    click.assert_not_called()
    assert focus.call_count == (stage == 'during_focus')


@pytest.mark.parametrize('failure', ['discovery', 'permission_changed', 'authorization', 'observation'])
def test_service_preserves_launch_fact_after_later_failures(tmp_path, failure):
    executable = tmp_path / 'synthetic.exe'
    executable.write_bytes(b'not executed')
    ctx = SimpleNamespace(conversation_id='desktop_agent_test', task_id='task-test', run_id='run-test')
    config = {'project_id': 'project-test', 'revision': 2, 'windows': [], 'executables': [str(executable)]}
    returned = {'launched': True, 'process_pid': 10,
                'window': None if failure == 'discovery' else launched_identity()}
    contexts = [config, ValueError('changed')] if failure == 'permission_changed' else [config, config]
    configure = Mock(side_effect=ValueError('authorization failed')) if failure == 'authorization' else Mock(return_value={**config, 'revision': 3})
    with patch.object(desktop, 'project_context', side_effect=contexts), \
         patch.object(desktop, 'rpc', return_value=returned) as rpc, \
         patch.object(desktop, 'configure', configure), \
         patch.object(desktop, 'observe', AsyncMock(side_effect=ValueError('foreground unavailable'))) as observe, \
         patch.dict(desktop._latest, {desktop._key(ctx): {'old': True}}, clear=True):
        result = asyncio.run(desktop.launch(ctx, str(executable), ['synthetic.txt'], 2))
        assert desktop._key(ctx) not in desktop._latest
    rpc.assert_called_once()
    assert result['launched'] and result['process_pid'] == 10
    if failure == 'discovery':
        configure.assert_not_called()
        observe.assert_not_called()
    else:
        assert result['observation_pending'] and not result['automatic_retry_safe']
        assert result['window_authorized'] == (failure == 'observation')
        assert result['permission_revision'] == (3 if failure == 'observation' else 2)


def test_service_success_retains_observation_and_launch_identity(tmp_path):
    executable = tmp_path / 'synthetic.exe'
    executable.write_bytes(b'not executed')
    ctx = SimpleNamespace(conversation_id='desktop_agent_test', task_id='task-test', run_id='run-test')
    config = {'project_id': 'project-test', 'revision': 2, 'windows': [], 'executables': [str(executable)]}
    with patch.object(desktop, 'project_context', return_value=config), \
         patch.object(desktop, 'rpc', return_value={'launched': True, 'process_pid': 10, 'window': launched_identity()}), \
         patch.object(desktop, 'configure', return_value={**config, 'revision': 3}), \
         patch.object(desktop, 'observe', AsyncMock(return_value={'observation_id': 'fresh', 'permission_revision': 3})):
        result = asyncio.run(desktop.launch(ctx, str(executable), [], 2))
    assert result == {'observation_id': 'fresh', 'permission_revision': 3, 'launched': True, 'process_pid': 10}


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows process identity')
def test_real_process_tree_rejects_recycled_root_and_old_descendants():
    root_created = worker._process_created(os.getpid())
    # A plain Python child, no application UI and no custom input helper.
    with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)']) as child:
        try:
            child_created = int(worker._process_created(child.pid))
            assert child.pid in worker._launched_processes(os.getpid(), 0, root_created)
            assert child.pid not in worker._launched_processes(os.getpid(), child_created + 1, root_created)
            assert not worker._launched_processes(os.getpid(), 0, str(int(root_created) + 1))
        finally:
            child.terminate()
            child.wait(timeout=5)


def test_worker_input_receipt_survives_post_input_observation_failure():
    old = launched_identity()
    rect = SimpleNamespace(left=0, top=0, right=800, bottom=600)
    previous = {'id': 'observation-test', 'identity': old, 'time': worker.time.monotonic(),
                'wrappers': {}, 'rect': (0, 0, 800, 600)}
    user32 = SimpleNamespace(GetAsyncKeyState=lambda key: 0)
    with patch.dict(worker._observations, {'task-test': previous}, clear=True), \
         patch.object(worker, '_window', return_value=SimpleNamespace(rectangle=lambda: rect)), \
         patch.object(worker, '_identity', return_value=old), \
         patch.object(worker, '_focus_window'), \
         patch.object(worker.ctypes, 'WinDLL', return_value=user32, create=True), \
         patch.object(worker, '_send_keys') as send, \
         patch.object(worker, 'release_inputs') as release, \
         patch.object(worker, 'observe', side_effect=ValueError('foreground unavailable')):
        result = worker.act(old, 'task-test', 'observation-test', 'type', text='synthetic only')
        assert 'task-test' not in worker._observations
    send.assert_called_once()
    release.assert_called_once()
    assert result['input_sent'] and result['observation_pending']
    assert not result['automatic_retry_safe'] and result['operation'] == 'type'


@pytest.mark.parametrize('changed_permission', [False, True])
def test_service_keeps_input_fact_and_hides_stale_image(changed_permission):
    ctx = SimpleNamespace(conversation_id='desktop_agent_test', task_id='task-test', run_id='run-test')
    config = {'revision': 2, 'windows': [launched_identity()]}
    row = {'conversation_id': ctx.conversation_id, 'task_id': ctx.task_id, 'permission_revision': 2}
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = row
    sent = {'input_sent': True, 'operation': 'type', 'observation_pending': True,
            'automatic_retry_safe': False, 'screenshot_base64': 'must not be saved'}
    contexts = [config, ValueError('revoked')] if changed_permission else [config, config]
    with patch.object(desktop, 'project_context', side_effect=contexts), \
         patch('app.execution_policy_service.configuration', return_value={'mode':'assisted'}), \
         patch.object(desktop.db, 'get_conn', return_value=conn), \
         patch.object(desktop, 'rpc', return_value=sent), \
         patch.object(desktop, '_save') as save, \
         patch.dict(desktop._latest, {desktop._key(ctx): {'old': True}}, clear=True):
        result = asyncio.run(desktop.act(ctx, 101, 'observed', 2, 'type', text='synthetic only'))
        assert desktop._key(ctx) not in desktop._latest
    save.assert_not_called()
    conn.close.assert_called_once()
    assert result['input_sent'] and result['observation_pending']
    assert not result['automatic_retry_safe']
    if changed_permission:
        assert 'screenshot_base64' not in result
