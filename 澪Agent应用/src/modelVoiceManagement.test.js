import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { computed, nextTick, ref } from 'vue'
import { formatModelBytes } from './modelCenterState.js'

function vision(mocks = {}) {
  const file = fs.readFileSync(new URL('./components/LocalVisionModelsPanel.vue', import.meta.url), 'utf8')
  const script = file.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const emitted = [], timers = []
  const context = { computed, nextTick, ref, formatModelBytes, onMounted() {}, onBeforeUnmount() {}, defineEmits: () => (...args) => emitted.push(args), setTimeout(fn) { timers.push(fn); return timers.length }, clearTimeout() {}, modelsApi: { loadVisionModels: async () => ({ models: [], download: {} }), ...mocks } }
  vm.runInNewContext(script + '\nresult = { snapshot, error, notice, plan, remaining, downloading, statusLabel, refresh, prepare, confirm, verify, use, cancelDownload };', context)
  return { ...context.result, emitted, timers }
}

function voices(mocks = {}) {
  const file = fs.readFileSync(new URL('./components/VoiceStoragePanel.vue', import.meta.url), 'utf8')
  const script = file.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const emitted = []
  const context = { nextTick, ref, formatModelBytes, onMounted() {}, onBeforeUnmount() {}, defineEmits: () => (...args) => emitted.push(args), loadManagedVoices: async () => ({ voices: [] }), ...mocks }
  vm.runInNewContext(script + '\nresult = { voices, selected, plan, error, notice, busy, refresh, useVoice, select, preview, confirm };', context)
  return { ...context.result, emitted }
}

test('升级检查仅加载清单，不下载、不验证、不切换；文件齐全不能冒充推理验证成功', async () => {
  const panel = vision({ loadVisionModels: async () => ({ selected_model_id: 'old', models: [{ id: 'old', installed: true, selected: true }], download: {} }), downloadVisionModel() { throw new Error('禁止自动下载') }, selectVisionModel() { throw new Error('禁止自动切换') } })
  await panel.refresh()
  assert.equal(panel.snapshot.value.selected_model_id, 'old')
  assert.equal(panel.statusLabel(panel.snapshot.value.models[0]), '文件已安装，待加载验证')
})

test('下载先确认并提交准确模型ID，完成后不自动切换', async () => {
  const calls = []
  const panel = vision({ downloadVisionModel: async id => { calls.push(id); return { models: [], download: { active: true, model_id: id } } } })
  await panel.prepare('download', { id: 'new', label: '新模型' })
  assert.equal(calls.length, 0)
  await panel.confirm()
  assert.deepEqual(calls, ['new'])
  assert.equal(panel.plan.value, null)
})

test('视觉验证失败明确报错且不切换；切换失败仍展示原选择', async () => {
  const old = { models: [{ id: 'old', installed: true, selected: true }], selected_model_id: 'old', download: {} }
  const panel = vision({ loadVisionModels: async () => old, verifyVisionModel: async () => ({ ok: false, verification: { ready: false, error: '推理失败' } }), selectVisionModel: async () => { throw new Error('新模型不能识图，原选择保留') } })
  await panel.refresh(); await panel.verify({ id: 'new', installed: true })
  assert.match(panel.error.value, /推理失败/)
  assert.equal(panel.snapshot.value.selected_model_id, 'old')
  await panel.use({ id: 'new', installed: true, verified: true })
  assert.match(panel.error.value, /原选择保留/)
  assert.equal(panel.snapshot.value.selected_model_id, 'old')
  assert.equal(panel.emitted.length, 0)
})

test('单模型删除必须使用原预览令牌，失败保留计划可重试', async () => {
  const calls = []
  const panel = vision({ previewVisionRemoval: async id => ({ token: 'reviewed', paths: [id] }), removeVisionModel: async (...args) => { calls.push(args); throw new Error('模型已变化，未删除') } })
  await panel.prepare('remove', { id: 'old', label: '旧模型' })
  assert.equal(calls.length, 0)
  await panel.confirm()
  assert.deepEqual(calls, [['old', 'reviewed']])
  assert.equal(panel.plan.value.token, 'reviewed')
  assert.match(panel.error.value, /未删除/)
})

test('下载中轮询不会被旧清单覆盖显式切换结果', async () => {
  let finishRead
  let reads = 0
  const selected = { selected_model_id: 'new', models: [], download: {} }
  const panel = vision({ loadVisionModels: () => ++reads === 1 ? new Promise(resolve => { finishRead = resolve }) : Promise.resolve(selected), selectVisionModel: async () => ({ ok: true, catalog: selected }) })
  const checking = panel.refresh()
  await panel.use({ id: 'new', installed: true, verified: true })
  finishRead({ selected_model_id: 'old', models: [], download: {} })
  await checking
  assert.equal(panel.snapshot.value.selected_model_id, 'new')
})

