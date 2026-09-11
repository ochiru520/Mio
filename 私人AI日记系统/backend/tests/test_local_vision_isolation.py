from __future__ import annotations

import json
import os
import subprocess
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from app import dependency_installer, environment_check_service
from app import local_vision_service as vision


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "settings", SimpleNamespace(local_vision_dir=tmp_path))
    for key, value in {"_server_process": None, "_pull_process": None, "_server_url": "", "_last_error": "",
                       "_probe_at": 0, "_probe_cache": {}, "_inference_probe_at": 0,
                       "_inference_probe_result": {}, "_server_desired_running": False}.items():
        monkeypatch.setattr(vision, key, value)
    exe = vision._ollama_executable()
    exe.parent.mkdir()
    exe.write_bytes(b"test runtime")
    manifest = vision._model_manifest_path()
    manifest.parent.mkdir(parents=True)
    blobs = vision._models_dir() / "blobs"
    blobs.mkdir()
    config = {"digest": "sha256:" + "a" * 64, "size": 3}
    layer = {"digest": "sha256:" + "b" * 64, "size": 5}
    (blobs / config["digest"].replace(":", "-")).write_bytes(b"abc")
    (blobs / layer["digest"].replace(":", "-")).write_bytes(b"12345")
    manifest.write_text(json.dumps({"config": config, "layers": [layer]}), encoding="utf-8")
    return tmp_path


def fake_server(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    process.terminate.side_effect = lambda: setattr(process.poll, "return_value", 0)
    process.wait.return_value = 0
    popen = Mock(return_value=process)
    monkeypatch.setattr(vision.subprocess, "Popen", popen)

    def request(path, **kwargs):
        vision._owned_server_url()
        if path == "/api/version":
            return {"version": "test"}
        if path == "/api/tags":
            return {"models": [{"name": vision.DEFAULT_MODEL}]}
        if path == "/api/generate":
            return {"response": "OK"}
        return {"models": []}

    monkeypatch.setattr(vision, "_request_json", request)
    return process, popen


def test_complete_download_without_running_server_is_installed(installed, monkeypatch):
    client = Mock(side_effect=AssertionError("Must not contact another Ollama"))
    monkeypatch.setattr(vision.httpx, "Client", client)
    current = vision.dependency_status()
    assert current["status"] == "installed"
    assert current["model_installed"] and current["runtime_installed"]
    assert not current["server_running"]
    vision.unload_model()
    vision.stop_server()
    client.assert_not_called()


def test_existing_files_do_not_launch_installer_again(installed, monkeypatch):
    popen = Mock(side_effect=AssertionError("Must not reinstall"))
    monkeypatch.setattr(dependency_installer.subprocess, "Popen", popen)
    result = dependency_installer.install_dependency("ollama_vision")
    assert result["installing"] is False
    popen.assert_not_called()


@pytest.mark.parametrize("content", [[], {}, {"config": {}, "layers": []}])
def test_malformed_manifest_is_missing_not_a_crash(installed, content):
    vision._model_manifest_path().write_text(json.dumps(content), encoding="utf-8")
    assert vision.dependency_status()["status"] == "missing"


def test_partial_blob_cannot_be_reported_installed(installed):
    blob = vision._models_dir() / "blobs" / ("sha256-" + "b" * 64)
    blob.write_bytes(b"123")
    assert vision.dependency_status()["status"] == "missing"


def test_user_ollama_environment_is_not_inherited(installed, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11434")
    monkeypatch.setenv("OLLAMA_MODELS", "external-models")
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "99")
    monkeypatch.setattr(vision, "_server_url", "http://127.0.0.1:34567")
    env = vision._runtime_env()
    assert env["OLLAMA_HOST"] == "127.0.0.1:34567"
    assert env["OLLAMA_MODELS"] == str(vision._models_dir())
    assert "OLLAMA_NUM_PARALLEL" not in env
    assert vision.os.environ["OLLAMA_MODELS"] == "external-models"


def test_occupied_port_gets_independent_service(installed, monkeypatch):
    process, popen = fake_server(monkeypatch)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        monkeypatch.setattr(vision, "DEFAULT_PORT", port)
        state = vision.start_server()
        assert state["base_url"] != f"http://127.0.0.1:{port}"
        assert state["base_url"] != "http://127.0.0.1:11434"
        assert state["owned_server"]
        args, kwargs = popen.call_args
        assert args[0] == [str(vision._ollama_executable()), "serve"]
        assert kwargs["env"]["OLLAMA_HOST"] == state["base_url"].removeprefix("http://")
        assert vision.activate()["status"] == "available"
        vision.stop_server()
        process.terminate.assert_called_once()
        assert occupied.getsockname()[1] == port
    assert vision.dependency_status()["status"] == "installed"


def test_concurrent_starts_create_one_owned_process(installed, monkeypatch):
    _, popen = fake_server(monkeypatch)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: vision.start_server(), range(4)))
    assert all(result["owned_server"] for result in results)
    popen.assert_called_once()


