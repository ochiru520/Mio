"""IndexTTS2 integration using an actual loopback HTTP bridge and synthetic WAV.

No IndexTTS, torch, checkpoints, real references or personal data required.
"""
import asyncio
import base64
from contextlib import contextmanager
import importlib.util
from io import BytesIO
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
import wave

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import companion_service as companion, indextts2_service as service
from app.config import settings
from app.routes.companion import router


def wav():
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x10\x01\xf0\xfe" * 1200)
    return output.getvalue()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    previous = {key: getattr(settings, key) for key in ("data_dir", "companion_dir", "companion_config_path")}
    for key, value in {"data_dir": tmp_path, "companion_dir": tmp_path / "companion", "companion_config_path": tmp_path / "companion.json"}.items():
        object.__setattr__(settings, key, value)
    monkeypatch.setattr(companion, "_gpt_sovits_last_error", "")
    monkeypatch.setattr(companion, "_live2d_model_ids", lambda: {"hiyori"})
    yield
    for key, value in previous.items():
        object.__setattr__(settings, key, value)


@pytest.fixture
def adapter():
    source = Path(__file__).resolve().parents[3] / "澪Agent应用/scripts/indextts2_adapter.py"
    spec = importlib.util.spec_from_file_location("mio_indextts2_test_bridge", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def serve(adapter, model):
    server = adapter.HTTPServer(("127.0.0.1", 0), adapter.handler_for(model))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_real_http_bridge_reference_bytes_and_output(tmp_path, adapter):
    reference = tmp_path / "合成参考.wav"
    reference.write_bytes(wav())
    calls = []
    def infer(**kwargs):
        calls.append((kwargs, Path(kwargs["spk_audio_prompt"]).read_bytes()))
        Path(kwargs["output_path"]).write_bytes(wav())
    with serve(adapter, SimpleNamespace(infer=infer)) as url:
        config = {"indextts2_url": url, "indextts2_ref_audio": str(reference)}
        status = service.runtime_status(config)
        assert status["service_running"] and status["reference_ready"]
        assert service.synthesize_wav("你好，这是隔离语音测试。", config) == wav()
    assert len(calls) == 1
    kwargs, received = calls[0]
    assert kwargs["text"] == "你好，这是隔离语音测试。"
    assert received == reference.read_bytes()
    assert not Path(kwargs["output_path"]).exists()
    assert not Path(kwargs["spk_audio_prompt"]).exists()


def test_bridge_errors_and_no_private_error_echo(adapter):
    model = Mock()
    model.infer.side_effect = RuntimeError("synthetic-private-text-and-path")
    valid = {"protocol": service.PROTOCOL, "text": "你好", "reference_name": "ref.wav",
             "reference_audio_base64": base64.b64encode(wav()).decode()}
    with serve(adapter, model) as url, httpx.Client(trust_env=False) as client:
        assert client.post(url + "/synthesize", json={}).status_code == 400
        assert client.post(url + "/synthesize", json={**valid, "reference_audio_base64": "bad!"}).status_code == 400
        assert client.get(url + "/unknown").status_code == 404
        failure = client.post(url + "/synthesize", json=valid)
        assert failure.status_code == 500 and "RuntimeError" in failure.text
        assert "synthetic-private" not in failure.text


def test_client_rejects_missing_reference_wrong_api_and_invalid_wav(tmp_path):
    config = {"indextts2_url": service.DEFAULT_URL, "indextts2_ref_audio": ""}
    with pytest.raises(ValueError, match="参考音频"):
        service.synthesize_wav("你好", config)
    reference = tmp_path / "ref.wav"; reference.write_bytes(wav())
    config["indextts2_ref_audio"] = str(reference)
    for response, expected in [(httpx.Response(200, text="<html>webui</html>"), "非音频"),
                               (httpx.Response(200, content=b"not-wav", headers={"content-type": "audio/wav"}), "PCM WAV"),
                               (httpx.Response(500, json={"detail": "synthetic failure"}), "synthetic failure")]:
        with patch.object(service.httpx, "Client") as client:
            client.return_value.__enter__.return_value.stream.return_value.__enter__.return_value = response
            with pytest.raises(OSError, match=expected):
                service.synthesize_wav("你好", config)
    with pytest.raises(ValueError, match="HTTP"):
        service.service_url({"indextts2_url": "file:///private"})


def test_config_save_upload_route_and_synthesis_entrypoints(tmp_path):
    app = FastAPI(); app.include_router(router)
    # Route response usually includes unrelated observations. Keep those isolated.
    with patch("app.routes.companion._status_payload", side_effect=lambda: {"settings": companion.load_config()}), TestClient(app) as client:
        saved = client.patch("/api/companion/settings", json={"voice_engine": "indextts2", "indextts2_url": "http://127.0.0.1:11996/"})
        assert saved.status_code == 200
        assert companion.load_config()["voice_engine"] == "indextts2"
        assert companion.load_config()["indextts2_url"] == service.DEFAULT_URL
        data = "data:audio/wav;base64," + base64.b64encode(wav()).decode()
        uploaded = client.post("/api/companion/voice/reference", json={"name": "ref.wav", "data_url": data})
        assert uploaded.status_code == 200
        config = companion.load_config()
        assert Path(config["indextts2_ref_audio"]).read_bytes() == wav()
        assert Path(config["indextts2_ref_audio"]).is_relative_to(tmp_path)
    with patch.object(service, "synthesize_wav", return_value=wav()) as synth, patch.object(companion, "_postprocess_speech_wav", side_effect=lambda content, _: content), patch.object(companion, "_ensure_gpt_sovits_service") as legacy:
        assert companion.synthesize_speech_wav("你好") == wav()
        assert b"".join(companion.iter_speech_wav_stream("你好")) == wav()
        with TestClient(app) as client:
            for endpoint in ("audio", "stream"):
                audio = client.post(f"/api/companion/voice/{endpoint}", json={"text": "你好"})
                assert audio.status_code == 200
                assert audio.headers["content-type"].startswith("audio/wav")
                assert audio.content == wav()
        result = {}
        fake_winsound = SimpleNamespace(PlaySound=Mock(), SND_MEMORY=4, SND_NODEFAULT=2)
        with patch.dict(sys.modules, {"winsound": fake_winsound}):
            companion._speak_wav_worker_impl("你好", companion._speech_generation, "", result, streaming=True)
        assert result["played"] is True
        fake_winsound.PlaySound.assert_called_once()
        assert synth.call_count == 5
        legacy.assert_not_called()
    with patch.object(companion.threading, "Thread") as thread:
        assert companion.warm_voice_language_async("zh") is False
        assert companion.warm_voice_runtime_async() is False
        assert companion.warm_voice_runtime()["warmup_state"] == "idle"
        assert asyncio.run(companion.start_voice_on_app_startup()) is False
        thread.assert_not_called()
    assert companion.voice_runtime_health()["runtime"] == "indextts2"
    assert companion.voice_runtime_health()["desired_running"] is False
    for control in (companion.start_voice_service, companion.stop_voice_service, companion.restart_voice_service):
        with pytest.raises(OSError, match="IndexTTS2"):
            control()
    # Existing defaults/providers remain available.
    for engine in ("cloud", "gpt_sovits"):
        assert companion.save_config({"voice_engine": engine})["voice_engine"] == engine


def test_reference_validation_and_default_opt_in(tmp_path):
    assert companion.load_config()["voice_engine"] == "gpt_sovits"
    assert companion.load_config()["indextts2_ref_audio"] == ""
    for data, name in [("invalid", "test.wav"), ("data:audio/wav;base64,", "test.wav"), ("data:audio/wav;base64,YQ==", "test.exe")]:
        with pytest.raises(ValueError):
            service.save_reference(data, name, tmp_path)
    with pytest.raises(OSError, match="PCM WAV"):
        service.validate_wav(wav()[:-8])