test('音色仅显式选择兼容方式，加载失败保留列表且不通知父页为成功', async () => {
  const calls = []
  const panel = voices({ selectManagedVoice: async (...args) => { calls.push(args); throw new Error('加载失败，原音色已恢复') } })
  panel.voices.value = [{ id: 'old', selected: true }]
  await panel.useVoice({ id: 'new' }, { engine: 'aniflive', can_select: false })
  assert.equal(calls.length, 0)
  await panel.useVoice({ id: 'new' }, { engine: 'mio_v2', can_select: true })
  assert.deepEqual(calls, [['new', 'mio_v2']])
  assert.match(panel.error.value, /原音色已恢复/)
  assert.equal(panel.voices.value[0].selected, true)
  assert.equal(panel.emitted.length, 0)
})

test('音色加载成功才刷新父页，原件清理默认不勾选', async () => {
  const panel = voices({ selectManagedVoice: async () => ({ ok: true, verified: true, message: '已验证并选择' }) })
  await panel.useVoice({ id: 'own' }, { engine: 'mio_v2', can_select: true })
  assert.equal(panel.notice.value, '已验证并选择')
  assert.deepEqual(panel.emitted, [['voices-changed'], ['refresh-environment']])
  panel.select({ id: 'own' })
  assert.equal(panel.plan.value, null)
})

test('当前音色不能通过清理流程自动换成别的声音', async () => {
  let previews = 0
  const panel = voices({ previewVoiceRemoval: async () => { previews++ } })
  panel.select({ id: 'own', selected: true })
  await panel.preview()
  assert.equal(previews, 0)
  assert.match(panel.error.value, /先切换到其他音色/)
})

test('视觉使用按钮先取得图片验证结果再切换，不跳过服务端验证门禁', async () => {
  const calls = []
  const panel = vision({ verifyVisionModel: async id => { calls.push(['verify', id]); return { ok: true, verification: { ready: true } } }, selectVisionModel: async id => { calls.push(['select', id]); return { ok: true } } })
  await panel.use({ id: 'new', installed: true })
  assert.deepEqual(calls, [['verify', 'new'], ['select', 'new']])
})

test('部分视觉删除显示实际剩余文件并丢弃不能再次提交的旧预览', async () => {
  const panel = vision({ previewVisionRemoval: async () => ({ token: 'old-plan' }), removeVisionModel: async () => ({ ok: false, removed_bytes: 1024, remaining_files: ['D:/owned/leftover'], message: '文件被占用' }) })
  await panel.prepare('remove', { id: 'old' }); await panel.confirm()
  assert.equal(panel.plan.value, null)
  assert.deepEqual(Array.from(panel.remaining.value), ['D:/owned/leftover'])
  assert.match(panel.error.value, /实际释放/)
})

test('下载请求响应丢失后重查真实后台状态，仍可看到取消入口', async () => {
  const panel = vision({ downloadVisionModel: async () => { throw Object.assign(new Error('请求超时'), { code: 'request_timeout' }) }, loadVisionModels: async () => ({ models: [], download: { active: true, model_id: 'new' } }) })
  await panel.prepare('download', { id: 'new' }); await panel.confirm()
  assert.equal(panel.downloading.value, true)
  assert.equal(panel.plan.value, null)
  assert.match(panel.error.value, /结果尚未确认/)
  assert.ok(panel.timers.length)
})

test('旧音色清单迟到不能覆盖切换后确认的默认音色', async () => {
  let finishRead, reads = 0
  const panel = voices({ loadManagedVoices: () => ++reads === 1 ? new Promise(resolve => { finishRead = resolve }) : Promise.resolve({ voices: [{ id: 'new', selected: true }] }), selectManagedVoice: async () => ({ ok: true, verified: true }) })
  const first = panel.refresh()
  await panel.useVoice({ id: 'new' }, { engine: 'mio_v2', can_select: true })
  finishRead({ voices: [{ id: 'old', selected: true }] }); await first
  assert.equal(panel.voices.value[0].id, 'new')
})

test('音色切换超时不能称为回滚成功，重新读取已实际生效的选择', async () => {
  const panel = voices({ loadManagedVoices: async () => ({ voices: [{ id: 'new', selected: true }] }), selectManagedVoice: async () => { throw Object.assign(new Error('等待超时'), { code: 'request_timeout' }) } })
  panel.voices.value = [{ id: 'old', selected: true }]
  await panel.useVoice({ id: 'new' }, { engine: 'mio_v2', can_select: true })
  assert.match(panel.error.value, /结果尚未确认/)
  assert.equal(panel.voices.value[0].id, 'new')
  assert.equal(panel.notice.value, '')
})

test('验证中读取409不会把候选音色替换为已验证列表', async () => {
  const panel = voices({ loadManagedVoices: async () => { throw Object.assign(new Error('仍在验证，请稍后检查'), { code: 'http_error', status: 409 }) } })
  panel.voices.value = [{ id: 'old', selected: true }]
  await panel.refresh()
  assert.equal(panel.voices.value[0].id, 'old')
  assert.match(panel.error.value, /仍在验证/)
})
