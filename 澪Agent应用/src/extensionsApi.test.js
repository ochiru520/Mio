import test from 'node:test'
import assert from 'node:assert/strict'
import { extensionsApi, extensionDraft, renderablePanel } from './services/extensionsApi.js'

test('extension lifecycle sends optimistic revisions and uses encoded identities', async () => {
  const previous = globalThis.fetch, requests = []
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, json: async () => ({ revision: 4 }) } }
  try {
    await extensionsApi.act('tool/with space', 'enabled', { enabled: true, expected_revision: 3, version: '1.0.0' })
    assert.equal(requests[0].url, '/api/extensions/tool%2Fwith%20space/enabled')
    assert.deepEqual(JSON.parse(requests[0].options.body), { enabled: true, expected_revision: 3, version: '1.0.0' })
    await extensionsApi.act('tool', 'rollback', { expected_revision: 4 })
    assert.equal(requests[1].url, '/api/extensions/tool/rollback')
    assert.equal(JSON.parse(requests[1].options.body).expected_revision, 4)
  } finally { globalThis.fetch = previous }
})

test('new extensions begin without granted permissions and panels expose only declared block types', () => {
  assert.deepEqual(extensionDraft('python').manifest.permissions, [])
  assert.equal(extensionDraft('panel').expected_revision, 0)
  assert.deepEqual(renderablePanel({ title: '状态', blocks: [{ type: 'text', text: '<script>alert(1)</script>' }, { type: 'script', source: 'alert(1)' }, { type: 'iframe', url: 'https://example.invalid' }] }), { title: '状态', blocks: [{ type: 'text', text: '<script>alert(1)</script>' }] })
})
