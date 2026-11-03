import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { computed, ref } from 'vue'
import { formatModelBytes } from './modelCenterState.js'

function setup(mocks = {}) {
  const file = fs.readFileSync(new URL('./components/AnifSetupPanel.vue', import.meta.url), 'utf8')
  const script = file.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  let cleanup
  const timers = [], emitted = [], revoked = []
  const context = {
    computed, ref, formatModelBytes, onBeforeUnmount(fn) { cleanup = fn },
    defineEmits: () => (...args) => emitted.push(args),
    setTimeout(fn, delay) { timers.push({ fn, delay }); return timers.length }, clearTimeout() {},
    URL: { createObjectURL: () => 'blob:preview', revokeObjectURL: url => revoked.push(url) },
    anifApi: { loadAnifSetup: async () => ({ voices: [{ id: 'own', name: '自己的声音', compatible: true }], can_start: true }), ...mocks },
  }
  vm.runInNewContext(script + '\nresult = { opened, loading, busy, snapshot, voiceId, error, notice, removal, audioUrls, job, running, ready, active, preparedVoiceName, downloadProgress, canStart, canActivate, canUninstall, refresh, toggle, start, cancel, listen, activate, prepareRemoval, remove };', context)
  return { ...context.result, timers, emitted, revoked, cleanup: () => cleanup() }
}

test('初始可选安装不触发模型加载或下载；展开仅检查状态', async () => {
  let installs = 0, checks = 0
  const panel = setup({ loadAnifSetup: async () => { checks++; return { voices: [], can_start: false } }, startAnifSetup: async () => { installs++ } })
  assert.equal(panel.opened.value, false)
  assert.equal(checks, 0)
  panel.toggle()
  await Promise.resolve()
  assert.equal(panel.opened.value, true)
  assert.equal(checks, 1)
  assert.equal(installs, 0)
})

test('无兼容音色或检查未通过时不能启动，检查失败也可关闭界面', async () => {
  let starts = 0
  const panel = setup({ loadAnifSetup: async () => { throw new Error('检查失败') }, startAnifSetup: async () => { starts++ } })
  panel.opened.value = true
  await panel.refresh()
  await panel.start()
  assert.equal(starts, 0)
  assert.match(panel.error.value, /检查失败/)
  panel.toggle()
  assert.equal(panel.opened.value, false)
})

test('提交准确的音色ID，运行中禁止重复启动且收起不取消后台任务', async () => {
  const calls = []
  const state = { voices: [{ id: 'own', compatible: true }], can_start: true }
  const panel = setup({
    loadAnifSetup: async () => state,
    startAnifSetup: async id => { calls.push(id); state.job = { id: 'build-1', stage: 'build', running: true, can_cancel: true }; return { job: state.job } },
  })
  await panel.refresh()
  panel.opened.value = true
  await panel.start()
  await panel.start()
  panel.toggle()
  assert.deepEqual(calls, ['own'])
  assert.equal(panel.opened.value, false)
  assert.equal(panel.running.value, true)
  assert.ok(panel.timers.length)
})

test('启动成功后状态暂时不可读仍轮询，不把网络失败当成编译完成', async () => {
  const panel = setup({
    startAnifSetup: async () => ({ job: { id: 'build-1', stage: 'download', running: true } }),
    loadAnifSetup: async () => { throw new Error('网络暂时中断') },
  })
  panel.snapshot.value = { voices: [{ id: 'own', compatible: true }], can_start: true }
  panel.voiceId.value = 'own'
  await panel.start()
  assert.equal(panel.running.value, true)
  assert.equal(panel.ready.value, false)
  assert.equal(panel.canActivate.value, false)
  assert.equal(panel.timers.at(-1).delay, 4000)
})

test('未验收不读取试听也不激活；服务端显式拒绝优先于ready显示', async () => {
  let audio = 0, activation = 0
  const panel = setup({ loadAnifPreview: async () => { audio++ }, activateAnifSetup: async () => { activation++ } })
  panel.snapshot.value = { job: { id: 'x', stage: 'build', running: true } }
  await panel.listen('zh'); await panel.activate()
  assert.equal(audio, 0); assert.equal(activation, 0)
  panel.snapshot.value = { job: { id: 'x', stage: 'ready', ready: true, running: false }, can_activate: false }
  await panel.activate()
  assert.equal(activation, 0)
})

test('明确使用后才激活，刷新父界面；安装ready本身不激活', async () => {
  let activation = 0
  const state = { job: { id: 'x', stage: 'ready', ready: true, running: false }, can_activate: true }
  const panel = setup({ loadAnifSetup: async () => state, activateAnifSetup: async () => { activation++; return { message: '已选择' } } })
  await panel.refresh()
  assert.equal(activation, 0)
  await panel.activate()
  assert.equal(activation, 1)
  assert.equal(panel.notice.value, '已选择')
  assert.deepEqual(panel.emitted, [['changed']])
})

