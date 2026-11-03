import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { computed, nextTick, ref } from 'vue'
import { downloadActions, formatModelBytes, modelStatus } from './modelCenterState.js'

function setup(mocks) {
  const file = fs.readFileSync(new URL('./components/ModelCenter.vue', import.meta.url), 'utf8')
  const script = file.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const context = { computed, nextTick, ref, onMounted() {}, onBeforeUnmount() {}, defineEmits: () => () => {}, setTimeout, clearTimeout, modelsApi: { loadModelCatalog: async () => ({ items: [], storage: [] }) }, depsApi: { loadDependencies: async () => ({ dependencies: [] }) }, ...mocks }
  vm.runInNewContext(script + '\nresult = {prepareInstall, prepareRemoval, confirm, confirmation, error, notice, release, previewLocation, storageId, folder, migrate, refresh, catalog, sectionMessages};', context)
  return context.result
}

test('模型状态区分文件存在、运行、可实测及下载状态，不能把来源验证当成现场成功', () => {
  assert.equal(modelStatus({ installed: true, verification: 'verified_local' }), '文件已安装，待启动验证')
  assert.equal(modelStatus({ running: true }), '服务运行中，可实测')
  assert.equal(modelStatus({ status: 'ready' }), '环境已就绪，可实测')
  assert.equal(modelStatus({ status: 'checking', installed: null }), '安装与运行状态待检查')
  assert.equal(modelStatus({}, { installing: true, progress: { stage: 'paused' } }), '下载已暂停')
  assert.deepEqual(downloadActions({ can_pause: true, can_resume: false, can_cancel: true }), ['pause', 'cancel'])
  assert.equal(formatModelBytes(null), '尚未提供大小')
  assert.equal(formatModelBytes(2 * 1024 ** 3), '2.0 GB')
})

test('模型列表独立显示，不等待旧组件接口，并标注缓存及检查错误', async () => {
  let calls = 0
  const api = setup({ depsApi: { loadDependencies: () => { throw new Error('不得阻塞模型列表') } }, modelsApi: { loadModelCatalog: async () => { calls++; return { items: [{ id: 'fixture' }], sections: { items: { state: 'error', has_value: true, error: '检查失败' } } } } } })
  await api.refresh()
  assert.equal(calls, 1)
  assert.equal(api.catalog.value.items[0].id, 'fixture')
  assert.match(api.sectionMessages.value[0].text, /检查失败.*上次结果/)
  assert.equal(api.error.value, '')
})

test('获取安装先展示计划，用户确认后才调用安装', async () => {
  const installs = []
  const depsApi = { loadDependencies: async () => ({ dependencies: [] }), installDependency: async id => { installs.push(id); return { message: '启动' } } }
  const api = setup({ depsApi })
  await api.prepareInstall({ id: 'asr', label: '识别', dependency_id: 'whisper' })
  assert.equal(installs.length, 0)
  assert.equal(api.confirmation.value.kind, 'install')
  await api.confirm()
  assert.deepEqual(installs, ['whisper'])
  assert.equal(api.confirmation.value, null)
})

test('删除必须带预览令牌，失败保留计划并明确显示错误', async () => {
  const deletes = []
  const api = setup({ depsApi: {
    previewDependencyUninstall: async () => ({ token: 'preview-token', paths: ['owned-model'] }),
    uninstallDependency: async (id, token) => { deletes.push([id, token]); throw new Error('文件被占用，未删除') },
  } })
  await api.prepareRemoval({ id: 'asr', dependency_id: 'whisper' })
  assert.equal(deletes.length, 0)
  await api.confirm()
  assert.deepEqual(deletes, [['whisper', 'preview-token']])
  assert.match(api.error.value, /未删除/)
  assert.equal(api.confirmation.value.token, 'preview-token')
})

test('存储操作保存预览的路径和迁移方式，后续修改输入不会改变已确认计划', async () => {
  const calls = []
  const api = setup({ modelsApi: {
    previewStorage: async (id, path, migrate) => ({ token: 'storage-token', mode: 'copy_and_switch', destination: path }),
    commitStorage: async (...args) => { calls.push(args); return { message: '原文件保留' } },
    loadModelCatalog: async () => ({ items: [], storage: [] }),
  } })
  api.folder.value = 'D:/models'; api.migrate.value = true
  await api.previewLocation()
  api.folder.value = 'E:/different'; api.migrate.value = false
  await api.confirm()
  assert.deepEqual(calls, [['voice', 'D:/models', true, 'storage-token']])
  assert.equal(api.notice.value, '原文件保留')
})
