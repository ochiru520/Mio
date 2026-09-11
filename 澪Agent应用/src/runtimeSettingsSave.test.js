import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import vm from 'node:vm'

const source = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const start = source.indexOf('async function saveRuntimeSettings()')
const code = source.slice(start, source.indexOf('async function loadMioProfileSettings', start))

function setup({ conflict = false, restart = false } = {}) {
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
    request: async (_url, options) => {
      sent = { headers: options.headers, payload: JSON.parse(options.body) }
      if (conflict) throw new Error('设置已被其他窗口更新')
      Object.assign(server, sent.payload)
      return { settings: server, revision: 'version-b', application: { restart_required: restart ? ['voice_training_dir'] : [] } }
    },
  }
  vm.createContext(sandbox)
  vm.runInContext(code, sandbox)
  return { sandbox, server, messages, sent: () => sent }
}

test('saving a category leaves externally changed switches intact and updates revision', async () => {
  const state = setup()
  assert.equal(await state.sandbox.saveRuntimeSettings(), true)
  assert.deepEqual(state.sent().payload, { daily_diary_check_seconds: 90 })
  assert.equal(state.sent().headers['If-Match'], 'version-a')
  assert.equal(state.server.qq_proactive_enabled, false)
  assert.equal(state.sandbox.runtimeSettingsRevision.value, 'version-b')
})

test('a revision conflict keeps the draft and does not report success', async () => {
  const state = setup({ conflict: true })
  assert.equal(await state.sandbox.saveRuntimeSettings(), false)
  assert.equal(state.sandbox.runtimeSettingsDraft.value.daily_diary_check_seconds, 90)
  assert.equal(state.sandbox.runtimeSettingsRevision.value, 'version-a')
  assert.equal(state.messages.at(-1).type, 'error')
  assert.equal(state.sandbox.runtimeSettingsBusy.value, false)
})

test('saved component paths show restart required instead of claiming immediate application', async () => {
  const state = setup({ restart: true })
  assert.equal(await state.sandbox.saveRuntimeSettings(), true)
  assert.match(state.messages.at(-1).message, /重启/)
})
