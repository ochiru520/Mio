from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import chat_service as chat
from app.llm import CompletionResult
from app.reply_text_stream import PublicReplyStream


def completion(text):
    return CompletionResult(content=text, model='synthetic', prompt_tokens=10,
                            cached_prompt_tokens=0, completion_tokens=10,
                            reasoning_tokens=0, cost_yuan=0, cost_source='test')


class PublicReplyStreamTests(unittest.IsolatedAsyncioTestCase):
    def make_filter(self):
        seen = []
        async def collect(piece, cumulative):
            seen.append((piece, cumulative))
        return PublicReplyStream(collect, clean=chat.clean_chat_reply,
                                 is_placeholder=chat._is_placeholder_only_reply), seen

    async def test_hidden_controls_split_at_every_character_never_reach_callback(self):
        stream, seen = self.make_filter()
        raw = '<think>内部推理。不可播放。</think>[[voice_emotion:gentle]]你好。<analysis>不要播这个。</analysis>今天很好。'
        for char in raw:
            await stream.delta(char, '')
        await stream.finish()
        self.assertEqual(''.join(piece for piece, _ in seen), '你好。今天很好。')
        self.assertEqual(seen[-1][1], '你好。今天很好。')

    async def test_incomplete_control_or_tool_block_is_not_flushed(self):
        for tail in ['<think>秘密。', '<thi', '[[voice_emotion:gentle', '<｜DSML｜calls>秘密。']:
            with self.subTest(tail=tail):
                stream, seen = self.make_filter()
                for char in '你好。' + tail:
                    await stream.delta(char, '')
                await stream.finish()
                self.assertEqual(''.join(piece for piece, _ in seen), '你好。')

    async def test_unlabelled_draft_waits_for_final_marker(self):
        stream, seen = self.make_filter()
        await stream.delta('We need to answer the user.\nNeed ask politely.\n', '')
        self.assertEqual(seen, [])
        await stream.delta('Final: 你好。', '')
        self.assertEqual(seen, [('你好。', '你好。')])

    async def test_unpunctuated_tail_is_only_emitted_on_finish(self):
        stream, seen = self.make_filter()
        await stream.delta('你好。还有一点', '')
        self.assertEqual(seen, [('你好。', '你好。')])
        await stream.finish()
        self.assertEqual(seen[-1], ('还有一点', '你好。还有一点'))

    async def test_placeholder_is_not_published(self):
        stream, seen = self.make_filter()
        await stream.delta('我想想。', '')
        await stream.finish()
        self.assertEqual(seen, [])


