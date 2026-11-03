<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as modelsApi from '../services/modelManagementApi.js'
import * as depsApi from '../services/dependenciesApi.js'
import { downloadActions, formatModelBytes, modelStatus } from '../modelCenterState.js'
import CapabilityChecks from './CapabilityChecks.vue'
import VoiceStoragePanel from './VoiceStoragePanel.vue'
import AnifSetupPanel from './AnifSetupPanel.vue'
import LocalVisionModelsPanel from './LocalVisionModelsPanel.vue'

const emit = defineEmits(['refresh-environment', 'navigate', 'voices-changed', 'storage-changed'])
const catalog = ref({ items: [], storage: [], hardware: {} })
const error = ref(''), notice = ref(''), busy = ref(false), loading = ref(false)
const category = ref('speech'), confirmation = ref(null), folder = ref(''), migrate = ref(false), storageId = ref('voice')
const confirmationPanel = ref(null)
const cacheRemaining = ref([])
const categories = [{ id: 'speech', label: '让 Mio 说话' }, { id: 'recognition', label: '听懂我说话' }, { id: 'vision', label: '看懂屏幕和图片' }]
const visibleItems = computed(() => catalog.value.items.filter(item => item.category === category.value))
let timer, destroyed = false
const dependencyFor = item => item.dependency
const sectionLabels = { items: '模型状态', storage: '磁盘空间', hardware: '电脑配置' }
const sectionMessages = computed(() => Object.entries(catalog.value.sections || {}).filter(([, state]) => state.state !== 'ready').map(([id, state]) => ({ id, text: `${sectionLabels[id] || id}：${state.state === 'error' ? state.error || '检查失败，可重新检查' : '正在后台检查'}${state.has_value ? '，当前显示上次结果' : ''}` })))
const progressFor = item => dependencyFor(item)?.progress || {}
const failureFor = item => dependencyFor(item)?.last_error || (['error', 'failed'].includes(progressFor(item).stage) ? progressFor(item).message || '安装未完成，请检查空间与网络后重试。' : '')
const actionLabels = { pause: '暂停下载', resume: '继续下载', cancel: '取消下载' }
async function showConfirmation(plan) { confirmation.value = plan; await nextTick(); confirmationPanel.value?.scrollIntoView({ block: 'nearest' }); confirmationPanel.value?.focus({ preventScroll: true }) }

async function refresh(silent = false, force = !silent) {
  if (loading.value) return
  loading.value = true
  if (!silent) error.value = ''
  try {
    const models = await modelsApi.loadModelCatalog({ refresh: force })
    if (destroyed) return
    catalog.value = models
    clearTimeout(timer)
    if (models.checking || models.items.some(item => item.dependency?.installing)) timer = setTimeout(() => refresh(true), 1800)
  } catch (cause) { if (!destroyed) { error.value = cause.message || '无法检查模型，请重试'; if (catalog.value.checking || catalog.value.items.some(item => item.dependency?.installing)) timer = setTimeout(() => refresh(true), 3500) } }
  finally { loading.value = false }
}

async function act(callback) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await callback() } catch (cause) { error.value = cause.message || '操作未完成，请重试' }
  finally { busy.value = false }
}

async function prepareInstall(item) {
  await act(async () => {
    const dep = dependencyFor(item)
    let pkg = null
    if (['genie_runtime', 'gpt_sovits'].includes(item.dependency_id)) pkg = await depsApi.loadDependencyPackage(item.dependency_id)
    await showConfirmation({ kind: 'install', item, dep, pkg })
  })
}

async function prepareRemoval(item) {
  await act(async () => { await showConfirmation({ ...await depsApi.previewDependencyUninstall(item.dependency_id), kind: 'remove', item }) })
}

async function previewLocation() {
  await act(async () => {
    const path = folder.value.trim()
    if (!path) throw new Error('请填写新的完整文件夹路径。')
    await showConfirmation({ ...await modelsApi.previewStorage(storageId.value, path, migrate.value), kind: 'storage', storageId: storageId.value, path, migrate: migrate.value })
  })
}

async function prepareCache() {
  await act(async () => { cacheRemaining.value = []; await showConfirmation({ ...await modelsApi.previewModelCache(), kind: 'cache' }) })
}

