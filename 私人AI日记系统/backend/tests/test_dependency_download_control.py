"""Control requests are scoped to live installers and their advertised stages."""
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault('MIO_DISABLE_DOTENV', '1')
os.environ.setdefault('MIO_RUNTIME_ROOT', tempfile.mkdtemp(prefix='mio-download-test-'))

from app import dependency_installer as installer
from app.dependency_download_control import control_download


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, 'settings', SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(installer, '_install_running', lambda _: True)
    installer._write_status('whisper', {'install_id': 'current', 'stage': 'downloading', 'can_pause': True, 'can_cancel': True})
    return tmp_path


def test_writes_cooperative_command_with_current_identity(state):
    result = control_download('whisper', 'pause')
    assert result['accepted']
    assert json.loads(installer._status_path('whisper').with_suffix('.control.json').read_text()) == {'install_id': 'current', 'action': 'pause'}


def test_stage_and_stale_process_never_receive_control(state, monkeypatch):
    with pytest.raises(ValueError, match='当前阶段'):
        control_download('whisper', 'resume')
    monkeypatch.setattr(installer, '_install_running', lambda _: False)
    with pytest.raises(ValueError, match='当前阶段'):
        control_download('whisper', 'cancel')
    assert not installer._status_path('whisper').with_suffix('.control.json').exists()


def test_resume_ended_install_reuses_normal_install_checks(state, monkeypatch):
    installer._write_status('whisper', {'stage': 'cancelled', 'done': True})
    monkeypatch.setattr(installer, '_install_running', lambda _: False)
    start = Mock(return_value={'installing': True})
    monkeypatch.setattr(installer, '_install_dependency', start)
    assert control_download('whisper', 'resume') == {'installing': True}
    start.assert_called_once_with('whisper')


def test_cancelled_install_does_not_finalize_or_report_success(state, monkeypatch):
    installer._write_status('whisper', {'stage': 'cancelled', 'done': True})
    finalize = Mock()
    monkeypatch.setattr(installer, '_ensure_install_finalized', finalize)
    monkeypatch.setattr(installer.environment_check_service, 'refresh_detection_cache', Mock())
    process = Mock(pid=123); process.wait.return_value = 1
    installer._watch_install('whisper', process)
    finalize.assert_not_called()
    assert installer._read_status('whisper')['stage'] == 'cancelled'


def test_payload_hides_live_controls_after_process_exit():
    result = installer._progress_payload({'can_pause': True, 'can_cancel': True, 'stage': 'cancelled'}, installing=False)
    assert not result['can_pause'] and not result['can_cancel'] and result['can_resume']


def test_installed_component_repair_keeps_download_controls(state, monkeypatch):
    from app import dependency_removal
    monkeypatch.setattr(installer, '_DEPENDENCY_DEFS', [{'id': 'whisper'}])
    monkeypatch.setattr(installer, '_install_path', lambda _: '')
    monkeypatch.setattr(dependency_removal, 'availability', lambda _: {'can_uninstall': False})
    monkeypatch.setattr(installer.environment_check_service, 'environment_status', lambda **_: {})
    monkeypatch.setattr(installer, '_detect_status', lambda *args: {'status': 'ready', 'detail': ''})
    entries = installer.list_dependencies()
    whisper = next(item for item in entries if item['id'] == 'whisper')
    assert whisper['installing'] and whisper['progress']['can_pause']


@pytest.mark.parametrize('dep,action', [('unknown', 'pause'), ('../whisper', 'cancel'), ('whisper', 'kill')])
def test_rejects_unknown_controls(dep, action):
    with pytest.raises(ValueError):
        control_download(dep, action)
