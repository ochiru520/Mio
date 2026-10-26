import test from 'node:test'
import assert from 'node:assert/strict'
import { ref } from 'vue'
import { petStartupPresentation } from './petStartupState.js'
import { useCompanion } from './composables/useCompanion.js'

test('a process or an old backend running flag never claims a visible pet', () => {
  assert.equal(petStartupPresentation({ running: true }).label, '待确认显示')
  assert.equal(petStartupPresentation({ running: true, startup: { state: 'failed', message: '模型缺失' } }).ready, false)
  assert.equal(petStartupPresentation({ startup: { state: 'ready' } }).ready, true)
  assert.equal(petStartupPresentation({ startup: { state: 'ready' } }, 'restart').label, '启动中')
})

function fixture(request) {
  const deps = {
    companionBusy: ref(''), errorMessage: ref(''), companionStatusError: ref(''), companionStatusReady: ref(true),
    companionStatus: ref({ pet: { running: false, settings: { live2d_disable_gpu: true } } }),
    companionStatusLoading: false, companionAvatarNonce: ref(0), request,
  }
  return { deps, ...useCompanion(deps) }
}

test('startup remains busy until the backend confirms readiness', async () => {
  let resolve
  const { deps, controlCompanion } = fixture(() => new Promise(done => { resolve = done }))
  const pending = controlCompanion('start')
  assert.equal(deps.companionBusy.value, 'start')
  assert.equal(petStartupPresentation(deps.companionStatus.value.pet, deps.companionBusy.value).label, '启动中')
  resolve({ pet: { running: true, startup: { state: 'ready' } } })
  await pending
  assert.equal(deps.companionBusy.value, '')
  assert.equal(petStartupPresentation(deps.companionStatus.value.pet).label, '运行中')
})

test('failed startup reloads diagnostic status, preserves the draft and displays the original error', async () => {
  const savedFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({ pet: { running: true,
    startup: { state: 'failed', message: 'WebGL初始化失败' }, settings: { live2d_disable_gpu: false } } }),
    { headers: { 'Content-Type': 'application/json' } })
  try {
    const { deps, controlCompanion } = fixture(async () => { throw new Error('桌宠启动失败：WebGL初始化失败') })
    await controlCompanion('start')
    assert.equal(deps.companionStatus.value.pet.startup.state, 'failed')
    assert.equal(deps.companionStatus.value.pet.settings.live2d_disable_gpu, true)
    assert.equal(deps.errorMessage.value, '桌宠启动失败：WebGL初始化失败')
    assert.equal(deps.companionBusy.value, '')
  } finally { globalThis.fetch = savedFetch }
})