async function confirm(offline = false) {
  const plan = confirmation.value
  if (!plan || busy.value) return
  await act(async () => {
    if (plan.kind === 'install') {
      let result
      if (offline) {
        const picker = window.pywebview?.api?.install_dependency_package
        if (!picker) throw new Error('请在 Mio 桌面程序中选择离线 ZIP 安装包。')
        result = await picker(plan.item.dependency_id)
        if (result?.canceled) return
        if (!result?.ok) throw new Error(result?.error || '离线安装未启动')
      } else result = await depsApi.installDependency(plan.item.dependency_id)
      notice.value = result.message || '已启动安装；完成后请进行功能实测。'
    } else if (plan.kind === 'remove') {
      const result = await depsApi.uninstallDependency(plan.item.dependency_id, plan.token)
      notice.value = result.message || '已卸载所选组件。'
    } else if (plan.kind === 'cache') {
      const result = await modelsApi.clearModelCache(plan.token)
      cacheRemaining.value = result.remaining_files || []
      const message = `${result.message || '缓存清理已处理。'} 实际释放 ${formatModelBytes(result.removed_bytes)}。`
      if (result.ok === false) error.value = message
      else notice.value = message
    } else {
      const result = await modelsApi.commitStorage(plan.storageId, plan.path, plan.migrate, plan.token)
      notice.value = result.message || '安装位置已更新；迁移时保留原位置文件，不会立即释放原磁盘空间。'
      emit('storage-changed')
    }
    confirmation.value = null
    await refresh(true, true); emit('refresh-environment')
  })
}

async function release(item) {
  await act(async () => {
    const result = await modelsApi.releaseModel(item.id)
    notice.value = result.message || '服务已停止，内存已释放；模型文件仍保留。'
    await refresh(true, true); emit('refresh-environment')
  })
}

async function control(item, action) {
  await act(async () => {
    const result = await depsApi.controlDependencyDownload(item.dependency_id, action)
    notice.value = result.message || (action === 'cancel' ? '已取消下载，保留已下载部分。' : '下载状态已更新。')
    await refresh(true, true)
  })
}

async function openAnif() {
  category.value = 'speech'
  await nextTick()
  document.querySelector('.model-center [data-model-id="aniflive"]')?.scrollIntoView({ block: 'start', behavior: 'smooth' })
}

onMounted(() => refresh())
onBeforeUnmount(() => { destroyed = true; clearTimeout(timer) })
</script>

