import assert from 'node:assert/strict'
import test from 'node:test'
import { apiRequest, classifyApiRequest } from './services/api.js'

test('接口按读取、模型、媒体和备份分类截止时间', () => {
  assert.equal(classifyApiRequest('/api/agent/messages'), 'read')
  assert.equal(classifyApiRequest('/api/agent/chat', 'POST'), 'model')
  assert.equal(classifyApiRequest('/api/companion/voice/audio', 'POST'), 'media')
  assert.equal(classifyApiRequest('/api/backups/import', 'POST'), 'backup')
  assert.equal(classifyApiRequest('/api/settings/runtime', 'PATCH'), 'mutation')
})

test('接口截止会中止 fetch 并返回可区分的超时错误', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
  })
  try {
    await assert.rejects(
      apiRequest('/api/agent/messages', { timeoutMs: 5 }),
      (error) => error.code === 'request_timeout' && error.timeoutMs === 5,
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('调用方取消与超时使用不同错误码', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
  })
  const controller = new AbortController()
  try {
    const pending = apiRequest('/api/agent/chat', { method: 'POST', body: '{}', signal: controller.signal })
    controller.abort('user_cancelled')
    await assert.rejects(pending, (error) => error.code === 'request_cancelled')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('媒体请求可以通过同一接口返回 Blob', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(new Blob(['voice']), { status: 200 })
  try {
    const result = await apiRequest('/api/companion/voice/audio', { responseType: 'blob' })
    assert.equal(result.size, 5)
  } finally {
    globalThis.fetch = originalFetch
  }
})
