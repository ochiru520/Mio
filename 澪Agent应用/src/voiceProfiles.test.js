import assert from 'node:assert/strict'
import test from 'node:test'

import { MAX_VOICE_PROFILES, createVoiceProfile, removeVoiceProfile } from './voiceProfiles.js'

test('创建音色会生成稳定 ID 和公开默认字段', () => {
  const created = createVoiceProfile({}, '  日语旁白  ', 12345)

  assert.equal(created.id, 'voice-9ix')
  assert.equal(created.profile.name, '日语旁白')
  assert.equal(created.profile.gpt_sovits_prompt_language, 'zh')
  assert.equal(created.profile.gpt_sovits_ref_audio, '')
})

test('创建音色会避开重复 ID 并限制最多二十个', () => {
  const first = createVoiceProfile({ 'voice-9ix': {} }, '备用', 12345)
  const full = Object.fromEntries(Array.from({ length: MAX_VOICE_PROFILES }, (_, index) => [`v${index}`, {}]))

  assert.equal(first.id, 'voice-9ix-1')
  assert.equal(createVoiceProfile(full, '超出上限', 12345), null)
})

test('删除默认音色会回退并清理失效的模型绑定', () => {
  const result = removeVoiceProfile(
    { calm: { name: '沉静' }, bright: { name: '明快' } },
    { modelA: 'calm', modelB: 'bright' },
    'bright',
    'bright',
  )

  assert.deepEqual(result.profiles, { calm: { name: '沉静' } })
  assert.deepEqual(result.bindings, { modelA: 'calm' })
  assert.equal(result.defaultId, 'calm')
  assert.equal(result.selectedId, 'calm')
})