<template>
  <section class="model-center" aria-label="模型管理">
    <header><div><h3>模型与功能</h3><p>按用途选择，需要哪个再安装哪个。升级保留已有模型和个人音色，更换后可自行决定是否清理旧文件。</p></div><button type="button" :disabled="loading || busy" @click="refresh()">{{ loading ? '正在检查…' : '重新检查' }}</button></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="notice" role="status" class="notice">{{ notice }}</p>
    <p v-for="message in sectionMessages" :key="message.id" role="status">{{ message.text }}</p>
    <div v-if="catalog.hardware" class="hardware"><strong>这台电脑</strong><span>内存 {{ formatModelBytes(catalog.hardware.ram_total_bytes) }}</span><span v-for="gpu in catalog.hardware.gpus" :key="gpu.name">{{ gpu.name }}</span><p>{{ catalog.hardware.recommendation }}</p><small v-for="note in catalog.hardware.limitations" :key="note">{{ note }}</small></div>
    <nav aria-label="模型用途"><button v-for="tab in categories" :key="tab.id" type="button" :aria-pressed="category === tab.id" @click="category = tab.id">{{ tab.label }}</button></nav>
    <KeepAlive><LocalVisionModelsPanel v-if="category === 'vision'" @changed="refresh(); emit('refresh-environment')" /></KeepAlive>
    <p v-if="!visibleItems.length && !loading">暂无可展示的模型，请重新检查。</p>
    <article v-for="item in visibleItems" :key="item.id" :data-model-id="item.id" class="model-card">
      <div class="card-title"><strong>{{ item.label }}</strong><span>{{ modelStatus(item, dependencyFor(item)) }}</span><b v-if="item.selected">当前选择</b></div>
      <p>{{ item.description }}</p>
      <p v-if="failureFor(item)" role="alert" class="error">上次安装：{{ failureFor(item) }} 可点击“重试安装”；仍失败时到高级组件中预览脱敏诊断。</p>
      <small v-if="item.verification === 'experimental'">实验支持，效果和速度请先试听。</small><small v-if="item.verification === 'unverified'">尚未在此版本验证，请先检查兼容性。</small>
      <div v-if="dependencyFor(item)?.installing || ['paused','cancelled'].includes(progressFor(item).stage)" class="download-state"><progress :value="progressFor(item).percent || 0" max="100" /><span>{{ progressFor(item).message || '安装中，等待进度…' }}</span><span v-if="progressFor(item).speed_mb_s">{{ Number(progressFor(item).speed_mb_s).toFixed(1) }} MB/s</span><button v-for="action in downloadActions(progressFor(item))" :key="action" type="button" :disabled="busy" @click="control(item, action)">{{ actionLabels[action] }}</button><small v-if="progressFor(item).stage === 'cancelled'">已下载部分保留，可再次安装续传；释放磁盘请到下方缓存管理。</small></div>
      <div class="actions"><button v-if="item.dependency_id && !dependencyFor(item)?.installing && item.status !== 'unavailable'" type="button" :disabled="busy || item.status === 'checking'" @click="prepareInstall(item)">{{ item.status === 'checking' ? '检查安装状态中…' : failureFor(item) ? '重试安装' : item.installed ? '修复 / 重新安装' : '获取与安装' }}</button><button v-if="item.installed || item.selected || item.category === 'speech'" type="button" @click="emit('navigate', item.category === 'speech' ? 'settings-voice' : 'settings-pet')">{{ item.category === 'speech' ? '选择音色 / 试听' : '去使用设置' }}</button><button v-if="item.can_release" type="button" :disabled="busy" @click="release(item)">停止服务，释放内存</button><button v-if="item.can_uninstall && item.dependency_id" type="button" :disabled="busy" @click="prepareRemoval(item)">{{ item.id === 'ollama_vision' ? '卸载整个视觉功能' : '卸载，释放磁盘' }}</button></div>
      <details><summary>来源、大小与兼容说明</summary><p>下载大小：{{ formatModelBytes(item.size_bytes) }}</p><p v-if="item.install_path">安装位置：{{ item.install_path }}</p><p v-if="item.uninstall_impact">卸载影响：{{ item.uninstall_impact }}</p><p v-if="item.requirements">{{ item.requirements }}</p><p v-for="note in item.limitations" :key="note">{{ note }}</p><a v-for="source in item.sources" :key="source.url" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.label }}</a><p v-if="item.install_mode === 'external'">此模型需要自行获取兼容文件，再到声音设置中导入；列出模型不代表已安装。</p></details>
      <AnifSetupPanel v-if="item.id === 'aniflive'" @changed="refresh(); emit('refresh-environment'); emit('voices-changed')" @navigate="emit('navigate', $event)" />
    </article>
    <section v-if="confirmation" ref="confirmationPanel" class="confirmation" role="region" aria-label="确认模型操作" tabindex="-1">
      <h4>{{ confirmation.kind === 'install' ? `安装 ${confirmation.item.label}` : confirmation.kind === 'remove' ? `卸载 ${confirmation.item.label}` : confirmation.kind === 'cache' ? '确认清理下载缓存' : '确认更改安装位置' }}</h4>
      <template v-if="confirmation.kind === 'install'"><p>大小：{{ formatModelBytes(confirmation.pkg?.size_bytes ?? confirmation.item.size_bytes) }}</p><p>保存到：{{ confirmation.dep?.install_path || confirmation.item.install_path || '程序管理的模型目录' }}</p><p>{{ confirmation.pkg?.message || '安装完成后还需要启动与实测；可在此页面查看进度与重试。' }}</p><p v-if="confirmation.dep?.offline_only">当前版本需要对应离线 ZIP 包；可先查看下方来源或高级组件中的获取说明。</p><a v-for="source in confirmation.item.sources" :key="source.url" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.label }}</a><button v-if="!confirmation.dep?.offline_only || confirmation.dep?.local_package_available" type="button" :disabled="busy" @click="confirm()">确认安装</button><button v-if="confirmation.pkg" type="button" :disabled="busy" @click="confirm(true)">选择离线包安装</button></template>
      <template v-else><p>{{ confirmation.impact }}</p><p>{{ confirmation.preserved }}</p><p>{{ ['remove','cache'].includes(confirmation.kind) ? '预计释放' : '需要复制' }} {{ formatModelBytes(confirmation.size_bytes) }}</p><p v-if="confirmation.kind === 'storage'">目标：{{ confirmation.destination || confirmation.path }}。复制并切换时原文件保留，不会释放原磁盘空间。</p><ul><li v-for="path in confirmation.paths" :key="path">{{ path }}</li></ul><button type="button" :disabled="busy || !confirmation.token" @click="confirm()">{{ busy ? '正在处理，请勿关闭…' : '确认执行' }}</button></template>
      <button type="button" :disabled="busy" @click="confirmation = null">取消</button>
    </section>
    <details class="storage"><summary>安装位置与磁盘空间</summary><p>改安装位置只处理 Mio 管理的模型，个人外部文件保持引用。已有模型需复制后切换；原文件保留。切换后会重新载入已保存的声音与模型路径，请先保存尚未保存的修改。</p><p v-for="entry in catalog.storage" :key="entry.id">{{ entry.label }}：{{ entry.path }}，已用 {{ formatModelBytes(entry.size_bytes) }}，可用 {{ formatModelBytes(entry.free_bytes) }}</p><label>模型类型<select v-model="storageId"><option value="voice">语音模型</option><option value="vision">视觉模型</option></select></label><label>新安装文件夹<input v-model="folder" placeholder="例如 D:\Mio模型" :disabled="busy" /></label><label><input v-model="migrate" type="checkbox" :disabled="busy" />复制已有受管模型并切换（保留原文件）</label><button type="button" :disabled="busy" @click="previewLocation">检查空间和影响</button></details>
    <details><summary>下载缓存管理</summary><p>仅清理 Mio 管理的安装压缩包及未完成下载；已安装模型、个人音色与外部缓存保留。正在安装时不能清理。</p><button type="button" :disabled="busy" @click="prepareCache">检查可清理的下载缓存</button><ul v-if="cacheRemaining.length"><li v-for="path in cacheRemaining" :key="path">尚未清理：{{ path }}</li></ul></details>
    <details><summary>个人音色文件管理</summary><VoiceStoragePanel @refresh-environment="refresh(); emit('refresh-environment')" @voices-changed="emit('voices-changed')" @navigate="emit('navigate', $event)" @manage-anif="openAnif" /></details>
    <details><summary>功能实测（会加载模型）</summary><p>安装、运行与真正识别/发声是不同状态；这里通过最小请求检查实际能力。</p><CapabilityChecks @navigate="emit('navigate', $event)" /></details>
    <p class="footnote">仅在支持续传的下载阶段显示暂停按钮。</p>
  </section>
