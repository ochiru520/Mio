import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { ref } from 'vue'
import { useCompanion } from './composables/useCompanion.js'

function fixture(overrides = {}) {
  const calls = []
  const settings = { screen_watch_mode: 'adaptive', screen_watch_pace: 'normal', screen_audio_enabled: false, screen_vision_route: 'local', private_setting: 'preserve' }
  const deps = {
    companionBusy: ref(''), errorMessage: ref(''), companionStatusError: ref(''), companionStatusReady: ref(true),
    companionStatus: ref({ pet: { settings }, screen: { running: false } }),
    companionSettingKeys: { observation: ['screen_watch_mode', 'screen_watch_pace', 'screen_audio_enabled', 'screen_vision_route'] },
    savedCompanionSettings: ref({}), companionSettingsSnapshot: () => JSON.stringify(settings),
    observationMode: ref('screen'), selectedGameHwnd: ref(''), observationInterval: ref(1000), screenScope: ref('primary'),
    activeSettingsItem: ref({ label: '屏幕观察' }), showSettingsFeedback() {},
    request: async (path, options) => {
      calls.push({ path, body: JSON.parse(options.body || '{}') })
      if (path.endsWith('/settings')) return { pet: { settings } }
      return { running: path.endsWith('/start') }
    },
    ...overrides,
  }
  return { deps, calls, ...useCompanion(deps) }
}

test('saving observation settings neither starts capture nor changes the audio preference', async () => {
  const { deps, calls, saveCompanionSettings } = fixture()
  assert.equal(await saveCompanionSettings('observation'), true)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].path, '/api/companion/settings')
  assert.equal(calls[0].body.screen_watch_mode, 'adaptive')
  assert.equal(calls[0].body.screen_audio_enabled, false)
  assert.equal(Object.hasOwn(calls[0].body, 'private_setting'), false)
  assert.equal(deps.companionBusy.value, '')
})

test('explicit start saves the observation draft before starting, without enabling system audio', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({ pet: { settings: {} }, screen: { running: true } }), { headers: { 'Content-Type': 'application/json' } })
  try {
    const { deps, calls, controlObservation } = fixture()
    await controlObservation('start')
    assert.deepEqual(calls.map(row => row.path), ['/api/companion/settings', '/api/companion/screen/start'])
    assert.deepEqual(calls[1].body, { interval_ms: 1000, scope: 'primary' })
    assert.equal(deps.companionStatus.value.pet.settings.screen_audio_enabled, false)
    assert.equal(deps.companionStatus.value.screen.running, true)
  } finally { globalThis.fetch = originalFetch }
})

test('stop waits for the stop endpoint and does not overwrite saved audio or voice settings', async () => {
  let resolve
  const { deps, calls, controlObservation } = fixture({ request: async (path) => { calls.push(path); return new Promise(done => { resolve = done }) } })
  deps.companionStatus.value.screen.running = true
  deps.companionStatus.value.pet.settings.screen_audio_enabled = true
  const pending = controlObservation('stop')
  assert.equal(deps.companionStatus.value.screen.running, true)
  assert.equal(deps.companionBusy.value, 'observation-stop')
  resolve({ running: false })
  await pending
  assert.deepEqual(calls, ['/api/companion/screen/stop'])
  assert.equal(deps.companionStatus.value.screen.running, false)
  assert.equal(deps.companionStatus.value.pet.settings.screen_audio_enabled, true)
})

test('missing game selection and failed config save cannot begin capture', async () => {
  const { deps, calls, controlObservation } = fixture()
  deps.observationMode.value = 'game'
  await controlObservation('start')
  assert.equal(calls.length, 0)
  assert.match(deps.errorMessage.value, /请选择/)
  deps.observationMode.value = 'screen'
  deps.request = async () => { throw new Error('配置未保存') }
  await controlObservation('start')
  assert.equal(deps.companionStatus.value.screen.running, false)
  assert.equal(deps.errorMessage.value, '配置未保存')
})

function menuFixture(status) {
  const renderer = readFileSync(new URL('../public/live2d-pet/live2d-pet.js', import.meta.url), 'utf8')
  const source = renderer.slice(renderer.indexOf('  async function toggleObservation() {'), renderer.indexOf('  async function setClickThroughLocked('))
  const calls = [], bubbles = []
  const context = { observationBusy: false, observationStarting: false, observationStopping: false, observationOperation: 0, observationState: {}, updateContextMenuLabels() {}, showSpeechBubble: text => bubbles.push(text),
    request: async (path, options) => {
      calls.push({ path, body: options?.body ? JSON.parse(options.body) : null })
      if (path.endsWith('/status')) return status
      return { running: path.endsWith('/start') }
    },
  }
  vm.createContext(context)
  vm.runInContext(source, context)
  return { context, calls, bubbles }
}

