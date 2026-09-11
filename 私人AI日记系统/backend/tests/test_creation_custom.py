from __future__ import annotations

import copy
import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import creation_custom as custom
from app import db
from app.agent_loop_service import _image_prompt_needs_english_rewrite
from app.config import settings
from app.creation_models import CreationJobRequest
from app.creation_service import create_job, workflow_source_file
from app.creation_workflows import build_workflow_prompt, inspect_workflow, require_workflow, workflow_catalog
from app.routes.creation import router


GRAPH = {
    "1": {"class_type": "TextImage", "inputs": {"text": "test", "width": 512, "height": 768, "seed": 1}},
    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "old"}},
}
BINDINGS = {"prompt": [{"node_id": "1", "input": "text"}], "width": [{"node_id": "1", "input": "width"}], "seed": [{"node_id": "1", "input": "seed"}]}
INFO = {"TextImage": {"input": {"required": {"text": ["STRING"], "width": ["INT"], "height": ["INT"], "seed": ["INT"]}}}, "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING"]}}}}


class CustomWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        changes = {"data_dir": self.root, "runtime_config_path": self.root / "runtime.json", "db_path": self.root / "test.db", "creation_image_workflow_id": "anima-2.9b-image", "creation_video_workflow_id": "minimax-h3-video"}
        self.original = {key: getattr(settings, key) for key in changes}
        for key, value in changes.items():
            object.__setattr__(settings, key, value)
        db.init_db()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        gc.collect()
        for key, value in self.original.items():
            object.__setattr__(settings, key, value)
        self.temp.cleanup()

    def register(self, **changes):
        payload = {"label": "测试图片", "media_type": "image", "prompt": copy.deepcopy(GRAPH), "bindings": copy.deepcopy(BINDINGS), **changes}
        response = self.client.post("/api/creation/workflows", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["workflow"]

    def test_import_compile_default_and_archived_receipt(self):
        item = self.register()
        definition = require_workflow(item["id"])
        self.assertEqual(inspect_workflow(self.root, definition, object_info=INFO)["status"], "ready")
        response = self.client.put("/api/creation/configuration/workflows", json={"image_workflow_id": item["id"], "video_workflow_id": "minimax-h3-video"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(require_workflow("").id, item["id"])
        with patch("app.creation_service.schedule_job"):
            job, _ = create_job(CreationJobRequest(prompt="新的画面", width=640, seed=123))
        self.assertEqual(job["workflow_id"], item["id"])
        prompt, effective = build_workflow_prompt(self.root, definition, job["spec"], job_id=job["id"], object_info=INFO)
        self.assertEqual(prompt["1"]["inputs"], {"text": "新的画面", "width": 640, "height": 768, "seed": 123})
        self.assertEqual(prompt["2"]["inputs"]["filename_prefix"], f'MioJobs/{job["id"]}/image')
        self.assertEqual(effective["workflow_snapshot"]["source_sha256"], item["expected_sha256"])
        self.assertEqual(self.client.delete(f'/api/creation/workflows/{item["id"]}').status_code, 400)
        object.__setattr__(settings, "creation_image_workflow_id", "anima-2.9b-image")
        self.assertEqual(self.client.delete(f'/api/creation/workflows/{item["id"]}').status_code, 200)
        self.assertNotIn(item["id"], [row["id"] for row in workflow_catalog(self.root)])
        self.assertEqual(require_workflow(item["id"], include_disabled=True).id, item["id"])
        with patch("app.creation_service._require_comfy_root", return_value=self.root):
            self.assertTrue(workflow_source_file(job["id"])[0].is_file())

    def test_import_rejects_invalid_bindings_connections_and_editor_json(self):
        variants = [
            {"prompt": {"nodes": []}},
            {"bindings": {"prompt": [{"node_id": "2", "input": "images"}]}},
            {"bindings": {**BINDINGS, "height": BINDINGS["width"]}},
            {"bindings": {"prompt": BINDINGS["width"]}},
            {"bindings": {"other": BINDINGS["prompt"]}},
            {"prompt": {**GRAPH, "3": {"class_type": "TextImage", "inputs": {"text": "unused"}}}, "bindings": {"prompt": [{"node_id": "3", "input": "text"}]}},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                response = self.client.post("/api/creation/workflows", json={"label": "bad", "prompt": GRAPH, "bindings": BINDINGS, **changes})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(custom.definitions(), [])

    def test_preflight_reports_missing_node_and_tamper(self):
        item = self.register()
        definition = require_workflow(item["id"])
        self.assertEqual(inspect_workflow(self.root, definition, object_info={})["nodes"]["missing"], ["SaveImage", "TextImage"])
        custom._path(item["id"]).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已变化"):
            build_workflow_prompt(self.root, definition, {"prompt": "x"}, job_id="test", object_info=INFO)

    def test_defaults_validate_media_and_persist(self):
        item = self.register()
        invalid = self.client.put("/api/creation/configuration/workflows", json={"image_workflow_id": "minimax-h3-video", "video_workflow_id": item["id"]})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(custom.default_ids()["image_workflow_id"], "anima-2.9b-image")

    def test_environment_discovery_normalizes_portable_and_missing_python(self):
        root = self.root / "portable/ComfyUI"
        root.mkdir(parents=True)
        (root / "main.py").touch()
        (root / "folder_paths.py").touch()
        original = settings.comfyui_root
        try:
            object.__setattr__(settings, "comfyui_root", root.parent)
            matches = custom.discover_comfyui()["candidates"]
        finally:
            object.__setattr__(settings, "comfyui_root", original)
        found = next(item for item in matches if item["root"] == str(root))
        self.assertTrue(found["installed"])
        self.assertFalse(found["launchable"])
        python = root.parent / "python_embeded/python.exe"
        python.parent.mkdir(); python.touch()
        self.assertEqual(custom.python_for(root), python)
        self.assertEqual(custom.normalize_root(root.parent), root)

    def test_environment_url_and_path_validation(self):
        for value in ["http://example.com:8188", "http://127.0.0.1:0", "http://user:pass@localhost:8188", "http://localhost:8188/path", "https://127.0.0.1:8188"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                custom.validate_url(value)
        self.assertEqual(custom.validate_url("http://127.0.0.1:8188/"), "http://127.0.0.1:8188")
        with self.assertRaises(ValueError):
            custom.normalize_root(self.root)

    def test_custom_does_not_force_anima_language(self):
        item = self.register()
        self.assertFalse(_image_prompt_needs_english_rewrite({"workflow_id": item["id"], "prompt": "这是自定义工作流的中文图片提示词"}))


if __name__ == "__main__":
    unittest.main()
