import assert from 'node:assert/strict'
import test from 'node:test'
import { useSettings } from './composables/useSettings.js'

function setup(t, { remote = {}, repeatConflict = false, readError = false } = {}) {
  const baseline = { daily_diary_check_seconds: 60, qq_proactive_enabled: false }
  const server = { ...baseline, ...remote }
  const calls = []
  const feedback = []
  let version = 'b'
  const state = {
    runtimeSettingsBusy: { value: false }, runtimeSettingsReady: { value: true },
    runtimeSettingsRevision: { value: 'a' }, runtimeSettingsDraft: { value: { ...baseline, daily_diary_check_seconds: 90 } },
    savedRuntimeSettings: { value: JSON.stringify(baseline) },
    savedRuntimeSettingsSource: () => JSON.parse(state.savedRuntimeSettings.value),
    runtimeSettingKeysBySection: { diary: ['daily_diary_check_seconds'] }, privateRuntimePathKeys: [],
    activeSettingsSection: { value: 'diary' }, activeSettingsItem: { value: { label: '日记' } },
    activeView: { value: 'settings' }, errorMessage: { value: '' },
    serializeSettings: JSON.stringify, loadBootstrap: async () => {},
    showAppConfirm: async () => false,
    showSettingsFeedback: (section, type, message) => feedback.push({ section, type, message }),
  }
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/settings/runtime')
    const method = options.method || 'GET'
    calls.push({ method, headers: options.headers, body: options.body && JSON.parse(options.body) })
    if (method === 'GET') return readError
      ? Response.json({ detail: '读取失败' }, { status: 503 })
      : Response.json({ settings: server, revision: version })
    if (options.headers['If-Match'] !== version || repeatConflict) return Response.json({ detail: { code: 'settings_conflict', message: '版本冲突' } }, { status: 409 })
    Object.assign(server, JSON.parse(options.body))
    version = 'c'
    return Response.json({ settings: server, revision: version })
  })
  return { state, actions: useSettings(state), calls, server, feedback }
}

test('unrelated background changes rebase once without losing either change', async t => {
  const { state, actions, calls, server } = setup(t, { remote: { qq_proactive_enabled: true } })
  assert.equal(await actions.saveRuntimeSettings(), true)
  assert.deepEqual(calls.map(c => c.method), ['PATCH', 'GET', 'PATCH'])
  assert.equal(calls[2].headers['If-Match'], 'b')
  assert.deepEqual(calls[2].body, { daily_diary_check_seconds: 90 })
  assert.deepEqual(state.runtimeSettingsDraft.value, server)
  assert.deepEqual(server, { daily_diary_check_seconds: 90, qq_proactive_enabled: true })
})

test('a different value for the same field cannot be overwritten automatically', async t => {
  const { state, actions, calls, server } = setup(t, { remote: { daily_diary_check_seconds: 120 } })
  assert.equal(await actions.saveRuntimeSettings(), false)
  assert.deepEqual(calls.map(c => c.method), ['PATCH', 'GET'])
  assert.equal(server.daily_diary_check_seconds, 120)
  assert.equal(state.runtimeSettingsDraft.value.daily_diary_check_seconds, 90)
  assert.equal(state.runtimeSettingsRevision.value, 'a')
  assert.match(state.errorMessage.value, /同一项设置/)
})

test('the same intended remote value is safe to save under its new revision', async t => {
  const { actions, calls } = setup(t, { remote: { daily_diary_check_seconds: 90 } })
  assert.equal(await actions.saveRuntimeSettings(), true)
  assert.equal(calls.length, 3)
})

test('a second conflict stops with the draft intact rather than retrying forever', async t => {
  const { state, actions, calls } = setup(t, { repeatConflict: true })
  assert.equal(await actions.saveRuntimeSettings(), false)
  assert.equal(calls.length, 3)
  assert.match(state.errorMessage.value, /仍在被其他窗口更新/)
  assert.equal(state.runtimeSettingsDraft.value.daily_diary_check_seconds, 90)
  assert.equal(state.runtimeSettingsBusy.value, false)
})