test('切换任务时回收旧试听URL，迟到的试听不会污染新任务', async () => {
  let resolve
  const panel = setup({ loadAnifPreview: () => new Promise(done => { resolve = done }), loadAnifSetup: async () => ({ job: { id: 'new', stage: 'build', running: true } }) })
  panel.snapshot.value = { job: { id: 'old', stage: 'ready', ready: true, running: false } }
  panel.audioUrls.value = { ja: 'blob:old' }
  const pending = panel.listen('zh')
  await panel.refresh()
  resolve({})
  await pending
  assert.deepEqual(panel.revoked, ['blob:old'])
  assert.equal(Object.keys(panel.audioUrls.value).length, 0)
})

test('取消请求保留后端真实运行状态，不能提前显示停止成功', async () => {
  let cancelled = 0
  const state = { job: { id: 'x', stage: 'build', running: true, can_cancel: true } }
  const panel = setup({ loadAnifSetup: async () => state, cancelAnifSetup: async () => { cancelled++; return { message: '正在停止' } } })
  await panel.refresh(); await panel.cancel()
  assert.equal(cancelled, 1)
  assert.equal(panel.running.value, true)
  assert.equal(panel.notice.value, '正在停止')
})

test('卸载先预览再携令牌确认，失败保留计划和错误', async () => {
  const calls = []
  const panel = setup({ previewAnifUninstall: async () => ({ token: 'owned-token', size_bytes: 123 }), uninstallAnifSetup: async token => { calls.push(token); throw new Error('文件仍在使用') } })
  await panel.remove()
  assert.equal(calls.length, 0)
  await panel.prepareRemoval()
  assert.equal(calls.length, 0)
  await panel.remove()
  assert.deepEqual(calls, ['owned-token'])
  assert.equal(panel.removal.value.token, 'owned-token')
  assert.match(panel.error.value, /仍在使用/)
})

test('卸载成功回收试听并清除确认计划；组件移除后不再更新迟到请求', async () => {
  const panel = setup({ uninstallAnifSetup: async () => ({ message: '已卸载' }) })
  panel.removal.value = { token: 'owned' }
  panel.audioUrls.value = { zh: 'blob:before' }
  await panel.remove()
  assert.equal(panel.removal.value, null)
  assert.deepEqual(panel.revoked, ['blob:before'])
  panel.cleanup()
  assert.equal(panel.audioUrls.value.zh, undefined)
})

test('部分卸载不能显示成功，刷新后仍保留未删除提示', async () => {
  const panel = setup({ uninstallAnifSetup: async () => ({ ok: false, message: '部分文件仍被占用，请稍后重试卸载', remaining_files: ['owned'] }) })
  panel.removal.value = { token: 'owned' }
  await panel.remove()
  assert.match(panel.error.value, /仍被占用/)
  assert.equal(panel.notice.value, '')
})

test('新声包编译后旧活动音色不能禁用激活；试听名称始终绑定编译快照', async () => {
  const panel = setup()
  panel.snapshot.value = {
    voices: [{ id: 'old-voice', name: '旧音色' }, { id: 'new-voice', name: '新音色' }],
    job: { id: 'new-job', voice_id: 'new-voice', stage: 'ready', ready: true, running: false },
    installed: { job_id: 'old-job', voice_id: 'old-voice', active: true }, selected: true, can_activate: true,
  }
  panel.voiceId.value = 'old-voice'
  assert.equal(panel.active.value, false)
  assert.equal(panel.canActivate.value, true)
  assert.equal(panel.preparedVoiceName.value, '新音色')
  panel.snapshot.value.installed.job_id = 'new-job'
  assert.equal(panel.active.value, true)
})

test('ready阶段没有真实验收标记时不能显示试听', async () => {
  const panel = setup()
  panel.snapshot.value = { job: { id: 'x', stage: 'ready', ready: false, running: false }, can_activate: false }
  assert.equal(panel.ready.value, false)
  assert.equal(panel.canActivate.value, false)
})

test('大文件下载显示实际字节进度，进入编译后隐藏旧下载字节', () => {
  const panel = setup()
  panel.snapshot.value = { job: { id: 'x', stage: 'downloading', running: true, downloaded_bytes: 1024 ** 3, total_bytes: 3 * 1024 ** 3 } }
  assert.equal(panel.downloadProgress.value, '已下载 1.0 GB / 3.0 GB')
  panel.snapshot.value.job.downloaded_bytes = 2 * 1024 ** 3
  assert.equal(panel.downloadProgress.value, '已下载 2.0 GB / 3.0 GB')
  panel.snapshot.value.job.stage = 'build'
  assert.equal(panel.downloadProgress.value, '')
  panel.snapshot.value.job.stage = 'downloading'
  panel.snapshot.value.job.total_bytes = null
  assert.equal(panel.downloadProgress.value, '')
})
