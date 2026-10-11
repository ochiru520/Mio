import test from 'node:test'
import assert from 'node:assert/strict'
import { ref } from 'vue'
import { useConversations } from './composables/useConversations.js'

function setup({ status = 'pending', agent = true } = {}) {
  const calls = [], state = { status, requestId: '' }
  const deps = {
    selectedConversationId: ref('desktop_agent_test'), draft: ref('执行任务'), attachments: ref([]),
    sending: ref(false), errorMessage: ref(''), messages: ref([]), conversations: ref([]), loading: ref(false),
    reasoningLevel: ref('low'), selectedModel: ref('synthetic'), contextUsage: ref({}),
    latestTaskHandoff: ref(null), isAgentWorkspace: agent, workspaceMode: agent ? 'agent' : 'main',
    scrollToBottom: async () => {}, refreshContextUsage: async () => {}, modelRequestError: e => e.message,
    request: async (url, options) => {
      calls.push({ url, options })
      if (url.endsWith('/chat')) {
        state.requestId = JSON.parse(options.body).client_request_id
        throw Object.assign(new Error('240秒超时'), { code: 'request_timeout' })
      }
      if (url.includes('/chat/requests/')) return { status: state.status, response: {}, error: { message: '供应商失败' } }
      if (url.includes('/messages?')) return [
        { id: 1, role: 'user', request_id: state.requestId },
        { id: 2, role: 'assistant', request_id: 'unrelated', content: '另一个回答' },
      ]
      throw new Error(`Unexpected ${url}`)
    },
  }
  return { deps, calls, state, chat: useConversations(deps) }
}

async function withFetch(run) {
  const old = globalThis.fetch, mutations = []
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'POST') mutations.push({ url, body: JSON.parse(options.body) })
    return new Response(JSON.stringify(url.endsWith('/cancel') ? { cancelled: 1 } : []))
  }
  try { await run(mutations) } finally { globalThis.fetch = old }
}

test('Agent多轮请求不受单模型240秒截止，已成功请求消除超时报错且不取消', async () => withFetch(async mutations => {
  const { chat, deps, calls } = setup({ status: 'succeeded' })
  await chat.sendMessage()
  assert.equal(calls[0].options.timeoutMs, 0)
  assert.equal(deps.errorMessage.value, '')
  assert.equal(deps.sending.value, false)
  assert.equal(mutations.length, 0)
}))

test('中断的待完成请求保持停止入口，不把无关回答当完成，轮询最终成功', async () => withFetch(async mutations => {
  const { chat, deps, state } = setup()
  await chat.sendMessage()
  assert.equal(deps.sending.value, true)
  assert.ok(deps.activeChatCancelPayload)
  assert.match(deps.errorMessage.value, /核对后台/)
  assert.equal(mutations.length, 0)
  state.status = 'succeeded'
  await chat.refreshMessages()
  assert.equal(deps.sending.value, false)
  assert.equal(deps.errorMessage.value, '')
}))

test('连接中断后主动停止仍发送真实取消，使用原始请求和会话', async () => withFetch(async mutations => {
  const { chat, deps, state } = setup()
  await chat.sendMessage()
  await chat.cancelActiveChat()
  assert.deepEqual(mutations, [{ url: '/api/agent/chat/cancel', body: {
    conversation_id: 'desktop_agent_test', client_request_id: state.requestId,
  } }])
  assert.equal(deps.sending.value, false)
  assert.equal(deps.errorMessage.value, '已停止这次回复')
}))

test('后台失败从持久请求读取，切换会话时不污染当前错误提示', async () => withFetch(async () => {
  const { chat, deps, state } = setup()
  await chat.sendMessage()
  deps.selectedConversationId.value = 'another'
  deps.errorMessage.value = '另一会话的提示'
  state.status = 'failed'
  await chat.refreshMessages()
  assert.equal(deps.sending.value, false)
  assert.equal(deps.errorMessage.value, '另一会话的提示')
}))