test('a failed conflict refresh retains the saved baseline and draft', async t => {
  const { state, actions, calls } = setup(t, { readError: true })
  const saved = state.savedRuntimeSettings.value
  assert.equal(await actions.saveRuntimeSettings(), false)
  assert.equal(calls.length, 2)
  assert.equal(state.savedRuntimeSettings.value, saved)
  assert.equal(state.runtimeSettingsDraft.value.daily_diary_check_seconds, 90)
  assert.match(state.errorMessage.value, /读取失败/)
})

test('a save with no category changes does not send a stale empty PATCH', async t => {
  const { state, actions, calls } = setup(t)
  state.runtimeSettingsDraft.value.daily_diary_check_seconds = 60
  assert.equal(await actions.saveRuntimeSettings(), true)
  assert.equal(calls.length, 0)
  assert.equal(state.runtimeSettingsBusy.value, false)
})

test('saved responses preserve edits made during the request and other category drafts', async t => {
  const { state, actions } = setup(t)
  state.runtimeSettingsRevision.value = 'b'
  state.runtimeSettingsDraft.value.qq_proactive_enabled = true
  const pending = actions.saveRuntimeSettings()
  state.runtimeSettingsDraft.value.daily_diary_check_seconds = 100
  state.activeSettingsSection.value = 'qq'
  state.activeSettingsItem.value = { label: 'QQ' }
  assert.equal(await pending, true)
  assert.equal(state.runtimeSettingsDraft.value.daily_diary_check_seconds, 100)
  assert.equal(state.runtimeSettingsDraft.value.qq_proactive_enabled, true)
  assert.deepEqual(JSON.parse(state.savedRuntimeSettings.value), { daily_diary_check_seconds: 90, qq_proactive_enabled: false })
})

test('reselecting a clean category reads the latest settings', async t => {
  const { state, actions, calls } = setup(t, { remote: { daily_diary_check_seconds: 120 } })
  state.runtimeSettingsDraft.value.daily_diary_check_seconds = 60
  await actions.openSettingsSection('diary')
  assert.deepEqual(calls.map(c => c.method), ['GET'])
  assert.equal(state.runtimeSettingsDraft.value.daily_diary_check_seconds, 120)
  assert.equal(state.runtimeSettingsRevision.value, 'b')
})

test('reverting a field during its save remains a draft instead of being erased', async t => {
  const { state, actions } = setup(t)
  state.runtimeSettingsRevision.value = 'b'
  const pending = actions.saveRuntimeSettings()
  state.runtimeSettingsDraft.value.daily_diary_check_seconds = 60
  assert.equal(await pending, true)
  assert.equal(state.runtimeSettingsDraft.value.daily_diary_check_seconds, 60)
  assert.equal(JSON.parse(state.savedRuntimeSettings.value).daily_diary_check_seconds, 90)
})

test('background reload skips an existing draft and protects edits during an outstanding read', async t => {
  const { state, actions, calls } = setup(t)
  assert.equal(await actions.loadRuntimeSettings(), false)
  assert.equal(calls.length, 0)
  state.runtimeSettingsDraft.value.daily_diary_check_seconds = 60
  const pending = actions.loadRuntimeSettings()
  state.runtimeSettingsDraft.value.daily_diary_check_seconds = 100
  assert.equal(await pending, false)
  assert.equal(state.runtimeSettingsDraft.value.daily_diary_check_seconds, 100)
  assert.equal(state.runtimeSettingsRevision.value, 'a')
})

test('explicit reload asks before discarding, and a refusal preserves the draft', async t => {
  const { state, actions, calls } = setup(t, { remote: { daily_diary_check_seconds: 120 } })
  assert.equal(await actions.reloadRuntimeSettings(), false)
  assert.equal(calls.length, 0)
  assert.equal(state.runtimeSettingsDraft.value.daily_diary_check_seconds, 90)
  state.showAppConfirm = async () => true
  assert.equal(await actions.reloadRuntimeSettings(), true)
  assert.equal(state.runtimeSettingsDraft.value.daily_diary_check_seconds, 120)
  assert.equal(state.runtimeSettingsRevision.value, 'b')
})
