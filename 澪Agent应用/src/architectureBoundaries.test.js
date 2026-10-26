import test from 'node:test'
import assert from 'node:assert/strict'
import { inspectSource, inspectTransportBudget, violations } from '../scripts/check-architecture.mjs'

test('settings writes in UI are rejected even when an old transport budget exists', () => {
  const findings = inspectSource("export function save() { return deps.request('/api/settings/runtime', { method: 'PATCH' }) }", 'composables/useSettings.js')
  assert.equal(violations(findings, { 'composables/useSettings.js': 99 })[0].rule, 'settings-layer')
  assert.deepEqual(inspectSource("import * as settingsApi from '../services/settingsApi.js'; export const save = () => settingsApi.saveRuntimeSettings({})", 'composables/useSettings.js'), [])
})

test('completed transport boundary rejects requests regardless of a legacy budget', () => {
  const findings = inspectSource("<script setup>import { apiRequest as send } from '../services/api.js'; send(endpoint)</script><template><div /></template>", 'components/Test.vue')
  assert.equal(violations(findings)[0].rule, 'transport-layer')
  assert.equal(violations(findings, { 'components/Test.vue': 999 })[0].rule, 'transport-layer')
  assert.equal(violations([...findings, ...findings], { 'components/Test.vue': 999 }).length, 2)
  assert.equal(inspectTransportBudget({ transportLimits: { 'components/Test.vue': 999 } })[0].rule, 'transport-budget')
  assert.deepEqual(inspectTransportBudget({ transportLimits: {} }), [])
})

test('state and other frontend modules cannot move transport outside the checked folders', () => {
  assert.equal(inspectSource("export const load = () => fetch('/api/agent/bootstrap')", 'state/chat.js')[0].rule, 'transport-layer')
  assert.equal(inspectSource("import Page from '../components/ChatPage.vue'", 'state/chat.js')[0].rule, 'view-layer')
  assert.deepEqual(inspectSource("export const load = () => fetch('/api/agent/bootstrap')", 'services/chatApi.js'), [])
})

test('streaming and browser transport constructors are forbidden outside services', () => {
  for (const source of [
    "new EventSource('/api/agent/work/task/stream')",
    "new WebSocket('/api/realtime/voice')",
    "new XMLHttpRequest()",
    "navigator.sendBeacon('/api/telemetry', body)",
    "const Stream = EventSource; new Stream('/api/stream')",
    "const Socket = window.WebSocket; new Socket('/api/socket')",
    "const send = navigator['sendBeacon']; send('/api/telemetry')",
    "const { EventSource: Stream } = window; new Stream('/api/stream')",
  ]) assert.equal(violations(inspectSource(source, 'state/transport.js'))[0].rule, 'transport-layer')
  assert.deepEqual(inspectSource("export const open = () => new EventSource('/api/agent/work/task/stream')", 'services/tasksApi.js'), [])
})

test('domain and API modules cannot depend on Vue components', () => {
  const findings = inspectSource("import Dialog from '../components/Dialog.vue'; export { Dialog }", 'services/demoApi.js')
  assert.equal(findings[0].rule, 'view-layer')
})

test('syntax errors and string literals are distinguished from requests', () => {
  assert.equal(inspectSource('export function broken(', 'broken.js')[0].rule, 'syntax')
  assert.deepEqual(inspectSource("export const avatar = '/api/settings/avatar'; // request('/api/settings/runtime')", 'composables/useAvatar.js'), [])
  assert.deepEqual(inspectSource('export const html = "<script>hi</script>"', 'composables/useMarkup.js'), [])
})

test('local transport aliases and inline template handlers cannot bypass settings', () => {
  assert.equal(inspectSource("const send = fetch; export const save = () => send('/api/settings/runtime')", 'composables/demo.js')[0].rule, 'settings-layer')
  const findings = inspectSource('<template><button @click="fetch(\'/api/settings/runtime\', { method: \'PATCH\' })">保存</button></template>', 'components/Demo.vue')
  assert.equal(findings[0].rule, 'settings-layer')
})

test('settings checks include static endpoint bindings, computed calls and dynamic URLs', () => {
  for (const source of [
    "const endpoint = '/api/settings/runtime'; export const save = () => request(endpoint)",
    "export const save = () => deps['request']('/api/settings/runtime')",
    "export const save = endpoint => request(endpoint)",
  ]) {
    assert.equal(inspectSource(source, 'composables/useSettings.js')[0].rule, 'settings-layer')
  }
})
