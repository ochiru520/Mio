import test from 'node:test'
import assert from 'node:assert/strict'
import { useCapabilityChecks } from './composables/useCapabilityChecks.js'

test('an older backend is explicit and cannot submit capability checks', async () => {
  const calls = []
  let oldBackend = true
  const state = useCapabilityChecks({ request: async (url, options) => {
    calls.push([url, options])
    if (oldBackend) throw Object.assign(new Error('Not Found'), { status: 404 })
    return { capabilities: [{ id: 'chat' }], busy: false }
  } })
  await state.refresh()
  assert.equal(state.unsupported.value, true)
  assert.equal(state.error.value, '')
  state.prepare({ id: 'chat' })
  await state.confirm()
  await state.cancel({ id: 'chat', run_id: 'old' })
  assert.equal(state.confirmation.value, null)
  assert.equal(calls.length, 1)
  oldBackend = false
  await state.refresh()
  assert.equal(state.unsupported.value, false)
  state.prepare(state.capabilities.value[0])
  await state.confirm()
  assert.equal(calls[2][0], '/api/capabilities/chat/check')
  state.dispose()
})

test('a temporary capability read failure is not mislabeled as an older backend', async () => {
  const state = useCapabilityChecks({ request: async () => { throw Object.assign(new Error('Unavailable'), { status: 503 }) } })
  await state.refresh()
  assert.equal(state.unsupported.value, false)
  assert.equal(state.error.value, 'Unavailable')
  state.dispose()
})

test('reading and preparing a capability never submits a check', async () => {
  const calls = []
  const state = useCapabilityChecks({ request: async (...args) => {
    calls.push(args); return { capabilities: [{ id: 'chat' }], busy: false }
  } })
  await state.refresh()
  state.prepare(state.capabilities.value[0])
  assert.equal(calls.length, 1)
  await state.confirm()
  assert.equal(calls[1][0], '/api/capabilities/chat/check')
  assert.deepEqual(JSON.parse(calls[1][1].body), { consent: true })
  assert.equal(state.confirmation.value, null)
  state.dispose()
})

test('a late status read cannot replace a newly started check', async () => {
  let resolveRead
  const state = useCapabilityChecks({ request: (url, options) => options
    ? Promise.resolve({ capabilities: [{ id:'chat',status:'running' }], busy:true })
    : new Promise(resolve => { resolveRead = resolve }), schedule: () => 1, unschedule() {} })
  const read = state.refresh()
  state.prepare({ id:'chat' })
  await state.confirm()
  resolveRead({ capabilities:[{ id:'chat',status:'unchecked' }],busy:false })
  await read
  assert.equal(state.capabilities.value[0].status, 'running')
  state.dispose()
})

test('cancel targets the visible run and unmount stops polling', async () => {
  const calls = [], cleared = []
  const state = useCapabilityChecks({ request: async (url, options) => {
    calls.push([url, options]); return { capabilities: [], busy: !options }
  }, schedule: () => 7, unschedule: id => cleared.push(id) })
  await state.refresh()
  await state.cancel({ id:'tts',run_id:'visible-run' })
  assert.deepEqual(JSON.parse(calls[1][1].body), { run_id:'visible-run' })
  state.dispose()
  assert.ok(cleared.includes(7))
})

test('a failed start reconciles server state before an explicit retry', async () => {
  let scheduled
  const state = useCapabilityChecks({ request: async () => { throw Error('timeout') },
    schedule: callback => { scheduled = callback; return 3 }, unschedule() {} })
  state.prepare({ id:'chat' })
  await state.confirm()
  assert.equal(state.error.value, 'timeout')
  assert.equal(scheduled, state.refresh)
  assert.equal(state.sending.value, false)
  state.dispose()
})
