import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.conversation_runtime import ConversationRunCoordinator, RuntimeTraceStore, ChatRunCancelledError


class ChatCancelIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_cancel_does_not_stop_new_request(self):
        coordinator=ConversationRunCoordinator(RuntimeTraceStore())
        started,finish=asyncio.Event(),asyncio.Event()
        async def runner():
            started.set();await finish.wait();return 'new response'
        pending=asyncio.create_task(coordinator.submit('same','desktop',runner,capture_seconds=0,request_id='new'))
        await started.wait()
        self.assertEqual(await coordinator.cancel('same',source='desktop',expected_request_id='old'),0)
        finish.set();self.assertEqual(await pending,'new response')

    async def test_current_cancel_stops_matching_request(self):
        coordinator=ConversationRunCoordinator(RuntimeTraceStore())
        started=asyncio.Event()
        async def runner():
            started.set();await asyncio.Event().wait()
        pending=asyncio.create_task(coordinator.submit('same','desktop',runner,request_id='current'))
        await started.wait()
        self.assertEqual(await coordinator.cancel('same',source='desktop',expected_request_id='current'),1)
        with self.assertRaises(ChatRunCancelledError):await pending

    async def test_cancel_queued_request_keeps_other_requests(self):
        coordinator=ConversationRunCoordinator(RuntimeTraceStore())
        started,finish=asyncio.Event(),asyncio.Event()
        async def active():
            started.set();await finish.wait();return 'active'
        async def queued():return 'queued'
        # Equal clock readings reproduce the zero-window boundary without
        # changing the event loop's own clock or relying on a scheduling delay.
        with patch('app.conversation_runtime.time', SimpleNamespace(monotonic=lambda: 42.0)):
            running=asyncio.create_task(coordinator.submit('same','desktop',active,capture_seconds=0,request_id='active'))
            await started.wait()
            coordinator._states['same'].active.started_at = 42.0
            queued_task=asyncio.create_task(coordinator.submit('same','desktop',queued,capture_seconds=0,request_id='queued'))
            await asyncio.sleep(0)
            self.assertEqual(await coordinator.cancel('same',source='desktop',expected_request_id='queued'),1)
            with self.assertRaises(ChatRunCancelledError):await queued_task
            finish.set();self.assertEqual(await running,'active')

    async def test_captured_followup_replaces_cancellation_identity(self):
        coordinator=ConversationRunCoordinator(RuntimeTraceStore())
        first_started,second_started,finish=asyncio.Event(),asyncio.Event(),asyncio.Event()
        async def first():
            first_started.set();await asyncio.Event().wait()
        async def second():
            second_started.set();await finish.wait();return 'second'
        one=asyncio.create_task(coordinator.submit('same','desktop',first,capture_seconds=10,request_id='first'))
        await first_started.wait()
        two=asyncio.create_task(coordinator.submit('same','desktop',second,capture_seconds=10,request_id='second'))
        await second_started.wait()
        self.assertEqual(await coordinator.cancel('same',source='desktop',expected_request_id='first'),0)
        finish.set();self.assertEqual(await asyncio.gather(one,two),['second','second'])

    async def test_legacy_source_cancellation_is_preserved(self):
        coordinator=ConversationRunCoordinator(RuntimeTraceStore())
        started=asyncio.Event()
        async def runner():
            started.set();await asyncio.Event().wait()
        pending=asyncio.create_task(coordinator.submit('same','qq',runner))
        await started.wait()
        self.assertEqual(await coordinator.cancel('same',source='desktop'),0)
        self.assertEqual(await coordinator.cancel('same',source='qq'),1)
        with self.assertRaises(ChatRunCancelledError):await pending