</template>

<style scoped>
.model-center { display:grid; gap:16px; font-size: var(--text-base); line-height:1.65; color:var(--text); min-width:0; }
.model-center header,.card-title,.actions,.download-state { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.model-center header { justify-content:space-between; }
.model-center h3,.model-center h4 { font-size: var(--text-lg); margin:0 0 6px; }
.model-center p { margin:0 0 6px; }
.model-center button,.model-center input,.model-center select { font:inherit; color:inherit; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:7px 11px; box-sizing:border-box; max-width:100%; }
.model-center select { appearance:none; background-image:none; }
.model-center button { cursor:pointer; }
.model-center button:disabled { opacity:.5; cursor:wait; }
.model-center nav { display:flex; gap:16px; flex-wrap:wrap; border-bottom:1px solid var(--line); }
.model-center nav button { border:0; border-bottom:1px solid transparent; border-radius:0; padding:6px 0; background:transparent; }
.model-center nav [aria-pressed=true] { border-bottom-color:var(--accent); color:var(--accent); }
.model-card,.hardware,.storage { border:0; border-bottom:1px solid var(--line); padding:0 0 16px; overflow-wrap:anywhere; }
.card-title>strong { font-size: var(--text-lg); }
.card-title>span,.model-center small,.footnote { color:var(--text-muted); }
.card-title>b { color:inherit; font-size: var(--text-xs); font-weight:400; }
.hardware>span { margin-left:12px; }
.model-center small { display:block; font-size: var(--text-xs); }
.model-center details { margin-top:8px; }
.model-center summary { cursor:pointer; font-size: var(--text-base); list-style:none; text-decoration:underline; text-underline-offset:3px; }
.model-center summary::-webkit-details-marker { display:none; }
.actions { margin:10px 0; }
.model-center a { color:var(--accent); display:inline-block; margin:5px 10px 5px 0; text-decoration:underline; }
.error { color:var(--danger); }
.notice { color:var(--text); }
.confirmation { padding:16px; border:1px solid var(--line); border-radius:6px; background:var(--surface); overflow-wrap:anywhere; }
.confirmation button { margin:10px 8px 0 0; }
.storage label { display:block; margin:10px 0; }
.storage label>input:not([type=checkbox]),.storage select { display:block; width:100%; margin-top:5px; }
.download-state { padding:8px 0; }
.download-state progress { max-width:100%; accent-color:var(--accent); }
.model-center :deep(.capability-checks) { border:0; padding:0; border-radius:0; }
.model-center :deep(.capability-checks h3) { font-size: var(--text-lg); }
@media(max-width:520px) { .model-center button { white-space:normal; } }
</style>
