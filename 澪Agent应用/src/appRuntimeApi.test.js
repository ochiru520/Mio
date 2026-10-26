import test from 'node:test'
import assert from 'node:assert/strict'
import * as api from './services/appRuntimeApi.js'

test('root lifecycle API preserves handoff encoding, request options and window booleans', async () => {
  const requests = []
  const transport = (...args) => { requests.push(args); return Promise.resolve({ ok: true }) }
  await api.loadSourceHandoff('desktop_a/b?中文', transport)
  await api.checkAutomaticDiary(transport)
  await api.reportAppReady(transport)
  await api.loadModelCatalog(transport)
  await api.reportPetChatWindow(0, transport)
  await api.reportPetChatWindow('open', transport)
  assert.deepEqual(requests, [
    ['/api/agent/work/handoff/source/desktop_a%2Fb%3F%E4%B8%AD%E6%96%87'],
    ['/api/diary/automatic/check', { method: 'POST', body: '{}' }],
    ['/api/companion/app-ready', { method: 'POST' }],
    ['/api/agent/models/catalog'],
    ['/api/companion/chat-window/state', { method: 'POST', body: '{"open":false}' }],
    ['/api/companion/chat-window/state', { method: 'POST', body: '{"open":true}' }],
  ])
})
