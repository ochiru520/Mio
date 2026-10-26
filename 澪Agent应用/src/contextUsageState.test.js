import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { DEFAULT_CONTEXT_MAX_TOKENS, contextUsagePresentation } from './contextUsageState.js'
import { useConversations } from './composables/useConversations.js'
import { useSettings } from './composables/useSettings.js'

test('the default context budget is 272k tokens without using legacy character values', () => {
  assert.equal(DEFAULT_CONTEXT_MAX_TOKENS, 272000)
  for (const usage of [undefined, null, {}, { used_chars: 9000, max_chars: 18000, percent: 50 }]) {
    const shown = contextUsagePresentation(usage)
    assert.equal(shown.amount, '0 / 272,000 token（估算）')
    assert.equal(shown.percent, 0)
    assert.match(shown.label, /^上下文预算：/)
    assert.doesNotMatch(shown.label, /字|窗口/)
  }
})

test('the displayed ratio uses current token usage and preserves an explicit custom budget', () => {
  const shown = contextUsagePresentation({ used_tokens: 136000, max_tokens: 272000,
    percent: 99, token_percent: 12, used_chars: 800, max_chars: 1000, total_tokens: 999999 })
  assert.equal(shown.percent, 50)
  assert.equal(shown.amount, '136,000 / 272,000 token（估算）')
  assert.doesNotMatch(shown.label, /999,999/)
  assert.equal(contextUsagePresentation({ used_tokens: 1500, max_tokens: 1000 }).percent, 150)
  assert.equal(contextUsagePresentation({ used_tokens: 10000, max_tokens: 100000 }).limit, 100000)
  assert.equal(contextUsagePresentation({ used_tokens: 300, max_tokens: 1000, estimated: false }).amount, '300 / 1,000 token')
})

test('invalid token counts cannot display NaN or inherit a character budget', () => {
  for (const invalid of [0, -1, Infinity, 'invalid']) {
    const shown = contextUsagePresentation({ used_tokens: invalid, max_tokens: invalid, max_chars: 18000 })
    assert.equal(shown.used, 0)
    assert.equal(shown.limit, 272000)
    assert.equal(shown.percent, 0)
    assert.doesNotMatch(shown.label, /NaN|Infinity|18,000/)
  }
})

test('the runtime settings API loads and saves the token budget without a character field', async t => {
  const server = { chat_context_max_tokens: 272000, memory_context_max_chars: 5000 }
  const changes = []
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    assert.equal(url, '/api/settings/runtime')
    if (options.method === 'PATCH') {
      const payload = JSON.parse(options.body)
      changes.push(payload)
      Object.assign(server, payload)
    }
    return Response.json({ settings: server, revision: 'token-budget' })
  })
  const state = {
    runtimeSettingsBusy: ref(false), runtimeSettingsReady: ref(false),
    runtimeSettingsDraft: ref({}), runtimeSettingsRevision: ref(''), savedRuntimeSettings: ref('{}'),
    savedRuntimeSettingsSource: () => JSON.parse(state.savedRuntimeSettings.value),
    serializeSettings: JSON.stringify, runtimeSettingKeysBySection: { conversation: ['chat_context_max_tokens'] },
    privateRuntimePathKeys: [], activeSettingsSection: ref('conversation'), activeSettingsItem: ref({ label: '对话与记忆' }),
    errorMessage: ref(''), loadBootstrap: async () => {}, showSettingsFeedback() {},
  }
  const api = useSettings(state)
  assert.equal(await api.loadRuntimeSettings(), true)
  assert.equal(state.runtimeSettingsDraft.value.chat_context_max_tokens, 272000)
  state.runtimeSettingsDraft.value.chat_context_max_tokens = 300000
  assert.equal(await api.saveRuntimeSettings(), true)
  assert.deepEqual(changes, [{ chat_context_max_tokens: 300000 }])
  assert.equal(state.runtimeSettingsDraft.value.chat_context_max_tokens, 300000)
  assert.equal(server.memory_context_max_chars, 5000)
})

test('new conversations clear current token usage while preserving the token budget', async t => {
  const descriptors = Object.fromEntries(['document', 'localStorage'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { querySelector: () => null } })
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { setItem() {} } })
  t.after(() => { for (const [key, descriptor] of Object.entries(descriptors)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor)
    else delete globalThis[key]
  } })
  t.mock.method(globalThis, 'fetch', async () => Response.json({ id: 'synthetic-token-budget' }))
  for (const usage of [{ used_tokens: 300, max_tokens: 300000 }, { used_chars: 9000, max_chars: 18000 }]) {
    const state = { activeView: ref('chat'), errorMessage: ref(''), selectedConversationId: ref('old'),
      conversations: ref([]), messages: ref([{ id: 1 }]), contextUsage: ref(usage), conversationStorageKey: 'fixture', isAgentWorkspace: false }
    await useConversations(state).createNewConversation()
    assert.equal(state.errorMessage.value, '')
    assert.equal(state.contextUsage.value.used_tokens, 0)
    assert.equal(state.contextUsage.value.max_tokens, usage.max_tokens || 272000)
    assert.equal(state.contextUsage.value.has_summary, false)
    assert.equal('used_chars' in state.contextUsage.value, false)
    assert.deepEqual(state.messages.value, [])
  }
})


test('context details separate text estimates, image unknowns and actual route reserve', () => {
  const shown = contextUsagePresentation({ used_tokens: 1000, max_tokens: 28000, configured_max_tokens: 272000, model_window_tokens: 32000, output_reserved_tokens: 4000, before_compression_tokens: 2000, breakdown: { blocks: [
    {category: 'history', estimated_tokens: 700}, {category: 'tool_schema', estimated_tokens: 300}], unestimated_image_count: 2 } })
  assert.equal(shown.breakdown.length, 2)
  assert.match(shown.title, /历史消息：700/)
  assert.match(shown.title, /2 张图片，用量未知/)
  assert.match(shown.title, /2,000 → 1,000/)
  assert.match(shown.title, /模型窗口 32,000.*预留输出 4,000.*272,000/)
})
