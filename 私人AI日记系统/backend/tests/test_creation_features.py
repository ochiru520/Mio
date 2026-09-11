from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.creation_models import CreationJobRequest, CreationToolRemoteImageRequest
from app.creation_service import (
    _assert_public_url,
    _image_urls_from_payload,
    _comfyui_launch_target,
    _require_comfy_root,
    _request_remote_image,
    build_workflow_prompt,
    comfyui_preflight,
    create_job,
    get_job,
    retry_job,
    save_asset,
    save_job_output_as_asset,
    start_comfyui,
    workflow_source_file,
)
from app.creation_workflows import (
    WorkflowDefinition,
    _load_runtime_workflow,
    inspect_workflow,
    require_workflow,
    ui_workflow_to_api_prompt,
)
from app.agent_loop_service import (
    PlannedToolCall,
    _agent_creation_target,
    _creation_job_follow_up_call,
    _creation_preflight_calls,
    _inherit_continuation_arguments,
    _image_prompt_needs_english_rewrite,
    _resolve_agent_creation_message,
    run_agent_loop,
)
from app.agent_tool_service import ToolExecutionResult
from app.creation_lora_catalog import IMAGE_LORA_CATALOG, list_image_loras
from app.routes.creation import router


PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class CreationFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original = {
            "db_path": db.settings.db_path,
            "creation_asset_dir": db.settings.creation_asset_dir,
            "creation_output_dir": db.settings.creation_output_dir,
            "model_profiles_path": db.settings.model_profiles_path,
        }
        object.__setattr__(db.settings, "db_path", root / "test.db")
        object.__setattr__(db.settings, "creation_asset_dir", root / "assets")
        object.__setattr__(db.settings, "creation_output_dir", root / "outputs")
        object.__setattr__(db.settings, "model_profiles_path", root / "models.json")
        db.init_db()

    def tearDown(self) -> None:
        for key, value in self.original.items():
            object.__setattr__(db.settings, key, value)
        self.temp_dir.cleanup()

    def test_asset_and_preset_snapshot_are_isolated_from_source(self) -> None:
        asset = save_asset("reference.png", "image/png", PNG_DATA_URL)
        self.assertTrue(asset["id"].startswith("asset_"))
        self.assertTrue(Path(db.settings.creation_asset_dir, f"{asset['id']}.png").is_file())

        from app.creation_models import CreationPresetRequest
        from app.creation_service import save_preset

        preset = save_preset(CreationPresetRequest(
            kind="character",
            name="Mio",
            content="silver-gray hair",
            reference_asset_ids=[asset["id"]],
        ))
        with patch("app.creation_service.schedule_job"):
            job, created = create_job(CreationJobRequest(
                media_type="image",
                backend="comfyui",
                workflow_id="anima-2.9b-image",
                prompt="standing in rain",
                character_preset_id=preset["id"],
            ))
        self.assertTrue(created)
        self.assertEqual(job["status"], "created")
        self.assertEqual(job["preset_snapshot"]["character"]["name"], "Mio")
        self.assertIn("silver-gray hair", job["spec"]["prompt"])
        self.assertEqual(job["spec"]["reference_asset_ids"], [asset["id"]])

    def test_remote_request_schema_requires_provider_and_model(self) -> None:
        with self.assertRaises(ValueError):
            CreationJobRequest(backend="remote_api", prompt="a cat")
        payload = CreationToolRemoteImageRequest(
            provider_id="provider-1", model_id="image-model", prompt="a cat"
        )
        self.assertEqual(payload.remote_api_mode, "auto")

    def test_completed_image_output_can_be_reused_as_video_reference_asset(self) -> None:
        output_dir = Path(db.settings.creation_output_dir) / "job_image_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "image.png"
        content = base64.b64decode(PNG_DATA_URL.split(",", 1)[1])
        output_path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        timestamp = db.now_iso()
        outputs = [{
            "path": str(output_path), "name": output_path.name, "media_type": "image",
            "mime_type": "image/png", "size": len(content), "sha256": digest,
        }]
        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO creation_jobs(
                    id, conversation_id, source, media_type, backend, workflow_id,
                    status, stage, progress, spec_json, preset_snapshot_json, outputs_json,
                    confirmation_reason, idempotency_key, parent_job_id, created_at, updated_at
                ) VALUES ('job_image_output', 'desktop_agent_video_ref', 'desktop', 'image',
                    'comfyui', 'anima-2.9b-image', 'completed', 'completed', 1, '{}', '{}', ?, '',
                    'image-output-key', '', ?, ?)
                """,
                (json.dumps(outputs), timestamp, timestamp),
            )

        first = save_job_output_as_asset("job_image_output")
        second = save_job_output_as_asset("job_image_output")

        self.assertTrue(first["id"].startswith("asset_"))
        self.assertEqual(second["id"], first["id"])

    def test_responses_image_generation_result_is_detected(self) -> None:
        encoded = base64.b64encode(b"image-bytes").decode("ascii")
        payload = {"output": [{"type": "image_generation_call", "result": encoded}]}
        self.assertEqual(_image_urls_from_payload(payload), [("base64", encoded)])

    def test_remote_images_api_accepts_binary_image_response(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, endpoint, **kwargs):
                calls.append((endpoint, kwargs["json"]))
                return httpx.Response(
                    200,
                    headers={"content-type": "image/png"},
                    content=b"fake-png",
                    request=httpx.Request("POST", endpoint),
                )

        with patch("app.creation_service.httpx.AsyncClient", return_value=FakeClient()):
            result = asyncio.run(_request_remote_image(
                "job-remote-binary",
                {"base_url": "https://example.test/v1", "api_key": "secret", "auth_scheme": "bearer"},
                ["images"],
                [],
                {"model_id": "image-model", "prompt": "a cat", "width": 512, "height": 768},
                [],
            ))

        self.assertEqual(result, b"fake-png")
        self.assertEqual(calls[0][0], "https://example.test/v1/images/generations")
        self.assertEqual(calls[0][1]["size"], "512x768")
        self.assertEqual(calls[0][1]["n"], 1)

    def test_remote_responses_api_accepts_image_generation_base64(self) -> None:
        encoded = base64.b64encode(b"fake-png").decode("ascii")

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, endpoint, **kwargs):
                self.endpoint = endpoint
                self.payload = kwargs["json"]
                return httpx.Response(
                    200,
                    json={"output": [{"type": "image_generation_call", "result": encoded}]},
                    request=httpx.Request("POST", endpoint),
                )

        client = FakeClient()
        with patch("app.creation_service.httpx.AsyncClient", return_value=client):
            result = asyncio.run(_request_remote_image(
                "job-remote-responses",
                {"base_url": "https://example.test/v1", "api_key": "secret", "auth_scheme": "bearer"},
                ["responses"],
                ["data:image/png;base64,ZmFrZS1yZWY="],
                {"model_id": "image-model", "prompt": "a cat"},
                [],
            ))

        self.assertEqual(result, b"fake-png")
        self.assertEqual(client.endpoint, "https://example.test/v1/responses")
        self.assertEqual(client.payload["tools"], [{"type": "image_generation"}])
        self.assertEqual(client.payload["input"][0]["content"][1]["type"], "input_image")

    def test_remote_images_api_retries_with_degraded_parameters(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, endpoint, **kwargs):
                calls.append(kwargs["json"])
                if len(calls) == 1:
                    return httpx.Response(
                        400,
                        text="size unsupported",
                        request=httpx.Request("POST", endpoint),
                    )
                return httpx.Response(
                    200,
                    json={"data": [{"b64_json": base64.b64encode(b"fake-png").decode("ascii")}]},
                    request=httpx.Request("POST", endpoint),
                )

        with patch("app.creation_service.httpx.AsyncClient", return_value=FakeClient()):
            result = asyncio.run(_request_remote_image(
                "job-remote-fallback",
                {"base_url": "https://example.test/v1", "api_key": "secret", "auth_scheme": "bearer"},
                ["images"],
                [],
                {"model_id": "image-model", "prompt": "a cat", "width": 512, "height": 768},
                [],
            ))

        self.assertEqual(result, b"fake-png")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("response_format", calls[1])

    def test_public_url_rejects_private_and_credentials(self) -> None:
        with self.assertRaises(ValueError):
            _assert_public_url("http://127.0.0.1:8188/image.png")
        with self.assertRaises(ValueError):
            _assert_public_url("https://user:password@example.com/image.png")

    def test_comfyui_root_requires_an_existing_installation(self) -> None:
        original = db.settings.comfyui_root
        try:
            object.__setattr__(db.settings, "comfyui_root", Path(self.temp_dir.name))
            with self.assertRaisesRegex(ValueError, "ComfyUI"):
                _require_comfy_root()
        finally:
            object.__setattr__(db.settings, "comfyui_root", original)

    def test_start_comfyui_does_not_spawn_when_service_is_reachable(self) -> None:
        reachable = {"ok": True, "reachable": True, "base_url": "http://127.0.0.1:8188"}
        with patch("app.creation_service.comfyui_health", new=AsyncMock(return_value=reachable)), patch(
            "app.creation_service.subprocess.Popen"
        ) as popen:
            result = asyncio.run(start_comfyui(wait_seconds=1))
        self.assertTrue(result["ok"])
        self.assertTrue(result["already_running"])
        popen.assert_not_called()

    def test_start_comfyui_uses_fixed_executable_arguments_and_cwd(self) -> None:
        root = Path(self.temp_dir.name)
        executable = root / "python" / "python.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"MZ")
        (root / "main.py").write_text("", encoding="utf-8")
        process = MagicMock(pid=4242, returncode=None)
        process.poll.return_value = None
        health = AsyncMock(side_effect=[
            {"ok": False, "reachable": False},
            {"ok": False, "reachable": False},
            {"ok": True, "reachable": True, "base_url": "http://127.0.0.1:8188"},
        ])
        original_data_dir = db.settings.data_dir
        try:
            object.__setattr__(db.settings, "data_dir", root / "data")
            with patch("app.creation_service.comfyui_health", new=health), patch(
                "app.creation_service._comfyui_launch_target",
                return_value=(root, executable, "127.0.0.1", 8188),
            ), patch("app.creation_service.asyncio.sleep", new=AsyncMock()), patch(
                "app.creation_service.subprocess.Popen", return_value=process
            ) as popen:
                result = asyncio.run(start_comfyui(wait_seconds=1))
        finally:
            object.__setattr__(db.settings, "data_dir", original_data_dir)
        self.assertTrue(result["ok"])
        self.assertTrue(result["started"])
        command = popen.call_args.args[0]
        self.assertEqual(command, [
            str(executable), "main.py", "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch",
        ])
        self.assertEqual(popen.call_args.kwargs["cwd"], str(root))
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_start_comfyui_returns_background_state_and_reuses_live_process(self) -> None:
        root = Path(self.temp_dir.name)
        executable = root / "python" / "python.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"MZ")
        (root / "main.py").write_text("", encoding="utf-8")
        process = MagicMock(pid=4343, returncode=None)
        process.poll.return_value = None
        loop = MagicMock()
        loop.time.side_effect = [0.0, 2.0]
        original_data_dir = db.settings.data_dir
        try:
            object.__setattr__(db.settings, "data_dir", root / "data")
            with patch("app.creation_service.comfyui_health", new=AsyncMock(return_value={"ok": False, "reachable": False})), patch(
                "app.creation_service._comfyui_launch_target",
                return_value=(root, executable, "127.0.0.1", 8188),
            ), patch("app.creation_service.asyncio.get_running_loop", return_value=loop), patch(
                "app.creation_service.subprocess.Popen", return_value=process
            ) as popen, patch("app.creation_service._comfyui_start_process", None):
                first = asyncio.run(start_comfyui(wait_seconds=1))
                second = asyncio.run(start_comfyui(wait_seconds=1))
        finally:
            object.__setattr__(db.settings, "data_dir", original_data_dir)
        self.assertTrue(first["ok"])
        self.assertTrue(first["starting"])
        self.assertTrue(second["starting"])
        self.assertEqual(second["pid"], 4343)
        popen.assert_called_once()

    def test_comfyui_launch_target_rejects_non_loopback_api(self) -> None:
        root = Path(self.temp_dir.name)
        executable = root / "python" / "python.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"MZ")
        original_url = db.settings.comfyui_base_url
        try:
            object.__setattr__(db.settings, "comfyui_base_url", "http://192.168.1.20:8188")
            with patch("app.creation_service._require_comfy_root", return_value=root):
                with self.assertRaisesRegex(ValueError, "回环地址"):
                    _comfyui_launch_target()
        finally:
            object.__setattr__(db.settings, "comfyui_base_url", original_url)

    def test_workflow_preflight_reports_hash_and_missing_model(self) -> None:
        root = Path(self.temp_dir.name)
        workflow_root = root / "my_workflows"
        workflow_root.mkdir()
        definition = WorkflowDefinition(
            id="test-image", label="测试图片", media_type="image", filename="test.json",
            description="test", expected_sha256="deadbeef",
        )
        (workflow_root / "test.json").write_text(json.dumps({
            "nodes": [{"id": 1, "type": "UNETLoader", "mode": 0, "widgets_values": ["missing.safetensors"]}],
            "links": [],
        }), encoding="utf-8")
        report = inspect_workflow(root, definition, object_info={"UNETLoader": {"input": {"required": {"unet_name": [["other.safetensors"]], "weight_dtype": [["default"]]}}}})
        self.assertEqual(report["status"], "changed")
        self.assertEqual(report["models"][0]["requested"], "missing.safetensors")
        self.assertFalse(report["models"][0]["available"])
        self.assertTrue(any("工作流已变化" in item for item in report["errors"]))

    def test_selected_workflows_map_prompt_and_output_prefix(self) -> None:
        workflow = require_workflow("anima-2.9b-image", "image")
        source = Path(r"D:\AI\ComfyUI-aki-v1.4\my_workflows") / workflow.filename
        canvas = json.loads(source.read_text(encoding="utf-8"))
        sampler = next(node for node in canvas["nodes"] if node["id"] == 28)
        sampler["widgets_values"][-1] = 0.36
        sampler["widgets_values_named"]["denoise"] = 0.36
        root = Path(self.temp_dir.name) / "workflow-mapping"
        (root / "my_workflows").mkdir(parents=True)
        fixture = root / "my_workflows" / workflow.filename
        fixture.write_text(json.dumps(canvas), encoding="utf-8")
        workflow = replace(workflow, expected_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest())
        prompt, effective = build_workflow_prompt(
            root,
            workflow,
            {
                "prompt": "new prompt",
                "negative_prompt": "bad",
                "seed": 123,
                "steps": 30,
                "cfg": 7.5,
                "lora_choices": ["clear_lineart"],
            },
            job_id="job_unit",
        )
        self.assertEqual(workflow.filename, "无敌图片.json")
        self.assertEqual(len(workflow.expected_sha256), 64)
        self.assertEqual(prompt["29"]["inputs"]["text"], "new prompt")
        self.assertEqual(prompt["4"]["inputs"]["text"], "bad")
        self.assertEqual(prompt["5"]["inputs"]["text"], "bad")
        self.assertEqual(prompt["8"]["inputs"]["seed"], 123)
        self.assertEqual(prompt["28"]["inputs"]["seed"], 123)
        self.assertEqual(prompt["8"]["inputs"]["steps"], 4)
        self.assertEqual(prompt["28"]["inputs"]["steps"], 4)
        self.assertEqual(prompt["8"]["inputs"]["cfg"], 1.0)
        self.assertEqual(prompt["28"]["inputs"]["cfg"], 1.0)
        self.assertEqual(prompt["8"]["inputs"]["denoise"], 1.0)
        self.assertEqual(prompt["28"]["inputs"]["denoise"], 0.36)
        self.assertTrue(prompt["23"]["inputs"]["lora_1"]["on"])
        self.assertTrue(prompt["23"]["inputs"]["lora_2"]["on"])
        self.assertFalse(prompt["23"]["inputs"]["lora_3"]["on"])
        self.assertFalse(prompt["23"]["inputs"]["lora_4"]["on"])
        self.assertTrue(prompt["23"]["inputs"]["lora_5"]["on"])
        self.assertFalse(prompt["23"]["inputs"]["lora_6"]["on"])
        self.assertEqual(prompt["16"]["inputs"]["width"], 864)
        self.assertEqual(prompt["16"]["inputs"]["height"], 1536)
        self.assertEqual(prompt["22"]["inputs"]["filename_prefix"], "MioJobs/job_unit/image")
        self.assertEqual(effective["seed"], 123)
        self.assertEqual(effective["steps"], 4)
        self.assertEqual(effective["cfg"], 1.0)
        self.assertEqual(effective["lora_choices"], ["clear_lineart"])
        snapshot = effective["workflow_snapshot"]
        self.assertEqual(snapshot["mode"], "exact_workflow")
        self.assertEqual(snapshot["source_file"], "无敌图片.json")
        self.assertEqual(snapshot["source_sha256"], workflow.expected_sha256)
        self.assertEqual([item["name"] for item in snapshot["loras"] if item["on"]], [
            "功能_Anima_加速_Turbo_v0.1.safetensors",
            "功能_Anima_加速_Turbo_v0.2.safetensors",
            "画风_清晰线稿_Anima_v1.0.safetensors",
        ])

    def test_selected_video_workflow_prunes_alternate_branches(self) -> None:
        root = Path(r"D:\AI\ComfyUI-aki-v1.4")
        workflow = require_workflow("minimax-h3-video", "video")
        prompt, effective = build_workflow_prompt(
            root,
            workflow,
            {"prompt": "a short test", "width": 512, "height": 768, "duration_seconds": 1.5, "fps": 30, "seed": 321},
            job_id="video_unit",
            object_info=json.loads(Path("object_info_tmp.json").read_text(encoding="utf-8-sig")) if Path("object_info_tmp.json").is_file() else None,
            uploaded_reference="reference.png",
        )
        self.assertEqual(workflow.filename, "黑鹤.json")
        self.assertEqual(effective["runtime_workflow_filename"], "黑鹤.json")
        self.assertEqual(prompt["133"]["inputs"]["prompt"], "a short test")
        self.assertEqual(prompt["186"]["inputs"]["seed"], 321)
        self.assertEqual(prompt["188"]["inputs"]["fps"], 30)
        self.assertEqual(prompt["135"]["inputs"]["value"], 1.5)
        self.assertIn("a * 30", prompt["134"]["inputs"]["expression"])
        self.assertNotIn("362", prompt)  # disconnected TE-Speed optional branch
        self.assertEqual(prompt["189"]["inputs"]["filename_prefix"], "MioJobs/video_unit/video")

    def test_v3_dynamic_inputs_use_flat_dotted_wire_keys(self) -> None:
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "PrimitiveFloat",
                    "mode": 0,
                    "inputs": [{"name": "value", "link": None, "widget": {"name": "value"}}],
                    "widgets_values": [1.0],
                },
                {
                    "id": 2,
                    "type": "ComfyMathExpression",
                    "mode": 0,
                    "inputs": [
                        {"name": "values.a", "link": 10, "type": "FLOAT"},
                        {"name": "expression", "link": None, "widget": {"name": "expression"}},
                    ],
                    "widgets_values": ["a * 24"],
                },
            ],
            "links": [[10, 1, 0, 2, 0, "FLOAT"]],
        }
        object_info = {
            "PrimitiveFloat": {"input": {"required": {"value": ["FLOAT", {}]}}},
            "ComfyMathExpression": {
                "input": {
                    "required": {
                        "expression": ["STRING", {}],
                        "values": ["COMFY_AUTOGROW_V3", {}],
                    }
                }
            },
        }
        prompt = ui_workflow_to_api_prompt(workflow, object_info=object_info)
        inputs = prompt["2"]["inputs"]
        self.assertEqual(inputs["values.a"], ["1", 0])
        self.assertNotIn("values", inputs)

    def test_rgthree_power_lora_loader_keeps_dynamic_lora_slots(self) -> None:
        workflow = {
            "nodes": [
                {"id": 1, "type": "TestModel", "mode": 0, "inputs": []},
                {"id": 2, "type": "TestClip", "mode": 0, "inputs": []},
                {
                    "id": 23,
                    "type": "Power Lora Loader (rgthree)",
                    "mode": 0,
                    "inputs": [
                        {"name": "model", "type": "MODEL", "link": 1},
                        {"name": "clip", "type": "CLIP", "link": 2},
                    ],
                    "widgets_values": [
                        None,
                        {"type": "PowerLoraLoaderHeaderWidget"},
                        {"on": True, "lora": "enabled.safetensors", "strength": 1},
                        {"on": False, "lora": "disabled.safetensors", "strength": 0.5},
                        None,
                        "",
                    ],
                    "widgets_values_named": {
                        "divider": None,
                        "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"},
                        "lora_1": {"on": True, "lora": "enabled.safetensors", "strength": 1},
                        "lora_2": {"on": False, "lora": "disabled.safetensors", "strength": 0.5},
                        "➕ Add Lora": "",
                    },
                },
            ],
            "links": [
                [1, 1, 0, 23, 0, "MODEL"],
                [2, 2, 0, 23, 1, "CLIP"],
            ],
        }
        object_info = {
            "TestModel": {"input": {"required": {}, "optional": {}}},
            "TestClip": {"input": {"required": {}, "optional": {}}},
            "Power Lora Loader (rgthree)": {
                "input": {
                    "required": {"model": ["MODEL"], "clip": ["CLIP"]},
                    "optional": {},
                }
            }
        }

        prompt = ui_workflow_to_api_prompt(workflow, object_info=object_info)
        inputs = prompt["23"]["inputs"]
        self.assertEqual(inputs["lora_1"]["lora"], "enabled.safetensors")
        self.assertTrue(inputs["lora_1"]["on"])
        self.assertEqual(inputs["lora_2"]["lora"], "disabled.safetensors")
        self.assertFalse(inputs["lora_2"]["on"])
        self.assertNotIn("divider", inputs)
        self.assertNotIn("PowerLoraLoaderHeaderWidget", inputs)
        self.assertNotIn("➕ Add Lora", inputs)

    def test_video_workflow_never_falls_back_to_another_graph(self) -> None:
        root = Path(self.temp_dir.name)
        workflow_root = root / "my_workflows"
        workflow_root.mkdir()
        reference_graph = {"nodes": [{"id": 1, "type": "MiniMaxH3ReferenceToVideo", "mode": 0}]}
        first_frame_graph = {"nodes": [{"id": 1, "type": "MiniMaxH3ImageToVideo", "mode": 0}]}
        (workflow_root / "黑鹤.json").write_text(json.dumps(reference_graph), encoding="utf-8")
        (workflow_root / "MiniMaxH3_accel_fixed.json").write_text(json.dumps(first_frame_graph), encoding="utf-8")
        object_info = {
            "UNETLoader": {"input": {"required": {"unet_name": [["minimax_h3_fl2va_pruned_int8_convrot.safetensors"]]}}}
        }
        workflow, filename = _load_runtime_workflow(
            root,
            require_workflow("minimax-h3-video", "video"),
            object_info,
        )
        self.assertEqual(filename, "黑鹤.json")
        self.assertEqual(workflow["nodes"][0]["type"], "MiniMaxH3ReferenceToVideo")

    def test_creation_routes_return_job_and_confirmation_state(self) -> None:
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with patch("app.creation_service.schedule_job"):
            response = client.post("/api/creation/jobs", json={
                "backend": "comfyui",
                "media_type": "image",
                "workflow_id": "anima-2.9b-image",
                "prompt": "a test image",
            })
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()["job"]
        self.assertEqual(job["status"], "created")
        fetched = client.get(f"/api/creation/jobs/{job['id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["job"]["id"], job["id"])
        self.assertEqual(client.post(f"/api/creation/jobs/{job['id']}/cancel").status_code, 200)

        with patch("app.creation_service._provider_connection", return_value={
            "provider_id": "provider-1", "provider_name": "Test", "base_url": "https://example.com/v1",
            "api_key": "secret", "auth_scheme": "bearer",
        }), patch("app.creation_service.schedule_job"):
            remote = client.post("/api/creation/jobs", json={
                "backend": "remote_api",
                "media_type": "image",
                "prompt": "a remote image",
                "provider_id": "provider-1",
                "model_id": "image-model",
            })
        self.assertEqual(remote.status_code, 200, remote.text)
        self.assertEqual(remote.json()["job"]["status"], "needs_confirmation")

    def test_creation_preflight_route_returns_dependency_report(self) -> None:
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        report = {"ok": True, "checked": True, "comfyui": {"reachable": True}, "workflows": []}
        with patch("app.creation_service.comfyui_preflight", return_value=report):
            response = client.get("/api/creation/preflight")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), report)

    def test_creation_start_comfyui_route_returns_start_result(self) -> None:
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        result = {"ok": True, "started": False, "already_running": True, "message": "ComfyUI 已经连接。"}
        with patch("app.creation_service.start_comfyui", new=AsyncMock(return_value=result)):
            response = client.post("/api/creation/comfyui/start")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), result)

    def test_retry_strips_runtime_workflow_fields_before_validation(self) -> None:
        with patch("app.creation_service.schedule_job"):
            job, _ = create_job(CreationJobRequest(
                media_type="image",
                backend="comfyui",
                workflow_id="anima-2.9b-image",
                prompt="retry me",
            ))
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE creation_jobs SET status='completed', stage='completed', "
                "spec_json=? WHERE id=?",
                (json.dumps({
                    "media_type": "image", "backend": "comfyui",
                    "workflow_id": "anima-2.9b-image", "prompt": "retry me",
                    "runtime_workflow_filename": "无敌图片.json", "seed": 123,
                }, ensure_ascii=False), job["id"]),
            )
        with patch("app.creation_service.schedule_job"):
            retried = retry_job(job["id"])
        self.assertEqual(retried["status"], "needs_confirmation")
        self.assertNotIn("runtime_workflow_filename", retried["spec"])

    def test_agent_can_read_confirmed_creation_presets(self) -> None:
        from app.agent_tool_service import _dispatch_read_tool
        from app.creation_models import CreationPresetRequest
        from app.creation_service import save_preset

        save_preset(CreationPresetRequest(kind="style", name="水彩", content="soft watercolor"))
        result = asyncio.run(_dispatch_read_tool("creation_list_presets", {"kind": "style"}))
        self.assertEqual(len(result["presets"]), 1)
        self.assertEqual(result["presets"][0]["name"], "水彩")

    def test_agent_lora_catalog_explains_selectable_and_locked_entries(self) -> None:
        catalog = list_image_loras()
        self.assertEqual(catalog["workflow_id"], "anima-2.9b-image")
        self.assertEqual({item["id"] for item in catalog["loras"]}, {
            "highres_boost", "sensual_style", "clear_lineart", "soft_cel_pastel",
        })
        self.assertEqual(len(catalog["locked_loras"]), 2)
        self.assertTrue(all(item["locked"] and not item["selectable"] for item in catalog["locked_loras"]))
        self.assertTrue(all(item["purpose"] and item["use_when"] and item["avoid_when"] for item in IMAGE_LORA_CATALOG))

        from app.agent_tool_service import _dispatch_read_tool

        result = asyncio.run(_dispatch_read_tool("creation_list_loras", {}))
        self.assertEqual(result["workflow_id"], "anima-2.9b-image")
        self.assertEqual(len(result["loras"]), 4)

    def test_creation_preflight_reads_presets_and_video_assets_before_planning(self) -> None:
        calls = _creation_preflight_calls(
            "请生成一段视频，让这个角色在雨里回头。",
            "desktop_chat",
            None,
        )
        self.assertEqual(
            [item.name for item in calls],
            ["creation_list_presets", "creation_check_workflow", "creation_list_assets"],
        )
        image_calls = _creation_preflight_calls("请生成一张图片", "desktop_agent_image", None)
        self.assertEqual(
            [item.name for item in image_calls],
            ["creation_list_presets", "creation_list_loras", "creation_check_workflow"],
        )
        self.assertEqual(_creation_preflight_calls("和我聊聊天", "desktop_chat", None), [])
        self.assertEqual(_creation_preflight_calls("请生成一张图片", "qq_group_123", None), [])

    def test_planner_receives_preflight_results_before_generation_call(self) -> None:
        source_message_id = db.save_message(
            "user", "请做个动画，让角色回头。", source="desktop", conversation_id="desktop_chat"
        )
        captured: dict[str, object] = {}

        async def fake_plan(messages, **_kwargs):
            if "messages" not in captured:
                captured["messages"] = messages
                return ([PlannedToolCall(name, name, {}) for name in (
                    "creation_list_presets", "creation_check_workflow", "creation_list_assets"
                )], "native", [], [], 1)
            captured["messages"] = messages
            return (
                [PlannedToolCall(
                    "generate-video",
                    "comfyui_generate_video",
                    {"prompt": "让角色回头", "reference_asset_id": "asset_ref"},
                )],
                "native",
                [],
                [],
                1,
            )

        async def fake_execute(name, arguments, context):
            if name == "creation_list_presets":
                result = {"presets": [{"id": "preset_style", "kind": "style", "approved": True}]}
            elif name == "creation_check_workflow":
                result = {"ok": True, "checked": True, "workflows": [{"id": "minimax-h3-video", "status": "ready"}]}
            elif name == "creation_list_assets":
                result = {"assets": [{"id": "asset_ref", "original_name": "ref.png"}]}
            else:
                result = {"job": {"id": "job_fake", "status": "created"}}
            return ToolExecutionResult(name, "completed", result, context.step_index)

        with patch("app.agent_loop_service._plan", new=fake_plan), patch(
            "app.agent_loop_service.execute_tool_call", new=fake_execute
        ):
            result = asyncio.run(run_agent_loop(
                conversation_id="desktop_chat",
                source="desktop",
                user_message="请做个动画，让角色回头。",
                source_message_id=source_message_id,
                request_id="request-creation-preflight",
                trace_id="trace-creation-preflight",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={
                    "creation_list_presets", "creation_check_workflow", "creation_list_assets", "comfyui_generate_video"
                },
            ))

        planner_text = json.dumps(captured["messages"], ensure_ascii=False)
        self.assertIn("preset_style", planner_text)
        self.assertIn("creation_check_workflow", planner_text)
        self.assertIn("asset_ref", planner_text)
        self.assertEqual(
            [item.tool_name for item in result.observations],
            ["creation_list_presets", "creation_check_workflow", "creation_list_assets", "comfyui_generate_video"],
        )

    def test_agent_workspace_turns_meitu_request_into_direct_image_job(self) -> None:
        conversation_id = "desktop_agent_direct_image"
        source_message_id = db.save_message(
            "user", "给我生成一张萝莉美图", source="desktop", conversation_id=conversation_id
        )
        captured: dict[str, object] = {}
        execution_sources: list[str] = []

        async def fake_plan(messages, **kwargs):
            captured["messages"] = messages
            captured["allowed_tool_names"] = kwargs.get("allowed_tool_names")
            return (
                [PlannedToolCall(
                    "generate-image",
                    "comfyui_generate_image",
                    {
                        "prompt": "anime portrait, soft light",
                        "negative_prompt": "blurry, watermark",
                        "lora_choices": ["soft_cel_pastel"],
                    },
                )],
                "native",
                [],
                [],
                1,
            )

        async def fake_execute(name, arguments, context):
            execution_sources.append(context.source)
            if name == "creation_list_presets":
                result = {"presets": []}
            elif name == "creation_list_loras":
                result = list_image_loras()
            elif name == "creation_check_workflow":
                result = {
                    "ok": True,
                    "checked": True,
                    "workflows": [{"id": "anima-2.9b-image", "status": "ready"}],
                }
            else:
                result = {
                    "job": {
                        "id": "job_direct",
                        "status": "created",
                        "spec": dict(arguments),
                    }
                }
            return ToolExecutionResult(name, "completed", result, context.step_index)

        with patch("app.agent_loop_service._plan", new=fake_plan), patch(
            "app.agent_loop_service.execute_tool_call", new=fake_execute
        ):
            result = asyncio.run(run_agent_loop(
                conversation_id=conversation_id,
                source="desktop",
                user_message="给我生成一张萝莉美图",
                source_message_id=source_message_id,
                request_id="request-agent-direct-image",
                trace_id="trace-agent-direct-image",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={
                    "creation_list_presets", "creation_list_loras", "creation_check_workflow", "comfyui_generate_image"
                },
            ))

        self.assertTrue({"comfyui_generate_image", "creation_check_workflow", "creation_list_presets", "creation_list_loras"}.issubset(captured["allowed_tool_names"]))
        self.assertEqual(execution_sources, ["desktop"])
        self.assertEqual(result.status, "waiting_jobs")
        self.assertEqual(result.observations[-1].result["job"]["spec"]["lora_choices"], ["soft_cel_pastel"])
        self.assertEqual(
            [item.tool_name for item in result.observations],
            ["comfyui_generate_image"],
        )

    def test_agent_workspace_recognizes_common_short_image_phrases(self) -> None:
        allowed = {"comfyui_generate_image"}
        for message in (
            "直接生图",
            "给我来一张图",
            "现在来一张萝莉图吧",
            "我想要一张角色立绘",
            "生成一张功能验收图：白猫坐在窗边",
            "图片给我出一下",
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    _agent_creation_target(message, "desktop_agent_short", allowed),
                    "comfyui_generate_image",
                )

    def test_image_prompt_quality_gate_requires_english_for_anima(self) -> None:
        self.assertTrue(_image_prompt_needs_english_rewrite({
            "prompt": "一只白猫坐在雨后窗边，柔和动漫插画，竖版构图",
            "negative_prompt": "模糊，畸形，水印",
        }))
        self.assertFalse(_image_prompt_needs_english_rewrite({
            "prompt": "a white cat sitting by a window after rain, soft anime illustration",
            "negative_prompt": "blurry, malformed anatomy, watermark",
        }))

    def test_agent_replans_chinese_image_prompt_before_submission(self) -> None:
        conversation_id = "desktop_agent_english_prompt"
        source_message_id = db.save_message(
            "user", "生成一张白猫坐在窗边的图", source="desktop", conversation_id=conversation_id,
        )
        plan_calls = 0

        async def fake_plan(_messages, **_kwargs):
            nonlocal plan_calls
            plan_calls += 1
            arguments = (
                {"prompt": "白猫坐在窗边，柔和动漫插画", "negative_prompt": "模糊，水印", "lora_choices": []}
                if plan_calls == 1
                else {"prompt": "a white cat sitting by a window, soft anime illustration", "negative_prompt": "blurry, watermark", "lora_choices": []}
            )
            return ([PlannedToolCall(f"image-{plan_calls}", "comfyui_generate_image", arguments)], "native", [], [], 1)

        async def fake_execute(name, arguments, context):
            if name == "creation_list_presets":
                result = {"presets": []}
            elif name == "creation_check_workflow":
                result = {"ok": True, "checked": True, "workflows": [{"id": "anima-2.9b-image", "status": "ready"}]}
            else:
                result = {"job": {"id": "job_english", "status": "created", "spec": dict(arguments)}}
            return ToolExecutionResult(name, "completed", result, context.step_index)

        with patch("app.agent_loop_service._plan", new=fake_plan), patch(
            "app.agent_loop_service.execute_tool_call", new=fake_execute,
        ):
            result = asyncio.run(run_agent_loop(
                conversation_id=conversation_id,
                source="desktop",
                user_message="生成一张白猫坐在窗边的图",
                source_message_id=source_message_id,
                request_id="request-english-replan",
                trace_id="trace-english-replan",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={"creation_list_presets", "creation_check_workflow", "comfyui_generate_image"},
            ))

        self.assertEqual(plan_calls, 2)
        self.assertEqual(result.observations[0].status, "failed")
        generated = result.observations[-1].result["job"]["spec"]
        self.assertIn("white cat", generated["prompt"])
        self.assertNotIn("白猫", generated["prompt"])

    def test_agent_workspace_resumes_latest_unfulfilled_creation_after_service_recovery(self) -> None:
        conversation_id = "desktop_agent_resume_image"
        db.save_message(
            "user", "现在给我生成一张纳西妲在须弥森林里的图片",
            source="desktop", conversation_id=conversation_id,
        )
        db.save_message(
            "assistant", "ComfyUI 没启动，暂时无法生成",
            source="desktop", conversation_id=conversation_id,
        )
        source_message_id = db.save_message(
            "user", "已经修好了", source="desktop", conversation_id=conversation_id,
        )
        resolved = _resolve_agent_creation_message(
            "已经修好了", conversation_id, source_message_id,
            {"creation_list_presets", "creation_check_workflow", "comfyui_generate_image"},
        )
        self.assertEqual(resolved, "现在给我生成一张纳西妲在须弥森林里的图片")

    def test_agent_workspace_does_not_repeat_creation_that_already_has_a_job(self) -> None:
        conversation_id = "desktop_agent_no_repeat_image"
        previous_id = db.save_message(
            "user", "生成一张森林图片", source="desktop", conversation_id=conversation_id,
        )
        with db.get_conn() as conn:
            previous = conn.execute(
                "SELECT created_at FROM messages WHERE id = ?", (previous_id,)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO creation_jobs(
                    id, conversation_id, source, media_type, backend, workflow_id,
                    status, stage, progress, spec_json, preset_snapshot_json,
                    confirmation_reason, idempotency_key, parent_job_id, created_at, updated_at
                ) VALUES ('job_existing', ?, 'desktop', 'image', 'comfyui',
                    'anima-2.9b-image', 'created', 'created', 0, '{}', '{}', '',
                    'existing-job', '', ?, ?)
                """,
                (conversation_id, previous["created_at"], previous["created_at"]),
            )
        source_message_id = db.save_message(
            "user", "再试试", source="desktop", conversation_id=conversation_id,
        )
        resolved = _resolve_agent_creation_message(
            "再试试", conversation_id, source_message_id,
            {"creation_list_presets", "creation_check_workflow", "comfyui_generate_image"},
        )
        self.assertEqual(resolved, "再试试")

    def test_agent_workspace_continuation_inherits_style_but_not_seed_or_character(self) -> None:
        conversation_id = "desktop_agent_continue_image"
        timestamp = db.now_iso()
        spec = {
            "prompt": "纳西妲在森林里",
            "negative_prompt": "blurry, watermark",
            "character_preset_id": "nahida",
            "style_preset_id": "soft-anime",
            "width": 864,
            "height": 1536,
            "lora_choices": ["soft_cel_pastel"],
            "seed": 12345,
        }
        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO creation_jobs(
                    id, conversation_id, source, media_type, backend, workflow_id,
                    status, stage, progress, spec_json, preset_snapshot_json,
                    confirmation_reason, idempotency_key, parent_job_id, created_at, updated_at
                ) VALUES ('job_previous', ?, 'desktop', 'image', 'comfyui',
                    'anima-2.9b-image', 'completed', 'completed', 1, ?, '{}', '',
                    'continue-base', '', ?, ?)
                """,
                (conversation_id, json.dumps(spec, ensure_ascii=False), timestamp, timestamp),
            )
        resolved = _resolve_agent_creation_message(
            "再来一张其他角色的",
            conversation_id,
            999,
            {"comfyui_generate_image"},
        )
        self.assertIn("请生成一份新的图片", resolved)
        self.assertIn("不得继承 seed", resolved)

        calls = _inherit_continuation_arguments(
            [PlannedToolCall(
                "next-image",
                "comfyui_generate_image",
                {"prompt": "刻晴在同一片森林里", "negative_prompt": ""},
            )],
            "再来一张其他角色的",
            conversation_id,
        )
        arguments = calls[0].arguments
        self.assertEqual(arguments["style_preset_id"], "soft-anime")
        self.assertEqual(arguments["lora_choices"], ["soft_cel_pastel"])
        self.assertEqual((arguments["width"], arguments["height"]), (864, 1536))
        self.assertNotIn("character_preset_id", arguments)
        self.assertEqual(arguments["seed"], -1)
        self.assertEqual(arguments["parent_job_id"], "job_previous")

    def test_agent_workspace_status_question_reads_latest_real_job(self) -> None:
        conversation_id = "desktop_agent_status_image"
        timestamp = db.now_iso()
        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO creation_jobs(
                    id, conversation_id, source, media_type, backend, workflow_id,
                    status, stage, progress, spec_json, preset_snapshot_json,
                    confirmation_reason, idempotency_key, parent_job_id, created_at, updated_at
                ) VALUES ('job_status', ?, 'desktop', 'image', 'comfyui',
                    'anima-2.9b-image', 'running', 'sampling', 0.5, '{}', '{}', '',
                    'status-key', '', ?, ?)
                """,
                (conversation_id, timestamp, timestamp),
            )

        call = _creation_job_follow_up_call(
            "我的图呢，好了吗？",
            conversation_id,
            {"creation_get_job"},
        )

        self.assertIsNotNone(call)
        self.assertEqual(call.name, "creation_get_job")
        self.assertEqual(call.arguments, {"job_id": "job_status"})

    def test_false_agent_completion_claim_is_replaced_without_verified_output(self) -> None:
        from app.chat_service import _guard_agent_creation_completion_claim
        from app.agent_loop_service import AgentLoopResult

        execution = AgentLoopResult(
            "run_guard", "awaiting_response", "native",
            (ToolExecutionResult("creation_check_workflow", "completed", {"ok": True}, 1),),
            (), False, 2,
        )
        guarded = _guard_agent_creation_completion_claim(
            ["纳西妲这张已经生成出来了"], "desktop_agent_guard", execution,
        )
        self.assertEqual(guarded, ["这轮没有创建图片或视频任务，所以还没有成品。我不会把工作流检查通过说成已经生成。"])

        false_submission = _guard_agent_creation_completion_claim(
            ["任务已经提交到后台跑起来了"], "desktop_agent_guard", execution,
        )
        self.assertEqual(false_submission, ["这轮没有创建图片或视频任务，所以还没有成品。我不会把工作流检查通过说成已经生成。"])

        direct_submission = _guard_agent_creation_completion_claim(
            ["嗯，工作流是就绪的，我直接提交生成任务了"], "desktop_agent_guard", execution,
        )
        self.assertEqual(direct_submission, ["这轮没有创建图片或视频任务，所以还没有成品。我不会把工作流检查通过说成已经生成。"])

    def test_creation_completion_message_is_idempotent_and_contains_verified_output(self) -> None:
        from app.creation_service import _notify_chat_job_completion

        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO creation_jobs(
                    id, conversation_id, source, media_type, backend, workflow_id,
                    status, stage, progress, spec_json, preset_snapshot_json,
                    confirmation_reason, idempotency_key, parent_job_id, created_at, updated_at,
                    started_at, finished_at
                ) VALUES ('job_notify', 'desktop_chat', 'desktop', 'image', 'comfyui',
                    'anima-2.9b-image', 'completed', 'completed', 1.0, '{}', '{}', '',
                    'notify-key', '', '2026-09-02T00:00:00+00:00', '2026-09-02T00:00:00+00:00',
                    '2026-09-02T00:00:00+00:00', '2026-09-02T00:01:12.200000+00:00')
                """
            )
        output = [{
            "name": "image.png", "media_type": "image", "mime_type": "image/png",
            "size": 12, "sha256": "abc",
        }]
        _notify_chat_job_completion("job_notify", output)
        _notify_chat_job_completion("job_notify", output)
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT content, attachments_json, request_cost_yuan, request_cost_source, total_latency_ms "
                "FROM messages WHERE delivery_key = ?",
                ("creation-complete:job_notify",),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn("创作完成", rows[0]["content"])
        self.assertIn("1 分 12.2 秒", rows[0]["content"])
        self.assertEqual(rows[0]["request_cost_yuan"], 0.0)
        self.assertEqual(rows[0]["request_cost_source"], "local_comfyui")
        self.assertEqual(rows[0]["total_latency_ms"], 72200.0)
        self.assertEqual(json.loads(rows[0]["attachments_json"])[0]["url"], "/api/creation/jobs/job_notify/outputs/0")


if __name__ == "__main__":
    unittest.main()
