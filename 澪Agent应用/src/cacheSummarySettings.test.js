import test from 'node:test'
import assert from 'node:assert/strict'
import { useCacheSummarySettings } from './cacheSummarySettings.js'
import { cacheUsageLabel, coverageLabel } from './cacheUsage.js'

test('summary strategy persists only changed keys and reuses revision conflict protection', async t => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    requests.push({ url, options })
    if (options.method === 'PATCH') {
      assert.deepEqual(JSON.parse(options.body), { agent_summary_strategy: 'same_prefix' })
      assert.equal(options.headers['If-Match'], 'revision-1')
      return Response.json({ revision: 'revision-2', settings: { chat_summary_strategy: 'independent', agent_summary_strategy: 'same_prefix' } })
    }
    return Response.json({ revision: 'revision-1', settings: { chat_summary_strategy: 'independent', agent_summary_strategy: 'independent', unrelated: true } })
  })
  const state = useCacheSummarySettings()
  assert.equal(await state.load(), true)
  state.draft.value.agent_summary_strategy = 'same_prefix'
  assert.equal(await state.save(), true)
  assert.equal(requests.length, 2)
  assert.equal(state.draft.value.agent_summary_strategy, 'same_prefix')
  assert.match(state.feedback.value, /已保存/)
})

test('failed strategy save preserves draft and exposes the error', async t => {
  let fail = false
  t.mock.method(globalThis, 'fetch', async () => {
    if (fail) throw new Error('synthetic disconnected')
    return Response.json({ revision: 'revision', settings: {} })
  })
  const state = useCacheSummarySettings()
  await state.load(); fail = true; state.draft.value.chat_summary_strategy = 'same_prefix'
  assert.equal(await state.save(), false)
  assert.equal(state.draft.value.chat_summary_strategy, 'same_prefix')
  assert.match(state.error.value, /后台暂时没有响应/)
  assert.equal(state.busy.value, false)
})

test('coverage labels do not turn missing reports into zero hits', () => {
  assert.equal(cacheUsageLabel({}), '未知')
  assert.equal(cacheUsageLabel({ cache_hit_ratio: 0 }), '0.0%')
  assert.equal(coverageLabel(.25), '25.0%')
  assert.equal(coverageLabel(null), '未知')
})
