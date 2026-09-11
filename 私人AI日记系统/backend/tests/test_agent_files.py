from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import agent_file_service as files, agent_task_service as tasks, db
from app.routes.agent_tasks import router


class AgentFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = {name: getattr(db.settings, name) for name in ("db_path", "data_dir")}
        object.__setattr__(db.settings, "db_path", self.root / "test.db")
        object.__setattr__(db.settings, "data_dir", self.root / "private")
        db.init_db()
        self.task = tasks.prepare(run_id="file-test", request_id="file-test", conversation_id="file-test",
            source="desktop", user_message="write a document", model_id="test", reasoning_level="low",
            allowed_tools=[], context=[])
        app = FastAPI()
        app.include_router(router, prefix="/api/agent")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for name, value in self.original.items():
            object.__setattr__(db.settings, name, value)
        self.temp.cleanup()

    def test_authorize_read_and_revoke_directory(self):
        folder = self.root / "documents"
        folder.mkdir()
        source = folder / "source.txt"
        source.write_text("user document", encoding="utf-8")
        with self.assertRaises(ValueError):
            files.read_document(str(source))
        response = self.client.put("/api/agent/work/file-roots", json={"paths": [str(folder)]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(files.read_document(str(source))["text"], "user document")
        self.assertEqual(self.client.put("/api/agent/work/file-roots", json={"paths": []}).status_code, 200)
        with self.assertRaises(ValueError):
            files.read_document(str(source))

    def test_write_read_download_and_preserve_versions(self):
        for name in ("result.md", "result.docx", "result.json"):
            with self.subTest(name=name):
                content = '{"result": "first"}' if name.endswith("json") else "first\nsecond"
                output = files.write_document(name, content, self.task["id"])
                self.assertTrue(output["verified"])
                self.assertEqual(files.read_document(output["path"])["text"], content)
                response = self.client.get(output["url"])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(hashlib.sha256(response.content).hexdigest(), output["sha256"])
                changed = files.write_document(name, '"new"', self.task["id"])
                self.assertNotEqual(changed["path"], output["path"])
                self.assertEqual(files.read_document(output["path"])["text"], content)

    def test_reject_escape_hidden_files_and_invalid_json(self):
        for task_id in ("task_../escape", "../escape", "task_x/../../escape"):
            with self.assertRaises(ValueError):
                files.write_document("result.md", "text", task_id)
        for name in ("../escape.md", ".env", "file:stream.md"):
            with self.assertRaises(ValueError):
                files.write_document(name, "text", self.task["id"])
        with self.assertRaises(ValueError):
            files.write_document("result.json", "invalid", self.task["id"])
        self.assertEqual(self.client.put("/api/agent/work/file-roots",
            json={"paths": [str(db.settings.data_dir)]}).status_code, 400)

    def test_tampered_output_is_not_reported_verified(self):
        output = files.write_document("result.md", "expected", self.task["id"])
        Path(output["path"]).write_text("unexpected", encoding="utf-8")
        with self.assertRaises(ValueError):
            files.write_document("result.md", "expected", self.task["id"])
