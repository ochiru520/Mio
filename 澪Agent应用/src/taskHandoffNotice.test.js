import test from 'node:test'
import assert from 'node:assert/strict'
import { ref } from 'vue'
import { useTaskHandoff } from './composables/useTaskHandoff.js'
import { handoffNoticeTitle } from './taskHandoffNotice.js'

function setup(load) {
  const deps = { selectedConversationId: ref('source-a'), latestTaskHandoff: ref(null), isAgentWorkspace: false, handoffLoadVersion: 0 }
  return { deps, ...useTaskHandoff(deps, load) }
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

test('historical completed, cancelled, failed and unconfirmed tasks do not claim continued execution', () => {
  for (const status of ['completed', 'cancelled', 'failed', undefined, 'unknown']) assert.equal(handoffNoticeTitle({ task_id: 'old', status }), '')
  assert.match(handoffNoticeTitle({ task_id: 'active', status: 'waiting_confirmation' }), /等待确认/)
  assert.match(handoffNoticeTitle({ task_id: 'active', status: 'paused' }), /暂停/)
})

test('refresh observes terminal state and task deletion after initial handoff', async () => {
  let handoff = { task_id: 't', status: 'running' }
  const api = setup(async () => ({ handoff }))
  await api.refreshSourceHandoff()
  assert.ok(handoffNoticeTitle(api.deps.latestTaskHandoff.value))
  handoff = { task_id: 't', status: 'completed' }
  await api.refreshSourceHandoff()
  assert.equal(handoffNoticeTitle(api.deps.latestTaskHandoff.value), '')
  handoff = null
  await api.refreshSourceHandoff()
  assert.equal(api.deps.latestTaskHandoff.value, null)
})

test('late source read cannot overwrite another conversation', async () => {
  const old = deferred()
  const api = setup(id => id === 'source-a' ? old.promise : Promise.resolve({ handoff: { task_id: 'b', status: 'running' } }))
  const pending = api.refreshSourceHandoff()
  await Promise.resolve()
  api.deps.selectedConversationId.value = 'source-b'; api.deps.handoffLoadVersion++
  await api.refreshSourceHandoff()
  old.resolve({ handoff: { task_id: 'a', status: 'running' } }); await pending
  assert.equal(api.deps.latestTaskHandoff.value.task_id, 'b')
})

test('late status read cannot replace a handoff just published by a send or reconnect', async () => {
  for (const bumpVersion of [true, false]) {
    const old = deferred(), api = setup(() => old.promise)
    const pending = api.refreshSourceHandoff()
    if (bumpVersion) api.deps.handoffLoadVersion++
    api.deps.latestTaskHandoff.value = { task_id: 'new', status: 'ready' }
    old.resolve({ handoff: { task_id: 'old', status: 'completed' } }); await pending
    assert.equal(api.deps.latestTaskHandoff.value.task_id, 'new')
  }
})

test('same-generation polling coalesces and recovers after a transient read failure', async () => {
  const read = deferred(); let count = 0
  const api = setup(() => { count++; return read.promise })
  api.deps.latestTaskHandoff.value = { task_id: 'active', status: 'running' }
  const first = api.refreshSourceHandoff(), second = api.refreshSourceHandoff()
  assert.equal(first, second)
  read.resolve({ handoff: { task_id: 'active', status: 'paused' } }); await first
  assert.equal(count, 1)
  const failing = setup(async () => { throw Error('temporary disconnect') })
  failing.deps.latestTaskHandoff.value = { task_id: 'active', status: 'running' }
  await failing.refreshSourceHandoff()
  assert.equal(failing.deps.latestTaskHandoff.value.task_id, 'active')
})

test('disposed and Agent windows do not apply source task reads', async () => {
  const read = deferred(), api = setup(() => read.promise)
  const pending = api.refreshSourceHandoff(); api.disposeHandoff()
  read.resolve({ handoff: { task_id: 'late', status: 'running' } }); await pending
  assert.equal(api.deps.latestTaskHandoff.value, null)
  let count = 0; const agent = setup(async () => { count++; return { handoff: null } })
  agent.deps.isAgentWorkspace = true
  await agent.refreshSourceHandoff()
  assert.equal(count, 0)
})
