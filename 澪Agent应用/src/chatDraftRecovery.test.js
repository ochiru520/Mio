import test from 'node:test'
import assert from 'node:assert/strict'
import { ref } from 'vue'
import { useConversations } from './composables/useConversations.js'

const rejected = () => Object.assign(new Error('引用已经变化'), { code: 'http_error', status: 400 })
async function fixture(run, { chatResult, messages = () => [], requestStatus } = {}) {
  const previousFetch = globalThis.fetch
  globalThis.fetch = async () => new Response('[]')
  let requestId, sends = 0
  const attachment = { id: 'file', kind: 'text', name: '合成资料.txt', mime_type: 'text/plain', text: '资料正文', data_url: '', preview_url: 'data:text/plain,synthetic' }
  const reference = { kind: 'file', target: 'synthetic.txt', version: 'v1' }
  const deps = {
    selectedConversationId: ref('desktop_agent_a'), draft: ref('  待发送的原始内容  '), attachments: ref([attachment]),
    sending: ref(false), errorMessage: ref(''), messages: ref([]), conversations: ref([]), loading: ref(false),
    reasoningLevel: ref('low'), selectedModel: ref('synthetic'), contextUsage: ref({}), latestTaskHandoff: ref(null),
    isAgentWorkspace: true, workspaceMode: 'agent', scrollToBottom: async () => {}, refreshContextUsage: async () => {}, modelRequestError: error => error.message,
    request: async (url, options) => {
      if (url.endsWith('/chat')) { sends++; requestId = JSON.parse(options.body).client_request_id; if (chatResult) return await chatResult(); throw rejected() }
      if (url.includes('/chat/requests/')) { if (requestStatus) return await requestStatus(); return { status: 'pending' } }
      if (url.includes('/messages?')) return await messages(requestId)
      throw new Error(`Unexpected request ${url}`)
    },
  }
  const chat = useConversations(deps)
  chat.references.value = [reference]
  try { await run({ deps, chat, attachment, reference, sends: () => sends }) }
  finally { globalThis.fetch = previousFetch }
}

test('明确拒绝且未保存消息时恢复原文字、附件正文与引用，不自动重发', async () => fixture(async ({ deps, chat, attachment, reference, sends }) => {
  await chat.sendMessage()
  assert.equal(deps.draft.value, '  待发送的原始内容  ')
  assert.deepEqual(deps.attachments.value, [attachment])
  assert.deepEqual(chat.references.value, [reference])
  assert.equal(deps.messages.value.length, 0)
  assert.equal(chat.failedChatDrafts.value.length, 0)
  assert.equal(sends(), 1)
}))

test('等待时新输入不被失败回执覆盖，切换恢复草稿仍保留新输入', async () => {
  let reject
  const pending = new Promise((_, fail) => { reject = fail })
  await fixture(async ({ deps, chat, attachment, sends }) => {
    const sending = chat.sendMessage()
    deps.draft.value = '等待时输入的新内容'
    deps.attachments.value = [{ kind: 'text', name: '新的.txt', text: '新资料' }]
    reject(rejected()); await sending
    assert.equal(deps.draft.value, '等待时输入的新内容')
    assert.equal(deps.attachments.value[0].text, '新资料')
    assert.equal(chat.failedChatDrafts.value.length, 1)
    chat.restoreChatDraft(chat.failedChatDrafts.value[0].id)
    assert.equal(deps.draft.value, '  待发送的原始内容  ')
    assert.deepEqual(deps.attachments.value, [attachment])
    assert.equal(chat.failedChatDrafts.value[0].draft, '等待时输入的新内容')
    chat.restoreChatDraft(chat.failedChatDrafts.value[0].id)
    assert.equal(deps.draft.value, '等待时输入的新内容')
    assert.equal(chat.failedChatDrafts.value[0].draft, '  待发送的原始内容  ')
    assert.equal(sends(), 1)
  }, { chatResult: () => pending })
})

test('切换会话后失败草稿只归属原会话，不能在别的会话恢复', async () => {
  let reject
  const pending = new Promise((_, fail) => { reject = fail })
  await fixture(async ({ deps, chat }) => {
    const sending = chat.sendMessage()
    deps.selectedConversationId.value = 'desktop_agent_b'; deps.draft.value = 'B会话草稿'
    reject(rejected()); await sending
    const saved = chat.failedChatDrafts.value[0]
    assert.equal(saved.conversationId, 'desktop_agent_a')
    chat.restoreChatDraft(saved.id)
    assert.equal(deps.draft.value, 'B会话草稿')
    deps.selectedConversationId.value = 'desktop_agent_a'; deps.draft.value = ''
    chat.restoreChatDraft(saved.id)
    assert.equal(deps.draft.value, '  待发送的原始内容  ')
  }, { chatResult: () => pending })
})

test('失败前已经持久化的用户消息不再还原成待发送草稿', async () => fixture(async ({ deps, chat, sends }) => {
  await chat.sendMessage()
  assert.equal(deps.draft.value, '')
  assert.equal(chat.failedChatDrafts.value.length, 0)
  assert.equal(deps.messages.value.length, 1)
  assert.equal(sends(), 1)
}, { messages: id => [{ id: 1, request_id: id, role: 'user', content: '已保存内容' }] }))

test('网络结果未知先核对，明确未收到后才恢复且不自动重发', async () => fixture(async ({ deps, chat, sends }) => {
  await chat.sendMessage()
  assert.equal(deps.draft.value, '  待发送的原始内容  ')
  assert.equal(deps.sending.value, false)
  assert.equal(sends(), 1)
}, { chatResult: () => { throw Object.assign(new Error('timeout'), { code: 'request_timeout' }) }, requestStatus: () => { throw Object.assign(new Error('not found'), { status: 404 }) } }))

test('历史核对也失败时保留可手动恢复的副本', async () => fixture(async ({ deps, chat, sends }) => {
  await chat.sendMessage()
  assert.equal(deps.draft.value, '')
  assert.equal(chat.failedChatDrafts.value.length, 1)
  chat.restoreChatDraft(chat.failedChatDrafts.value[0].id)
  assert.equal(deps.draft.value, '  待发送的原始内容  ')
  assert.equal(sends(), 1)
}, { messages: () => { throw new Error('合成读取失败') } }))
