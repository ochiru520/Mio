import test from 'node:test'
import assert from 'node:assert/strict'
import { isRef } from 'vue'
import { createConversationState } from './state/conversationState.js'
import { createCompanionState, createPetCallRuntime } from './state/companionState.js'
import { createRecordsState } from './state/recordsState.js'
import { createTasksState } from './state/tasksState.js'
import { createPrivacyState } from './state/privacyState.js'
import { createQqState } from './state/qqState.js'
import { createProvidersState } from './state/providersState.js'
import { createSettingsState } from './state/settingsState.js'

test('each window owns independent reactive domain state and nested defaults', () => {
  for (const factory of [createConversationState, createCompanionState, createRecordsState,
    createTasksState, createPrivacyState, createQqState, createProvidersState]) {
    const first = factory()
    const second = factory()
    for (const [key, ref] of Object.entries(first)) {
      assert.ok(isRef(ref), `${factory.name}.${key}`)
      assert.notEqual(ref, second[key])
      if (ref.value && typeof ref.value === 'object') assert.notEqual(ref.value, second[key].value)
    }
  }
  const first = createCompanionState()
  const second = createCompanionState()
  first.companionStatus.value.pet.settings.voice_profiles.mio.name = '独立角色'
  assert.equal(second.companionStatus.value.pet.settings.voice_profiles.mio.name, 'Mio 专属音色')
  const chat = createConversationState()
  chat.messages.value.push({ content: '保留同一份消息状态' })
  assert.deepEqual(createConversationState().messages.value, [])
})

test('settings keep agent/chat sections and sidebar keys separate without sharing saved drafts', () => {
  const reads = []
  const storage = { getItem(key) {
    reads.push(key)
    return ({ mio_agent_settings_section: 'tools', mio_settings_section: 'voice',
      mio_agent_right_sidebar_visible: 'true', mio_agent_right_sidebar_pinned: 'true' })[key] ?? null
  } }
  const original = { focus_mode: true, left_sidebar_visible: true, right_sidebar_visible: true,
    remember_sidebar_state: true, visibility: { message_time: true }, home_widgets: { diary: true } }
  const options = { initialAppPreferences: original, cloneAppPreferences: value => JSON.parse(JSON.stringify(value)), storage }
  const chat = createSettingsState({ ...options, isAgentWorkspace: false })
  const agent = createSettingsState({ ...options, isAgentWorkspace: true })
  assert.equal(chat.activeSettingsSection.value, 'voice')
  assert.equal(agent.activeSettingsSection.value, 'tools')
  assert.equal(chat.rightSidebarVisible.value, false)
  assert.equal(agent.rightSidebarVisible.value, true)
  assert.equal(agent.rightSidebarPinned.value, true)
  assert.equal(chat.rightSidebarPinned.value, false)
  assert.ok(reads.includes('mio_agent_settings_section') && reads.includes('mio_settings_section'))
  agent.appPreferencesDraft.value.visibility.message_time = false
  assert.equal(agent.savedAppPreferences.value.visibility.message_time, true)
  assert.equal(chat.appPreferencesDraft.value.visibility.message_time, true)
  assert.equal(original.visibility.message_time, true)
})

test('media resources have one mutable owner per call and preserve native object identity', () => {
  const first = createPetCallRuntime()
  const second = createPetCallRuntime()
  const nativeStream = { stop() {} }
  first.petCallStream = nativeStream
  first.petCallFrames.push(new Float32Array([0.25]))
  first.petCallNextTurnId += 1
  assert.equal(first.petCallStream, nativeStream)
  assert.equal(second.petCallStream, null)
  assert.deepEqual(second.petCallFrames, [])
  assert.equal(second.petCallNextTurnId, 1)
  assert.equal(second.petCallMonitorTimer, null)
})
