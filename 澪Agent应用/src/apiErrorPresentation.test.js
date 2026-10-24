import assert from 'node:assert/strict'
import test from 'node:test'
import { apiRequest } from './services/api.js'

async function withError(body, check) {
  const original = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify(body), { status: 422, headers: { 'Content-Type': 'application/json' } })
  try { await assert.rejects(apiRequest('/api/self-upgrade/configuration', { timeoutMs: 0 }), check) }
  finally { globalThis.fetch = original }
}

test('validation arrays show field errors instead of object Object and retain structured detail', async () => {
  const detail = [
    { loc: ['body', 'backend_root'], msg: 'String should have at least 1 character', type: 'string_too_short', input: '' },
    { loc: ['body', 'desktop_root'], msg: 'Field required', type: 'missing' },
  ]
  await withError({ detail }, error => {
    assert.equal(error.message, '后端源码目录：填写的内容太短；桌面应用源码目录：请填写此项')
    assert.equal(error.code, 'http_error')
    assert.equal(error.status, 422)
    assert.deepEqual(error.detail, detail)
    return true
  })
})

test('structured conflict messages and machine-readable fields remain available', async () => {
  await withError({ detail: { message: '设置已变化，请重新读取', code: 'revision_conflict', revision: 'abc' } }, error => {
    assert.equal(error.message, '设置已变化，请重新读取')
    assert.equal(error.detail.code, 'revision_conflict')
    return true
  })
})

test('unrecognized response details use HTTP fallback without exposing raw object inputs', async () => {
  await withError({ detail: { input: { api_key: 'fixture-credential' } } }, error => {
    assert.equal(error.message, '请求失败：HTTP 422')
    assert.ok(!error.message.includes('fixture-credential'))
    return true
  })
})

test('nested server error text is rendered and plain string errors keep their message', async () => {
  for (const detail of ['需要完整后端源码', { error: { message: '本地服务尚未启动' } }]) {
    await withError({ detail }, error => {
      assert.equal(error.message, typeof detail === 'string' ? detail : '本地服务尚未启动')
      return true
    })
  }
})
