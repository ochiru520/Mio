import test from 'node:test'
import assert from 'node:assert/strict'
import { useDesktopNavigation } from './composables/useDesktopNavigation.js'

function createHarness({ url = 'http://localhost/?workspace=agent&conversation_id=agent-1', switcher } = {}) {
  const values = new Map()
  const assigned = []
  const windowMock = {
    location: {
      href: url,
      get search() { return new URL(this.href).search },
      get pathname() { return new URL(this.href).pathname },
      get hash() { return new URL(this.href).hash },
    },
    pywebview: switcher ? { api: { switch_workspace: switcher } } : undefined,
    setTimeout,
    clearTimeout,
    history: { replaceState() {} },
  }
  Object.defineProperty(windowMock.location, 'href', {
    get() { return this._href ?? url },
    set(value) { this._href = value; assigned.push(value) },
    configurable: true,
  })
  const previousWindow = globalThis.window
  const previousStorage = globalThis.localStorage
  globalThis.window = windowMock
  globalThis.localStorage = {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  }
  const deps = {
    errorMessage: { value: '' },
    workspaceSwitchTimeoutMs: 10,
  }
  return {
    navigation: useDesktopNavigation(deps),
    assigned,
    values,
    cleanup() {
      if (previousWindow === undefined) delete globalThis.window
      else globalThis.window = previousWindow
      if (previousStorage === undefined) delete globalThis.localStorage
      else globalThis.localStorage = previousStorage
    },
  }
}

test('returning from Agent requests the main chat before focusing the main window', async () => {
  let calledWith
  const harness = createHarness({ switcher: async target => { calledWith = target; return { ok: true } } })
  try {
    await harness.navigation.switchDesktopWorkspace('main')
    assert.equal(calledWith, 'main')
    assert.ok(harness.values.get('mio_return_to_chat'))
    assert.equal(harness.assigned.length, 0)
  } finally { harness.cleanup() }
})

test('bridge failure falls back to the Mio chat route and drops Agent conversation identity', async () => {
  const harness = createHarness({ switcher: async () => ({ ok: false, error: 'unavailable' }) })
  try {
    await harness.navigation.switchDesktopWorkspace('main')
    const next = new URL(harness.assigned[0])
    assert.equal(next.searchParams.get('mio_return'), 'chat')
    assert.equal(next.searchParams.has('workspace'), false)
    assert.equal(next.searchParams.has('conversation_id'), false)
    assert.ok(harness.values.get('mio_return_to_chat'))
  } finally { harness.cleanup() }
})

test('hung workspace bridge falls back after its timeout', async () => {
  const harness = createHarness({ switcher: () => new Promise(() => {}) })
  try {
    await harness.navigation.switchDesktopWorkspace('main')
    assert.equal(harness.assigned.length, 1)
    assert.equal(new URL(harness.assigned[0]).searchParams.get('mio_return'), 'chat')
  } finally { harness.cleanup() }
})
