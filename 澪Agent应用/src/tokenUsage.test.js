import test from 'node:test'
import assert from 'node:assert/strict'
import { useBootstrap } from './composables/useBootstrap.js'

function fixture(request) {
  const deps = { request, showTokenUsagePanel: { value: false }, tokenUsageLoading: { value: false },
    tokenUsageError: { value: '' }, tokenUsageData: { value: { total: { total_tokens: 123 } } } }
  return { deps, open: useBootstrap(deps).openTokenUsagePanel }
}

test('reopening usage ignores the earlier response while the current request remains pending', async () => {
  const requests = []
  const { deps, open } = fixture(() => new Promise((resolve, reject) => requests.push({ resolve, reject })))
  const first = open(), second = open()
  requests[0].resolve({ total: { total_tokens: 999 } })
  await first
  assert.equal(deps.tokenUsageData.value.total.total_tokens, 123)
  assert.equal(deps.tokenUsageLoading.value, true)
  requests[1].resolve({ total: { total_tokens: 234 } })
  await second
  assert.equal(deps.tokenUsageData.value.total.total_tokens, 234)
  assert.equal(deps.tokenUsageLoading.value, false)
})

test('usage failure preserves data behind an explicit error and retry clears the error', async () => {
  let fail = true
  const { deps, open } = fixture(async () => {
    if (fail) throw new Error('disconnected')
    return { total: { total_tokens: 0 } }
  })
  await open()
  assert.equal(deps.tokenUsageError.value, 'disconnected')
  assert.equal(deps.tokenUsageLoading.value, false)
  assert.equal(deps.tokenUsageData.value.total.total_tokens, 123)
  fail = false
  await open()
  assert.equal(deps.tokenUsageError.value, '')
  assert.equal(deps.tokenUsageData.value.total.total_tokens, 0)
})
