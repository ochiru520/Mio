from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app import agent_task_service as tasks
from app.routes.agent_tasks import router


class AgentRunStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "stream.db")
        db.init_db()
        tasks.initialize()
        self.run_id = "run_stream_test"
        self.request_id = "request_stream_test"
        db.create_agent_run(self.run_id, self.request_id, conversation_id="stream-conversation")
        self.task = tasks.prepare(
            run_id=self.run_id,
            request_id=self.request_id,
            conversation_id="stream-conversation",
            source="desktop",
            user_message="测试续读",
            model_id="test-model",
            reasoning_level="standard",
            allowed_tools=[],
            context=[],
        )

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_event_append_is_idempotent_and_route_is_task_scoped(self) -> None:
        db.append_agent_run_event(self.run_id, "stream:event:1", "started", '{"ok":true}')
        db.append_agent_run_event(self.run_id, "stream:event:1", "started", '{"ok":true}')
        db.append_agent_run_event(self.run_id, "stream:event:2", "progress", '{"step":2}')

        app = FastAPI()
        app.include_router(router, prefix="/api/agent")
        with TestClient(app) as client:
            response = client.get(f"/api/agent/work/{self.task['id']}/events?limit=1")
            next_response = client.get(
                f"/api/agent/work/{self.task['id']}/events?after_id={response.json()['next_id']}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["event_key"] for item in response.json()["events"]], ["stream:event:1"])
        self.assertTrue(response.json()["has_more"])
        self.assertEqual([item["event_key"] for item in next_response.json()["events"]], ["stream:event:2"])

    def test_message_route_returns_structured_content(self) -> None:
        db.append_agent_run_message(
            self.run_id, 0, "stream:message:1", "planner_request", '[{"role":"user","content":"你好"}]',
            metadata_json='{"round":0}',
        )
        app = FastAPI()
        app.include_router(router, prefix="/api/agent")
        with TestClient(app) as client:
            response = client.get(f"/api/agent/work/{self.task['id']}/messages")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["messages"][0]["content"][0]["content"], "你好")
        self.assertEqual(response.json()["messages"][0]["metadata"]["round"], 0)

    def test_legacy_task_without_run_returns_empty_stream(self) -> None:
        legacy = tasks.prepare(
            run_id="",
            request_id="legacy_request",
            conversation_id="stream-conversation",
            source="desktop",
            user_message="旧任务",
            model_id="test-model",
            reasoning_level="standard",
            allowed_tools=[],
            context=[],
        )
        app = FastAPI()
        app.include_router(router, prefix="/api/agent")
        with TestClient(app) as client:
            events = client.get(f"/api/agent/work/{legacy['id']}/events")
            messages = client.get(f"/api/agent/work/{legacy['id']}/messages")

        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.json(), {
            "events": [], "next_id": 0, "has_more": False,
            "run_id": None, "status": "legacy",
        })
        self.assertEqual(messages.status_code, 200)
        self.assertEqual(messages.json(), {
            "messages": [], "next_id": 0, "has_more": False, "run_id": None,
        })

    def test_history_spans_runs_and_can_select_one_run(self) -> None:
        second_run = "run_stream_second"
        db.create_agent_run(second_run, "request_stream_second", conversation_id="stream-conversation")
        tasks.prepare(
            run_id=second_run, request_id="request_stream_second", task_id=self.task["id"],
            conversation_id="stream-conversation", source="desktop", user_message="补充要求",
            model_id="test-model", reasoning_level="standard", allowed_tools=[], context=[],
        )
        for run_id in (self.run_id, second_run):
            db.append_agent_run_event(run_id, f"{run_id}:event", "progress", '{}')
            db.append_agent_run_message(run_id, 0, f"{run_id}:message", "planner_response", '{}')
        app = FastAPI()
        app.include_router(router, prefix="/api/agent")
        with TestClient(app) as client:
            base = f"/api/agent/work/{self.task['id']}"
            for kind in ("events", "messages"):
                all_history = client.get(f"{base}/{kind}").json()
                self.assertEqual([item["run_id"] for item in all_history[kind]], [self.run_id, second_run])
                self.assertFalse(all_history["has_more"])
                selected = client.get(f"{base}/{kind}?run_id={self.run_id}").json()
                self.assertEqual([item["run_id"] for item in selected[kind]], [self.run_id])
            page = client.get(f"{base}/runs?limit=1").json()
            self.assertEqual([item["run_id"] for item in page["runs"]], [second_run])
            self.assertTrue(page["has_more"])
            final_page = client.get(f"{base}/runs?limit=1&offset={page['next_offset']}").json()
            self.assertEqual([item["run_id"] for item in final_page["runs"]], [self.run_id])
            self.assertFalse(final_page["has_more"])

    def test_exact_page_size_does_not_claim_more_history(self) -> None:
        db.append_agent_run_event(self.run_id, "exact:event", "progress", '{}')
        db.append_agent_run_message(self.run_id, 0, "exact:message", "planner_response", '{}')
        app = FastAPI()
        app.include_router(router, prefix="/api/agent")
        with TestClient(app) as client:
            for kind in ("events", "messages"):
                page = client.get(f"/api/agent/work/{self.task['id']}/{kind}?limit=1").json()
                self.assertEqual(len(page[kind]), 1)
                self.assertFalse(page["has_more"])

    def test_missing_task_and_foreign_run_are_rejected(self) -> None:
        db.create_agent_run("foreign_run", "foreign_request", conversation_id="other-conversation")
        app = FastAPI()
        app.include_router(router, prefix="/api/agent")
        with TestClient(app) as client:
            for kind in ("events", "messages", "runs"):
                self.assertEqual(client.get(f"/api/agent/work/missing/{kind}").status_code, 404)
            for kind in ("events", "messages"):
                response = client.get(f"/api/agent/work/{self.task['id']}/{kind}?run_id=foreign_run")
                self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