test('pet menu selects adaptive mode and starts only the saved window without enabling cloud or audio', async () => {
  const { context, calls, bubbles } = menuFixture({ pet: { settings: { screen_ai_enabled: true, screen_audio_enabled: false, screen_vision_route: 'local' } }, screen: { running: false, mode: 'window', hwnd: 12 } })
  const result = await context.toggleObservation()
  assert.equal(result.running, true)
  assert.deepEqual(calls.map(row => row.path), ['/api/companion/status', '/api/companion/settings', '/api/companion/game/start'])
  assert.equal(JSON.stringify(calls[1].body), '{"screen_watch_mode":"adaptive"}')
  assert.match(bubbles[0], /所选窗口/)
})

test('pet menu stops an active observer without saving or restarting it', async () => {
  const { context, calls } = menuFixture({ screen: { running: true } })
  assert.equal((await context.toggleObservation()).running, false)
  assert.deepEqual(calls.map(row => row.path), ['/api/companion/status', '/api/companion/screen/stop'])
})

test('pet menu respects a disabled AI setting and reports failures without claiming capture started', async () => {
  const { context, calls, bubbles } = menuFixture({ pet: { settings: { screen_ai_enabled: false } }, screen: { running: false } })
  assert.equal((await context.toggleObservation()).ok, false)
  assert.equal(calls.length, 1)
  assert.match(bubbles[0], /开启/)
  assert.equal(context.observationBusy, false)
})

test('stopping during a cold start ignores its late rejection and preserves newer UI activity', async () => {
  let rejectStart, signalStart
  const enteredStart = new Promise(resolve => { signalStart = resolve })
  const { deps, controlObservation, observationStarting } = fixture({ request: async path => {
    if (path.endsWith('/settings')) return {}
    if (path.endsWith('/start')) { signalStart(); return new Promise((resolve,reject) => { rejectStart = reject }) }
    return { running: false }
  } })
  const pending = controlObservation('start')
  await enteredStart
  assert.equal(observationStarting.value,true)
  await controlObservation('stop')
  assert.equal(deps.companionStatus.value.screen.running,false)
  assert.equal(deps.companionBusy.value,'')
  deps.companionBusy.value='voice'
  rejectStart(new Error('409: 启动已取消'))
  await pending
  assert.equal(deps.errorMessage.value,'')
  assert.equal(deps.companionBusy.value,'voice')
  assert.equal(observationStarting.value,false)
})

test('a late successful cold-start response cannot revive a stopped observer', async () => {
  let resolveStart, signalStart
  const enteredStart = new Promise(resolve => { signalStart = resolve })
  const { deps, controlObservation } = fixture({ request: async path => {
    if (path.endsWith('/settings')) return {}
    if (path.endsWith('/start')) { signalStart(); return new Promise(resolve => { resolveStart = resolve }) }
    return { running: false }
  } })
  const pending = controlObservation('start')
  await enteredStart
  await controlObservation('stop')
  resolveStart({ running: true })
  await pending
  assert.equal(deps.companionStatus.value.screen.running,false)
})

test('pet menu can stop cold preparation and ignores the cancelled start error', async () => {
  const { context, bubbles } = menuFixture({pet:{settings:{screen_ai_enabled:true}},screen:{running:false}})
  const request = context.request
  let rejectStart, signalStart
  const enteredStart = new Promise(resolve => { signalStart = resolve })
  context.request = async (path,options) => {
    if(path.endsWith('/start')) { signalStart(); return new Promise((resolve,reject)=>{rejectStart=reject}) }
    return request(path,options)
  }
  const pending = context.toggleObservation()
  await enteredStart
  assert.equal(context.observationStarting,true)
  assert.equal((await context.toggleObservation()).running,false)
  rejectStart(new Error('409: 启动已取消'))
  await pending
  assert.equal(context.observationState.running,false)
  assert.deepEqual(bubbles,['屏幕陪看已停止。'])
  assert.equal(context.observationStarting,false)
  assert.equal(context.observationBusy,false)
})
