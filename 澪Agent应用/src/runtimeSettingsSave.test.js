import assert from 'node:assert/strict'
import test from 'node:test'
import { useSettings } from './composables/useSettings.js'

function setup(t, { conflict = false, restart = false } = {}) {
  const server = { qq_proactive_enabled: false, daily_diary_check_seconds: 60 }
  let sent
  const messages = []
  const sandbox = {
    runtimeSettingsBusy: { value: false }, runtimeSettingsReady: { value: true },
    errorMessage: { value: '' }, runtimeSettingsRevision: { value: 'version-a' },
    runtimeSettingsDraft: { value: { qq_proactive_enabled: true, daily_diary_check_seconds: 90 } },
    savedRuntimeSettings: { value: '' },
    savedRuntimeSettingsSource: () => ({ qq_proactive_enabled: true, daily_diary_check_seconds: 60 }),
    runtimeSettingKeysBySection: { diary: ['daily_diary_check_seconds'] }, privateRuntimePathKeys: [],
    activeSettingsSection: { value: 'diary' }, activeSettingsItem: { value: { label: '日记' } },
    serializeSettings: JSON.stringify, loadBootstrap: async () => {},
    showSettingsFeedback: (_section, type, message) => messages.push({ type, message }),
  }
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/settings/runtime')
    if (!options.method || options.method === 'GET') return Response.json({ settings: { ...server, daily_diary_check_seconds: 120 }, revision: 'version-b' })
    assert.equal(options.method, 'PATCH')
    sent = { headers: options.headers, payload: JSON.parse(options.body) }
    if (conflict) return Response.json({ detail: { message: '设置已被其他窗口更新', revision: 'version-b' } }, { status: 409 })
    Object.assign(server, sent.payload)
    return Response.json({ settings: server, revision: 'version-b', application: { restart_required: restart ? ['voice_training_dir'] : [] } })
  })
  Object.assign(sandbox, useSettings(sandbox))
  return { sandbox, server, messages, sent: () => sent }
}

test('saving a category leaves externally changed switches intact and updates revision', async t => {
  const state = setup(t)
  assert.equal(await state.sandbox.saveRuntimeSettings(), true)
  assert.deepEqual(state.sent().payload, { daily_diary_check_seconds: 90 })
  assert.equal(state.sent().headers['If-Match'], 'version-a')
  assert.equal(state.server.qq_proactive_enabled, false)
  assert.equal(state.sandbox.runtimeSettingsRevision.value, 'version-b')
  assert.deepEqual(JSON.parse(state.sandbox.savedRuntimeSettings.value), state.server)
})

test('a revision conflict keeps the draft and does not report success', async t => {
  const state = setup(t, { conflict: true })
  const originalDraft = state.sandbox.runtimeSettingsDraft.value
  const originalSaved = state.sandbox.savedRuntimeSettings.value
  const reload = t.mock.fn()
  state.sandbox.loadBootstrap = reload
  assert.equal(await state.sandbox.saveRuntimeSettings(), false)
  assert.equal(state.sandbox.runtimeSettingsDraft.value.daily_diary_check_seconds, 90)
  assert.equal(state.sandbox.runtimeSettingsRevision.value, 'version-a')
  assert.equal(state.sandbox.runtimeSettingsDraft.value, originalDraft)
  assert.equal(state.sandbox.savedRuntimeSettings.value, originalSaved)
  assert.match(state.sandbox.errorMessage.value, /同一项设置.*你的修改已保留/)
  assert.equal(reload.mock.callCount(), 0)
  assert.equal(state.messages.at(-1).type, 'error')
  assert.equal(state.sandbox.runtimeSettingsBusy.value, false)
})

test('saved component paths show restart required instead of claiming immediate application', async t => {
  const state = setup(t, { restart: true })
  assert.equal(await state.sandbox.saveRuntimeSettings(), true)
  assert.match(state.messages.at(-1).message, /重启/)
})

test('runtime saves omit an absent revision and exclude private component paths', async t => {
  const state = setup(t)
  state.sandbox.runtimeSettingsRevision.value = ''
  state.sandbox.runtimeSettingsDraft.value.voice_training_dir = 'private-local-path'
  state.sandbox.runtimeSettingKeysBySection.diary.push('voice_training_dir')
  state.sandbox.privateRuntimePathKeys.push('voice_training_dir')
  assert.equal(await state.sandbox.saveRuntimeSettings(), true)
  assert.equal(Object.hasOwn(state.sent().headers, 'If-Match'), false)
  assert.deepEqual(state.sent().payload, { daily_diary_check_seconds: 90 })
})

test('runtime saves skip unready and busy drafts without making a request', async t => {
  const state = setup(t)
  state.sandbox.runtimeSettingsReady.value = false
  assert.equal(await state.sandbox.saveRuntimeSettings(), false)
  state.sandbox.runtimeSettingsReady.value = true
  state.sandbox.runtimeSettingsBusy.value = true
  assert.equal(await state.sandbox.saveRuntimeSettings(), false)
  assert.equal(state.sent(), undefined)
})
