import test from 'node:test'
import assert from 'node:assert/strict'
import { useDesktopNavigation } from './composables/useDesktopNavigation.js'

function navigation(switcher, timeoutMs = 1800) {
  const location = { href: 'http://127.0.0.1:1420/?workspace=agent&conversation_id=agent-thread&view=projects&settings-section=budget' }
  const timers = new Map()
  let nextTimer = 0
  const stored = new Map()
  globalThis.localStorage = { setItem(key, value) { stored.set(key, String(value)) }, getItem(key) { return stored.get(key) ?? null } }
  globalThis.window = { location, setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id }, clearTimeout(id) { timers.delete(id) }, pywebview: switcher === undefined ? undefined : { api: { switch_workspace: switcher } } }
  const api = useDesktopNavigation({ errorMessage: { value: '' }, workspaceSwitchTimeoutMs: timeoutMs })
  return { api, location, stored, fireTimers: () => { for (const callback of timers.values()) callback() } }
}

test('returning to Mio falls back to the main chat when the desktop bridge rejects', async () => {
  const { api, location, stored } = navigation(async () => { throw new Error('bridge unavailable') })
  await api.switchDesktopWorkspace('main')
  const target = new URL(location.href)
  assert.equal(target.searchParams.has('workspace'), false)
  assert.equal(target.searchParams.has('conversation_id'), false)
  assert.equal(target.searchParams.has('view'), false)
  assert.equal(target.searchParams.has('settings-section'), false)
  assert.equal(target.searchParams.get('mio_return'), 'chat')
  assert.ok(stored.get('mio_return_to_chat'))
})

test('returning to Mio falls back when the desktop bridge reports a failed switch', async () => {
  const { api, location } = navigation(async () => ({ ok: false, error: 'main window unavailable' }))
  await api.switchDesktopWorkspace('main')
  assert.equal(new URL(location.href).searchParams.get('mio_return'), 'chat')
})

test('returning to Mio falls back when the desktop bridge never responds', async () => {
  const { api, location, fireTimers } = navigation(() => new Promise(() => {}))
  const switching = api.switchDesktopWorkspace('main')
  await new Promise(resolve => setImmediate(resolve))
  fireTimers()
  await switching
  const target = new URL(location.href)
  assert.equal(target.searchParams.has('workspace'), false)
  assert.equal(target.searchParams.get('mio_return'), 'chat')
})

test('successful desktop switches keep the existing main window and URL unchanged', async () => {
  let target = ''
  const original = 'http://127.0.0.1:1420/?workspace=agent&conversation_id=agent-thread'
  const { api, location } = navigation(async value => { target = value; return { ok: true, workspace: value } })
  location.href = original
  await api.switchDesktopWorkspace('main')
  assert.equal(target, 'main')
  assert.equal(location.href, original)
})

test('returning from Agent displayed in the physical Mio window navigates to chat', async () => {
  const { api, location } = navigation(async target => ({ ok: true, workspace: target, navigation_required: true }))
  await api.switchDesktopWorkspace('main')
  const next = new URL(location.href)
  assert.equal(next.searchParams.has('workspace'), false)
  assert.equal(next.searchParams.has('conversation_id'), false)
  assert.equal(next.searchParams.get('mio_return'), 'chat')
})

test('returning to Agent in its physical window reopens its own conversation selection', async () => {
  const { api, location } = navigation(async target => ({ ok: true, workspace: target, navigation_required: true }))
  location.href = 'http://127.0.0.1:1420/?conversation_id=desktop_pet&mio_return=chat'
  await api.switchDesktopWorkspace('agent')
  const next = new URL(location.href)
  assert.equal(next.searchParams.get('workspace'), 'agent')
  assert.equal(next.searchParams.has('conversation_id'), false)
})

test('missing or mismatched host acknowledgements cannot silently strand the user', async () => {
  for (const result of [undefined, {}, { ok: true }, { ok: true, workspace: 'agent' }]) {
    const { api, location } = navigation(async () => result)
    await api.switchDesktopWorkspace('main')
    assert.equal(new URL(location.href).searchParams.get('mio_return'), 'chat')
  }
})
