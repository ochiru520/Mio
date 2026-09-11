import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import artifact_service as artifacts, agent_file_service as files, creation_service as creation
from app import maintenance_service as maintenance
from app.config import settings
from app.creation_models import CreationJobRequest
from app.routes.artifacts import router
from test_runtime_safety_v2 import isolated


def test_document_versions_are_deduplicated_and_downloadable():
    first = files.write_document('report.md', 'version one', 'task_audit')
    repeated = files.write_document('report.md', 'version one', 'task_audit')
    second = files.write_document('report.md', 'version two', 'task_audit')
    assert first['artifact_id'] == repeated['artifact_id']
    assert first['version'] == 1 and second['version'] == 2
    assert first['artifact_id'] != second['artifact_id']
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        assert client.get(first['artifact_url']).text == 'version one'
        assert client.get(second['artifact_url']).text == 'version two'
        listed = client.get('/api/artifacts?task_id=task_audit&limit=1').json()
        assert listed['has_more'] and len(listed['artifacts']) == 1
    Path(first['path']).unlink()
    assert artifacts.file(first['artifact_id'])[0].read_text() == 'version one'


def test_comfy_output_survives_source_removal_and_installation_switch(tmp_path):
    first_root = tmp_path/'comfy-one'
    object.__setattr__(settings, 'comfyui_root', first_root)
    with patch.object(creation, 'schedule_job'):
        job, _ = creation.create_job(CreationJobRequest(prompt='synthetic'))
    source = first_root/'output'/'MioJobs'/job['id']/'first.png'
    source.parent.mkdir(parents=True)
    Image.new('RGBA', (16,16), (255,0,0,100)).save(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    outputs = creation._validated_comfy_outputs(first_root, job['id'], 'image', {})
    creation._update_job(job['id'], status='completed', outputs_json=json.dumps(outputs))
    assert outputs[0]['artifact_id']
    object.__setattr__(settings, 'comfyui_root', tmp_path/'comfy-two')
    source.unlink()
    delivered, mime, name = creation.output_file(job['id'], 0)
    assert hashlib.sha256(delivered.read_bytes()).hexdigest() == original_hash
    assert mime == 'image/png' and name == 'first.png'


def test_legacy_job_adoption_uses_recorded_hash_after_directory_change(tmp_path):
    with patch.object(creation, 'schedule_job'):
        job, _ = creation.create_job(CreationJobRequest(prompt='synthetic'))
    source = tmp_path/'old-comfy'/'output'/'MioJobs'/job['id']/'old.png'
    source.parent.mkdir(parents=True)
    Image.new('RGB', (16,16)).save(source)
    original = source.read_bytes()
    outputs = [{'path': str(source), 'name': 'old.png', 'mime_type': 'image/png', 'sha256': hashlib.sha256(original).hexdigest()}]
    creation._update_job(job['id'], status='completed', outputs_json=json.dumps(outputs))
    delivered, _, _ = creation.output_file(job['id'], 0)
    assert delivered != source and delivered.read_bytes() == original
    assert creation.get_job(job['id'])['outputs'][0]['artifact_id']


def test_corrupt_blob_and_bad_source_hash_are_rejected(tmp_path):
    source = tmp_path/'source.txt'; source.write_text('synthetic')
    with pytest.raises(ValueError, match='改变'):
        artifacts.import_file(source, source_root=tmp_path, producer='test', expected_sha256='0'*64)
    result = artifacts.import_file(source, source_root=tmp_path, producer='test', expected_sha256=hashlib.sha256(b'synthetic').hexdigest())
    blob, _ = artifacts.file(result['id']); blob.write_text('tampered')
    with pytest.raises(ValueError, match='校验失败'): artifacts.file(result['id'])


def test_artifact_import_participates_in_maintenance_write_gate(tmp_path):
    source = tmp_path/'source.txt'; source.write_text('synthetic')
    maintenance.begin('restore')
    with pytest.raises(maintenance.MaintenanceModeError):
        artifacts.import_file(source, source_root=tmp_path, producer='test', expected_sha256=hashlib.sha256(b'synthetic').hexdigest())
    assert artifacts.list_artifacts() == []