class ChatReplyDeltaTests(unittest.IsolatedAsyncioTestCase):
    def options(self, **kwargs):
        return dict(temperature=.5, model_id='first', model_name='synthetic',
                    reasoning_level='off', request_id='test', **kwargs)

    async def test_first_sentence_is_delivered_before_model_finishes(self):
        first = asyncio.Event()
        release = asyncio.Event()
        seen = []
        async def collect(piece, cumulative):
            seen.append(piece); first.set()
        async def provider(messages, *, on_delta, **kwargs):
            await on_delta('[[voice_emotion:gentle]]你好。', '[[voice_emotion:gentle]]你好。')
            await release.wait()
            await on_delta('后一句', '[[voice_emotion:gentle]]你好。后一句')
            return completion('[[voice_emotion:gentle]]你好。后一句')
        with patch.object(chat, 'get_model_profile', return_value=SimpleNamespace(api_mode='chat_completions')), patch.object(chat, 'call_chat_completion_stream_result', side_effect=provider), patch.object(chat, '_record_completion_latency'):
            task = asyncio.create_task(chat._complete_chat_reply([], on_reply_delta=collect, **self.options()))
            await asyncio.wait_for(first.wait(), 1)
            self.assertFalse(task.done())
            self.assertEqual(seen, ['你好。'])
            release.set()
            result, _ = await task
        self.assertEqual(result.content, '[[voice_emotion:gentle]]你好。后一句')
        self.assertEqual(seen, ['你好。', '后一句'])

    async def test_default_chat_keeps_nonstreaming_path(self):
        original = AsyncMock(return_value=completion('你好。'))
        streaming = AsyncMock()
        with patch.object(chat, 'call_chat_completion_result', original), patch.object(chat, 'call_chat_completion_stream_result', streaming), patch.object(chat, '_record_completion_latency'):
            await chat._complete_chat_reply([], **self.options())
        original.assert_awaited_once()
        streaming.assert_not_awaited()

    async def test_recovered_final_answer_also_flushes_unpunctuated_tail(self):
        seen = []
        async def collect(piece, cumulative):
            seen.append(piece)
        raw = 'We need to answer.\nNeed ask politely.\nFinal: 你好。后半句'
        async def provider(messages, *, on_delta, **kwargs):
            await on_delta(raw, raw)
            return completion(raw)
        with patch.object(chat, 'get_model_profile', return_value=SimpleNamespace(api_mode='chat_completions')), patch.object(chat, 'call_chat_completion_stream_result', side_effect=provider), patch.object(chat, '_record_completion_latency'):
            result, _ = await chat._complete_chat_reply([], on_reply_delta=collect, **self.options())
        self.assertEqual(result.content, '你好。后半句')
        self.assertEqual(seen, ['你好。', '后半句'])

    async def test_callback_is_forwarded_through_chat_entry_and_serialization(self):
        callback = AsyncMock()
        expected = object()
        unlocked = AsyncMock(return_value=expected)
        async def submit(conversation, source, runner, **kwargs):
            return await runner()
        with patch.object(chat.chat_run_coordinator, 'submit', side_effect=submit), patch.object(chat, '_chat_with_ai_unlocked', unlocked):
            result = await chat.chat_with_ai('测试', conversation_id='reply-delta-test', on_reply_delta=callback)
        self.assertIs(result, expected)
        self.assertIs(unlocked.await_args.kwargs['on_reply_delta'], callback)

    async def test_messages_protocol_and_tools_keep_original_path(self):
        for api_mode, tools in [('messages', None), ('chat_completions', [{'type':'function'}])]:
            with self.subTest(api_mode=api_mode, tools=tools):
                original = AsyncMock(return_value=completion('你好。'))
                streaming = AsyncMock()
                callback = AsyncMock()
                with patch.object(chat, 'get_model_profile', return_value=SimpleNamespace(api_mode=api_mode)), patch.object(chat, 'call_chat_completion_result', original), patch.object(chat, 'call_chat_completion_stream_result', streaming), patch.object(chat, '_record_completion_latency'):
                    await chat._complete_chat_reply([], on_reply_delta=callback, tools=tools, **self.options())
                original.assert_awaited_once(); streaming.assert_not_awaited(); callback.assert_not_awaited()

    async def test_failure_after_public_text_does_not_invoke_fallback(self):
        callback = AsyncMock()
        async def provider(messages, *, on_delta, **kwargs):
            await on_delta('你好。', '你好。')
            raise TimeoutError('stream stopped')
        streaming = AsyncMock(side_effect=provider)
        with patch.object(chat, 'get_model_profile', return_value=SimpleNamespace(api_mode='chat_completions')), patch.object(chat, 'call_chat_completion_stream_result', streaming), patch.object(chat, 'require_configured') as configured:
            with self.assertRaisesRegex(TimeoutError, 'stream stopped'):
                await chat._complete_chat_reply_with_single_fallback([], on_reply_delta=callback, fallback_model_id='fallback', **self.options())
        self.assertEqual(streaming.await_count, 1)
        configured.assert_not_called()
        callback.assert_awaited_once_with('你好。', '你好。')

    async def test_failure_before_public_text_keeps_one_fallback(self):
        callback = AsyncMock()
        async def provider(messages, *, on_delta, model_id, **kwargs):
            if model_id == 'first':
                await on_delta('<think>内部草稿。', '')
                raise TimeoutError('no public result')
            await on_delta('备用回答。', '备用回答。')
            return completion('备用回答。')
        streaming = AsyncMock(side_effect=provider)
        profile = SimpleNamespace(api_mode='chat_completions', model='synthetic')
        with patch.object(chat, 'get_model_profile', return_value=profile), patch.object(chat, 'call_chat_completion_stream_result', streaming), patch.object(chat, 'require_configured'), patch.object(chat, '_record_completion_latency'), patch.object(chat, 'normalize_model_reasoning', return_value='off'):
            result, _, fallback_from = await chat._complete_chat_reply_with_single_fallback([], on_reply_delta=callback, fallback_model_id='fallback', **self.options())
        self.assertEqual(result.content, '备用回答。')
        self.assertEqual(fallback_from, 'first')
        self.assertEqual(streaming.await_count, 2)
        callback.assert_awaited_once_with('备用回答。', '备用回答。')

    async def test_cancellation_is_not_caught_as_model_fallback(self):
        streaming = AsyncMock(side_effect=asyncio.CancelledError)
        with patch.object(chat, 'get_model_profile', return_value=SimpleNamespace(api_mode='chat_completions')), patch.object(chat, 'call_chat_completion_stream_result', streaming), patch.object(chat, 'require_configured') as configured:
            with self.assertRaises(asyncio.CancelledError):
                await chat._complete_chat_reply_with_single_fallback([], on_reply_delta=AsyncMock(), fallback_model_id='fallback', **self.options())
        self.assertEqual(streaming.await_count, 1)
        configured.assert_not_called()


if __name__ == '__main__':
    unittest.main()
