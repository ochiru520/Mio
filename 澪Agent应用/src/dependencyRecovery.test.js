import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { computed, nextTick, reactive, ref, watch } from 'vue'

function setup(name, props, mocks, exports) {
  const file = fs.readFileSync(new URL(`./components/${name}.vue`, import.meta.url), 'utf8')
  const script = file.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const context = { computed, nextTick, reactive, ref, watch, onMounted() {}, onBeforeUnmount() {},
    defineProps: () => props, defineEmits: () => () => {},
    CheckCircle2: null, CircleHelp: null, CircleAlert: null, ...mocks, result: null }
  vm.runInNewContext(script + `\nresult = { ${exports} };`, context)
  return context.result
}

test('retry confirmation replaces the displayed attempt and server refresh wins', async () => {
  const props = reactive({ observations: [{ result: { job: { id: 'a', status: 'unknown' } } }] })
  const calls = []
  const api = setup('JobRecovery', props, { apiRequest: async url => {
    calls.push(url)
    return { job: { id: 'b', status: url.endsWith('/retry') ? 'needs_confirmation' : 'created' } }
  } }, 'jobs, perform')
  await api.perform(api.jobs.value[0], 'retry')
  assert.equal(api.jobs.value[0].id, 'b')
  await api.perform(api.jobs.value[0], 'confirm')
  assert.equal(calls[1], '/api/creation/jobs/b/confirm')
  assert.equal(api.jobs.value.length, 0)
  props.observations = [{ result: { job: { id: 'b', status: 'failed', error: 'real failure' } } }]
  assert.equal(api.jobs.value[0].error, 'real failure')
})

test('uninstall is previewed first, confirmed with its token, and refreshed after success', async () => {
  const calls = []
  const api = setup('DependencyCenter', {}, {
    previewDependencyUninstall: async id => ({ id, token: 'reviewed-token', paths: ['managed/model'] }),
    uninstallDependency: async (id, token) => { calls.push([id, token]); return { message: '已卸载' } },
    loadDependencies: async () => ({ dependencies: [{ id: 'whisper', status: 'missing', can_uninstall: false }] }),
  }, 'prepareUninstall, confirmUninstall, uninstallPlan, dependencies, notice, actionFor')
  await api.prepareUninstall({ id: 'whisper', label: 'Whisper' })
  assert.equal(calls.length, 0)
  assert.equal(api.uninstallPlan.value.token, 'reviewed-token')
  await api.confirmUninstall()
  assert.deepEqual(calls, [['whisper', 'reviewed-token']])
  assert.equal(api.uninstallPlan.value, null)
  assert.equal(api.dependencies.value[0].status, 'missing')
  assert.equal(api.notice.value, '已卸载')
  assert.equal(api.actionFor({ id: 'whisper', status: 'ready' }).kind, 'verify')
  assert.equal(api.actionFor({ id: 'whisper', status: 'ready', installing: true }).kind, 'progress')
})

test('uninstall failure remains visible after refreshing dependency state', async () => {
  const api = setup('DependencyCenter', {}, {
    previewDependencyUninstall: async id => ({ id, token: 'reviewed-token', paths: ['managed/model'] }),
    uninstallDependency: async () => { throw new Error('文件被占用') },
    loadDependencies: async () => ({ dependencies: [] }),
  }, 'prepareUninstall, confirmUninstall, error')
  await api.prepareUninstall({ id: 'whisper' })
  await api.confirmUninstall()
  assert.equal(api.error.value, '文件被占用')
})
