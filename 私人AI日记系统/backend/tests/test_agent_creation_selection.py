from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import agent_task_service as tasks, agent_tool_service, db
from app.agent_creation_selection import bind_selected_workflow
from app.agent_loop_service import run_agent_loop
from app.agent_tool_service import ToolExecutionContext, ToolExecutionResult, execute_tool_call
from app.chat_service import ChatResult
from app.llm import ToolCall
from app.routes.agent import AgentChatRequest, agent_chat
from tests.test_agent_model_first import _completion


QWEN = 'qwen-2.1-image'
ANIMA = 'anima-2.9b-image'
VIDEO = 'minimax-h3-video'


class CreationSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = db.settings.db_path
        object.__setattr__(db.settings, 'db_path', Path(self.temp.name) / 'test.db')
        db.init_db()
        self.conversation = 'desktop_agent_selection'
        db.create_agent_conversation(self.conversation)
        self.message_id = db.save_message('user', '画一只戴蓝色围巾的白猫，中文插画风格', conversation_id=self.conversation)

    def tearDown(self):
        object.__setattr__(db.settings, 'db_path', self.old_path)
        self.temp.cleanup()

    async def run_loop(self, selection, calls, **kwargs):
        planner = AsyncMock(side_effect=[_completion(*calls), _completion()])
        execute = AsyncMock(return_value=ToolExecutionResult('comfyui_generate_image', 'completed', {'done': True}, 1))
        with patch('app.agent_loop_service.call_chat_completion_result', planner), patch('app.agent_loop_service.execute_tool_call', execute):
            result = await run_agent_loop(conversation_id=self.conversation, source='desktop',
                user_message='画一只戴蓝色围巾的白猫，中文插画风格', source_message_id=self.message_id,
                request_id='selection-test', trace_id='test', model_id='test-model', reasoning_level='low',
                creation_workflow_id=selection, **kwargs)
        return result, planner, execute

    async def test_qwen_is_bound_before_anima_language_validation_and_saved(self):
        result, planner, execute = await self.run_loop(QWEN, [ToolCall('generate', 'comfyui_generate_image', json.dumps({'prompt': '画一只戴蓝色围巾的白猫，中文插画风格'}))])
        execute.assert_awaited_once()
        name, arguments, context = execute.await_args.args
        self.assertEqual(arguments['workflow_id'], QWEN)
        self.assertNotIn('negative_prompt', arguments)
        self.assertEqual(context.creation_workflow_id, QWEN)
        self.assertEqual(tasks.get(result.task_id)['snapshot']['creation_workflow_id'], QWEN)
        # Lazy schema exposure must preserve the authorized capability set.
        names = set(tasks.get(result.task_id)['allowed_tools'])
        schemas = {tool['function']['name'] for tool in planner.await_args_list[0].kwargs['tools']}
        self.assertIn('agent_discover_tools', schemas)
        self.assertIn('creation_use_reference', names)
        self.assertNotIn('remote_generate_image', names)
        self.assertNotIn('comfyui_generate_video', names)
        self.assertIn(QWEN, json.dumps(planner.await_args_list[0].args[0], ensure_ascii=False))

    async def test_wrong_workflow_is_rejected_instead_of_silently_substituted(self):
        result, _, execute = await self.run_loop(QWEN, [ToolCall('wrong', 'comfyui_generate_image', json.dumps({'prompt': 'cat', 'workflow_id': ANIMA}))])
        execute.assert_not_awaited()
        self.assertIn(QWEN, result.observations[0].error)
        self.assertEqual(result.observations[0].status, 'failed')

    async def test_video_selection_keeps_reference_tools_but_blocks_image_and_remote(self):
        result, planner, execute = await self.run_loop(VIDEO, [ToolCall('wrong', 'remote_generate_image', json.dumps({'prompt': 'cat'}))])
        execute.assert_not_awaited()
        # Lazy schema exposure must preserve the authorized capability set.
        names = set(tasks.get(result.task_id)['allowed_tools'])
        schemas = {tool['function']['name'] for tool in planner.await_args_list[0].kwargs['tools']}
        self.assertIn('agent_discover_tools', schemas)
        self.assertIn('comfyui_generate_video', names)
        self.assertIn('creation_use_reference', names)
        self.assertNotIn('comfyui_generate_image', names)
        self.assertTrue(result.observations[0].error)

    async def test_auto_keeps_all_tools_and_does_not_force_generation(self):
        result, planner, execute = await self.run_loop('', [])
        execute.assert_not_awaited()
        # Lazy schema exposure must preserve the authorized capability set.
        names = set(tasks.get(result.task_id)['allowed_tools'])
        schemas = {tool['function']['name'] for tool in planner.await_args_list[0].kwargs['tools']}
        self.assertIn('agent_discover_tools', schemas)
        self.assertTrue({'comfyui_generate_image', 'comfyui_generate_video', 'remote_generate_image'} <= names)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(tasks.get(result.task_id)['snapshot']['creation_workflow_id'], '')

    async def test_background_continuation_keeps_selection(self):
        result, _, _ = await self.run_loop(QWEN, [])
        tasks.update(result.task_id, status='ready')
        loop = AsyncMock(return_value=replace(result, status='completed'))
        with patch('app.agent_loop_service.run_agent_loop', loop), patch('app.agent_loop_service.final_model_call', AsyncMock(return_value=_completion())):
            await tasks.continue_task(result.task_id)
        loop.assert_awaited_once()
        self.assertEqual(loop.await_args.kwargs['creation_workflow_id'], QWEN)

    async def test_execution_binds_selection_before_job_and_receipt(self):
        db.create_agent_run('run-selection', 'exec-selection', conversation_id=self.conversation, source='desktop', source_message_id=self.message_id, model_id='test-model')
        context = ToolExecutionContext('run-selection', 'exec-selection', 'trace', self.conversation,
            self.message_id, '画一只白猫', 1, creation_workflow_id=QWEN)
        # Exercise availability using an isolated installation, including on CI.
        comfyui = Path(self.temp.name) / 'ComfyUI'
        comfyui.mkdir()
        (comfyui / 'main.py').write_text('# test installation\n', encoding='utf-8')
        with patch.object(agent_tool_service, 'settings', replace(agent_tool_service.settings, comfyui_root=comfyui)), \
                patch('app.creation_service.create_job', return_value=({'id': 'job_selected', 'status': 'created'}, True)) as create:
            result = await execute_tool_call('comfyui_generate_image', {'prompt': '一只白猫'}, context)
        self.assertEqual(result.status, 'completed', result.error)
        self.assertEqual(create.call_args.args[0].workflow_id, QWEN)
        step = db.list_agent_run_steps('run-selection')[0]
        self.assertEqual(json.loads(step['arguments_json'])['workflow_id'], QWEN)
        receipt = db.list_tool_execution_receipts(limit=1)[0]
        self.assertEqual(receipt['id'], result.receipt_id)
        self.assertEqual(receipt['status'], 'executed')
        self.assertEqual(json.loads(receipt['request_json'])['workflow_id'], QWEN)
        with self.assertRaises(ValueError):
            await execute_tool_call('comfyui_generate_video', {'prompt': 'cat'}, context)

    async def test_route_forwards_selection_and_idempotency_distinguishes_tools(self):
        chat = AsyncMock(return_value=ChatResult(reply='收到', replies=['收到'], model_id='test-model'))
        payload = AgentChatRequest(message='画一只猫', model_id='test-model', conversation_id=self.conversation,
            agent_workspace=True, creation_workflow_id=QWEN, client_request_id='selection-route')
        with patch('app.routes.agent.resolve_model_id', return_value='test-model'), patch('app.routes.agent.chat_with_ai', chat):
            await agent_chat(payload)
            self.assertEqual(chat.await_args.kwargs['creation_workflow_id'], QWEN)
            with self.assertRaises(HTTPException) as error:
                await agent_chat(payload.model_copy(update={'creation_workflow_id': ANIMA}))
            self.assertEqual(error.exception.status_code, 400)
            chat.assert_awaited_once()

    async def test_route_rejects_unregistered_or_companion_selection(self):
        for workflow, agent in [('unknown', True), (QWEN, False)]:
            with self.subTest(workflow=workflow, agent=agent), self.assertRaises(HTTPException) as error:
                await agent_chat(AgentChatRequest(message='test', conversation_id=self.conversation, agent_workspace=agent, creation_workflow_id=workflow))
            self.assertEqual(error.exception.status_code, 400)

    def test_discovery_defaults_to_selection_and_explicit_inspection_is_preserved(self):
        self.assertEqual(bind_selected_workflow('creation_check_workflow', {}, QWEN), {'workflow_id': QWEN})
        self.assertEqual(bind_selected_workflow('creation_list_loras', {'workflow_id': ANIMA}, QWEN), {'workflow_id': ANIMA})
        self.assertEqual(bind_selected_workflow('get_today_state', {}, QWEN), {})

    async def test_catalog_does_not_probe_comfyui_or_fetch_job_history(self):
        from app.routes.creation import creation_workflows
        with patch('app.creation_service.comfyui_health', AsyncMock()) as health, patch('app.creation_service.list_jobs') as jobs:
            result = await creation_workflows()
        health.assert_not_awaited()
        jobs.assert_not_called()
        self.assertTrue({ANIMA, QWEN, VIDEO} <= {item['id'] for item in result['workflows']})
