import json
import os
import tempfile
from types import SimpleNamespace

import pytest

os.environ.setdefault('MIO_DISABLE_DOTENV', '1')
os.environ.setdefault('MIO_RUNTIME_ROOT', tempfile.mkdtemp(prefix='mio-output-test-'))

from app import audio_output_devices as audio


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, 'settings', SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(audio, '_enumerate_outputs', lambda: [
        {'id': 'waveout:speaker', 'label': '扬声器', 'index': 4},
        {'id': 'waveout:headset', 'label': '耳机', 'index': 2}])
    return tmp_path


def test_default_does_not_enumerate_or_require_configuration(outputs):
    assert audio.resolve_output_device() == 0xFFFFFFFF


def test_selection_uses_current_index_after_device_reordering(outputs, monkeypatch):
    assert audio.select_output('waveout:headset')['output_device_id'] == 'waveout:headset'
    assert audio.resolve_output_device() == 2
    monkeypatch.setattr(audio, '_enumerate_outputs', lambda: [{'id': 'waveout:headset', 'label': '耳机', 'index': 6}])
    assert audio.resolve_output_device() == 6
    assert json.loads((outputs/'audio-output.json').read_text()) == {'output_device_id': 'waveout:headset'}


def test_disconnected_device_requires_explicit_choice(outputs, monkeypatch):
    audio.select_output('waveout:speaker')
    monkeypatch.setattr(audio, '_enumerate_outputs', lambda: [])
    assert audio.status()['selection_missing']
    with pytest.raises(RuntimeError, match='已断开'):
        audio.resolve_output_device()
    audio.select_output('default')
    assert audio.resolve_output_device() == 0xFFFFFFFF


def test_bad_selection_does_not_overwrite_previous(outputs):
    audio.select_output('waveout:headset')
    with pytest.raises(ValueError):
        audio.select_output('../../file')
    assert audio.status()['output_device_id'] == 'waveout:headset'
