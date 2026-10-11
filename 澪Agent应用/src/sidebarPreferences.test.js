import assert from 'node:assert/strict'
import test from 'node:test'
import { useDesktopNavigation } from './composables/useDesktopNavigation.js'

function state(remember, agent = false) {
  const values = new Map()
  globalThis.localStorage = { setItem: (key, value) => values.set(key, value) }
  const saved = { remember_sidebar_state: remember, left_sidebar_visible: true, right_sidebar_visible: true }
  const deps = {
    isAgentWorkspace: agent,
    savedAppPreferences: { value: saved }, appPreferencesDraft: { value: { ...saved } },
    leftSidebarVisible: { value: true }, rightSidebarVisible: { value: true },
    leftSidebarPinned: { value: false }, rightSidebarPinned: { value: false },
  }
  return { deps, values, actions: useDesktopNavigation(deps) }
}

test('关闭记住侧栏后手动显隐和固定仅作用于当前会话', () => {
  const { deps, values, actions } = state(false)
  actions.toggleLeftSidebar(); actions.toggleRightSidebar()
  actions.toggleLeftSidebarPinned(); actions.toggleRightSidebarPinned()
  assert.equal(deps.leftSidebarVisible.value, false)
  assert.equal(deps.rightSidebarVisible.value, false)
  assert.equal(deps.leftSidebarPinned.value, true)
  assert.equal(deps.rightSidebarPinned.value, true)
  assert.equal(deps.savedAppPreferences.value.left_sidebar_visible, true)
  assert.equal(deps.savedAppPreferences.value.right_sidebar_visible, true)
  assert.equal(deps.appPreferencesDraft.value.left_sidebar_visible, true)
  assert.equal(values.size, 0)
})

test('开启记住侧栏后显隐和固定仍持久化', () => {
  const { deps, values, actions } = state(true)
  actions.toggleLeftSidebar(); actions.toggleRightSidebar()
  actions.toggleLeftSidebarPinned(); actions.toggleRightSidebarPinned()
  assert.equal(deps.savedAppPreferences.value.left_sidebar_visible, false)
  assert.equal(deps.savedAppPreferences.value.right_sidebar_visible, false)
  assert.equal(values.get('mio_left_sidebar_visible'), 'false')
  assert.equal(values.get('mio_right_sidebar_visible'), 'false')
  assert.equal(values.get('mio_left_sidebar_pinned'), 'true')
  assert.equal(values.get('mio_right_sidebar_pinned'), 'true')
})

test('聊天的记住侧栏开关不控制Agent独立右栏', () => {
  const { deps, values, actions } = state(false, true)
  actions.toggleRightSidebar(); actions.toggleRightSidebarPinned()
  assert.equal(values.get('mio_agent_right_sidebar_visible'), 'false')
  assert.equal(values.get('mio_agent_right_sidebar_pinned'), 'true')
  assert.equal(values.has('mio_app_preferences'), false)
  assert.equal(deps.savedAppPreferences.value.right_sidebar_visible, true)
})