def test_failed_start_is_retryable_without_reinstall(installed, monkeypatch):
    process = Mock(returncode=1)
    process.poll.return_value = 1
    monkeypatch.setattr(vision.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(OSError, match="启动失败"):
        vision.start_server()
    assert vision.dependency_status()["status"] == "degraded"
    fake_server(monkeypatch)
    assert vision.activate()["status"] == "available"


def test_dead_service_cannot_reuse_successful_inference(installed, monkeypatch):
    monkeypatch.setattr(vision, "_inference_probe_result", {"ready": True, "state": "ready"})
    monkeypatch.setattr(vision, "_inference_probe_at", time.monotonic())
    assert vision.probe_inference()["ready"] is False
    assert vision.dependency_status()["status"] == "installed"


def test_inference_failure_keeps_files_installed_and_real_error(installed, monkeypatch):
    fake_server(monkeypatch)
    vision.start_server()
    original = vision._request_json

    def request(path, **kwargs):
        if path == "/api/generate":
            response = httpx.Response(500, json={"error": "out of memory"}, request=httpx.Request("POST", vision._server_url))
            response.raise_for_status()
        return original(path, **kwargs)

    monkeypatch.setattr(vision, "_request_json", request)
    result = vision.activate()
    assert result["status"] == "degraded"
    assert result["detail"] == "out of memory"
    assert result["model_installed"]


@pytest.mark.parametrize("state", ["installed", "unverified", "degraded"])
def test_dependency_center_preserves_runtime_state(installed, state):
    environment = {"optional": [{"id": "local_vision", "status": state, "detail": "specific reason"}]}
    result = dependency_installer._detect_status({"id": "ollama_vision", "kind": "script"}, environment)
    assert result["status"] == state
    assert result["detail"] == "specific reason"


def test_environment_check_never_starts_or_runs_inference(installed, monkeypatch):
    infer = Mock(side_effect=AssertionError("Read-only checks must not load a model"))
    monkeypatch.setattr(vision, "probe_inference", infer)
    monkeypatch.setattr(vision, "start_server", infer)
    checks = environment_check_service._optional_checks({"gpus": []})
    local = next(item for item in checks if item["id"] == "local_vision")
    assert local["status"] == "installed"
    assert "完整安装" in local["detail"]
    infer.assert_not_called()


def test_early_installer_exit_persists_error(installed, monkeypatch, tmp_path):
    vision._model_manifest_path().unlink()
    monkeypatch.setattr(dependency_installer, "settings", SimpleNamespace(data_dir=tmp_path))
    dependency_installer._write_status("ollama_vision", {"done": False, "stage": "starting"})
    result = dependency_installer._finalize_vision_download(1)
    assert result["done"] is True
    assert "退出码：1" in result["error"]
    assert "ollama_vision-install.log" in result["error"]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell installer")
def test_completed_files_make_installer_exit_successfully_without_launch(installed):
    script = Path(__file__).resolve().parents[3] / "澪Agent应用" / "scripts" / "deps" / "install-ollama-vision.ps1"
    status_file = installed / "install-status.json"
    env = {**os.environ, "MIO_LOCAL_VISION_DIR": str(installed), "MIO_STATUS_FILE": str(status_file), "OLLAMA_HOST": "127.0.0.1:11434"}
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                            env=env, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    progress = json.loads(status_file.read_text(encoding="utf-8-sig"))
    assert progress["done"] and not progress["error"]
    assert "启动并验证" in progress["message"]
