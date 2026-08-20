import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildTurnVoicePayload,
  detectTurnVoiceLanguage,
  turnVoiceModeLabel,
  voiceLanguageLabel,
} from './chatVoice.js'

test('整轮语音包含同一轮的全部短句', () => {
  const payload = buildTurnVoicePayload({
    model_id: 'deepseek-v4-flash',
    parts: [
      { content: '第一句' },
      { content: '第二句' },
      { content: '第三句' },
    ],
  })

  assert.deepEqual(payload, {
    text: '第一句\n第二句\n第三句',
    model_id: 'deepseek-v4-flash',
    language: 'zh',
  })
})

test('整轮语音忽略清理后为空的消息', () => {
  const payload = buildTurnVoicePayload(
    { parts: [{ content: '保留' }, { content: '[内部消息时间]' }] },
    (content) => content.startsWith('[') ? '' : content,
  )

  assert.equal(payload.text, '保留')
})

test('自动识别中文和日语语音', () => {
  assert.equal(detectTurnVoiceLanguage('今晚早点休息'), 'zh')
  assert.equal(detectTurnVoiceLanguage('今日は一緒に帰ろうね'), 'ja')
  assert.equal(turnVoiceModeLabel('今晚早点休息'), '中文原文')
  assert.equal(turnVoiceModeLabel('今日は一緒に帰ろうね'), '日语原文')
  assert.equal(turnVoiceModeLabel('今晚早点休息', 'ja'), '日语翻译')
  assert.equal(turnVoiceModeLabel('今日は一緒に帰ろうね', 'zh'), '中文翻译')
})

test('手动语言设置优先于正文识别', () => {
  const payload = buildTurnVoicePayload(
    { parts: [{ content: '晚上好' }] },
    undefined,
    'ja',
  )

  assert.equal(payload.language, 'ja')
  assert.equal(voiceLanguageLabel('ja'), '日语语音')
  assert.equal(voiceLanguageLabel('zh'), '中文语音')
})
