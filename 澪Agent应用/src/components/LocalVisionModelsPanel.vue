<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as modelsApi from '../services/modelManagementApi.js'
import { formatModelBytes } from '../modelCenterState.js'

const emit = defineEmits(['changed'])
const snapshot = ref({ models: [], download: {} }), loading = ref(false), busy = ref(false), error = ref(''), notice = ref(''), plan = ref(null), panel = ref(null), remaining = ref([])
const models = computed(() => snapshot.value.models || [])
const downloading = computed(() => snapshot.value.download?.active === true)
let timer, disposed = false, revision = 0, readId = 0
const uncertainResult = cause => ['request_timeout', 'request_cancelled', 'network_error'].includes(cause?.code) || cause?.status >= 500
function schedule() { clearTimeout(timer); if (!disposed && !busy.value && downloading.value) timer = setTimeout(() => refresh(true), 2000) }
function statusLabel(model) {
  if (snapshot.value.download?.model_id === model.id && downloading.value) return '正在下载'
  if (!model.installed) return '尚未完整安装'
  if (model.verified === true || model.verification?.ready === true) return '本机验证通过'
  if (model.verification?.state === 'failed') return '验证失败，可重试'
  return '文件已安装，待加载验证'
}
async function refresh(silent = false) {
  if (disposed || (loading.value && !silent)) return
  const version = revision, request = ++readId
  loading.value = true
  if (!silent) error.value = ''
  try { const result = await modelsApi.loadVisionModels(); if (!disposed && version === revision && request === readId) snapshot.value = result }
  catch (cause) { if (!disposed && version === revision && request === readId && (!silent || !error.value)) error.value = cause.message || '无法检查视觉模型，请重试。' }
  finally { if (request === readId) { loading.value = false; schedule() } }
}
async function act(callback) {
  if (busy.value || disposed) return
  revision++; clearTimeout(timer)
  busy.value = true; error.value = ''; notice.value = ''
  try { await callback() } catch (cause) {
    if (!disposed) {
      error.value = cause.message || '操作未完成，请重试。'
      if (uncertainResult(cause)) {
        error.value += '。操作结果尚未确认，后台可能仍在处理；请重新检查状态后再操作。'
        plan.value = null
        await refresh(true); emit('changed')
      }
    }
  }
  finally { busy.value = false; schedule() }
}
async function prepare(kind, model) {
  await act(async () => {
    const result = kind === 'remove' ? await modelsApi.previewVisionRemoval(model.id) : {}
    if (disposed) return
    remaining.value = []
    plan.value = { ...result, kind, model_id: model.id, label: model.label || model.id }
    await nextTick(); panel.value?.focus({ preventScroll: true }); panel.value?.scrollIntoView({ block: 'nearest' })
  })
}
async function confirm() {
  const reviewed = plan.value
  if (!reviewed || (reviewed.kind === 'remove' && !reviewed.token)) return
  await act(async () => {
    let result
    if (reviewed.kind === 'download') result = await modelsApi.downloadVisionModel(reviewed.model_id)
    else result = await modelsApi.removeVisionModel(reviewed.model_id, reviewed.token)
    if (disposed) return
    if (result.catalog) snapshot.value = result.catalog
    else if (result.models) snapshot.value = result
    remaining.value = result.remaining_files || []
    if (result.ok === false) {
      error.value = `${result.message || '部分文件未能删除，请重新检查。'} 实际释放 ${formatModelBytes(result.removed_bytes)}。`
      plan.value = null; await refresh(true); emit('changed'); return
    }
    notice.value = result.message || (reviewed.kind === 'download' ? '下载已开始。完成后先验证，再自行决定是否切换。' : `所选模型已删除，实际释放 ${formatModelBytes(result.removed_bytes)}。`)
    plan.value = null; await refresh(true); emit('changed')
  })
}
async function verify(model) {
  if (!model.installed || downloading.value) return
  await act(async () => {
    const result = await modelsApi.verifyVisionModel(model.id)
    if (disposed) return
    if (result.catalog) snapshot.value = result.catalog
    plan.value = null
    if (result.ok === false || result.verification?.ready === false) error.value = result.message || result.verification?.error || '验证未通过，原模型选择保留。'
    else notice.value = result.message || '验证通过。点击“使用这个模型”后才会切换。'
    await refresh(true)
  })
}
async function use(model) {
  if (!model.installed || model.selected || downloading.value) return
  await act(async () => {
    if (model.verified !== true && model.verification?.ready !== true) {
      const checked = await modelsApi.verifyVisionModel(model.id)
      if (disposed) return
      if (checked.catalog) snapshot.value = checked.catalog
      if (checked.ok !== true || checked.verification?.ready !== true) throw new Error(checked.message || checked.verification?.error || '验证未通过，原模型选择保留。')
    }
    const result = await modelsApi.selectVisionModel(model.id)
    if (disposed) return
    if (result.ok === false) throw new Error(result.message || '未能切换，原模型选择保留。')
    if (result.catalog) snapshot.value = result.catalog
    plan.value = null
    notice.value = result.message || '已切换视觉模型；其他已安装模型仍保留。'
    await refresh(true); emit('changed')
  })
}
async function cancelDownload() {
  await act(async () => {
    const result = await modelsApi.cancelVisionDownload()
    if (disposed) return
    if (result.models) snapshot.value = result
    notice.value = result.message || '已请求取消下载，原模型选择保留。'
    await refresh(true)
  })
}
onMounted(refresh)
onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })
</script>

<template>
  <section class="vision-models" aria-label="本地视觉模型选择与保留">
    <header><h4>选择本地视觉模型</h4><button type="button" :disabled="loading || busy" @click="refresh()">{{ loading ? '正在检查…' : '重新检查视觉模型' }}</button></header>
    <p>升级后保留原来的模型选择。新模型可以先下载和验证，准备好再切换；旧模型可保留，也可以单独删除。</p>
    <p>暂时不更换也能继续使用 Mio。停止服务只释放内存，保留模型文件。</p>
    <p v-if="snapshot.migration_message" role="status">{{ snapshot.migration_message }}</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <ul v-if="remaining.length"><li v-for="path in remaining" :key="path">尚未删除：{{ path }}</li></ul>
    <div v-if="downloading" role="status"><p>{{ snapshot.download.message || '模型正在后台下载，可以离开此页，稍后回来查看。' }}</p><button v-if="snapshot.download.can_cancel !== false" type="button" :disabled="busy" @click="cancelDownload">取消模型下载</button></div>
    <p v-if="snapshot.download?.error" class="error">下载未完成：{{ snapshot.download.error }}</p>
    <article v-for="model in models" :key="model.id" :aria-label="model.label || model.id">
      <strong>{{ model.label || model.id }}</strong><span v-if="model.selected"> · 当前使用</span><span v-if="model.recommended"> · 推荐</span><span v-if="model.legacy"> · 旧版可保留</span>
      <p>{{ statusLabel(model) }}<template v-if="model.installed">，占用 {{ formatModelBytes(model.size_bytes) }}</template></p>
      <p v-if="model.verification?.error" class="error">{{ model.verification.error }}</p>
      <div class="actions"><button v-if="!model.installed" type="button" :disabled="busy || downloading" @click="prepare('download', model)">下载 {{ model.label || model.id }}</button><button v-if="model.installed" type="button" :disabled="busy || downloading" @click="verify(model)">加载并验证</button><button v-if="model.installed && !model.selected" type="button" :disabled="busy || downloading" @click="use(model)">验证并使用这个模型</button><button v-if="model.can_remove && !model.selected" type="button" :disabled="busy || downloading" @click="prepare('remove', model)">仅删除这个模型</button></div>
      <p v-if="model.selected">先切换到其他模型，再删除当前模型。</p>
      <details><summary>模型来源与文件说明</summary><p>模型标识：{{ model.id }}</p><p v-if="model.shared_bytes">含共用文件 {{ formatModelBytes(model.shared_bytes) }}，删除时会保留其他模型需要的文件。</p><a v-for="source in model.sources || []" :key="source.url" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.label }}</a></details>
    </article>
    <section v-if="plan" ref="panel" class="confirmation" tabindex="-1" aria-label="确认视觉模型操作">
      <h4>{{ plan.kind === 'download' ? '下载' : '删除' }} {{ plan.label }}</h4>
      <template v-if="plan.kind === 'download'"><p>只下载所选模型，不会自动切换或删除现有模型。下载需要网络和磁盘空间；如未安装视觉运行器，请先使用下方组件安装入口。</p><button type="button" :disabled="busy" @click="confirm">确认下载</button></template>
      <template v-else><p>{{ plan.impact }}</p><p>{{ plan.preserved }}</p><p>预计释放 {{ formatModelBytes(plan.size_bytes) }}</p><ul><li v-for="path in plan.paths || []" :key="path">将删除：{{ path }}</li><li v-for="path in plan.shared_files || []" :key="path">保留共用文件：{{ path }}</li></ul><button type="button" :disabled="busy || !plan.token" @click="confirm">确认删除此模型</button></template>
      <button type="button" :disabled="busy" @click="plan = null">暂不处理</button>
    </section>
    <p v-if="busy" role="status">正在处理；加载验证可能需要一些时间。</p>
  </section>
</template>

<style scoped>
.vision-models { min-width:0; line-height:1.7; overflow-wrap:anywhere; }
.vision-models header,.actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.vision-models header { justify-content:space-between; }
.vision-models h4 { margin:0; font-size:var(--text-lg); }
.vision-models article { border-top:1px solid var(--line); margin-top:12px; padding-top:12px; }
.vision-models .confirmation { border:1px solid var(--line); padding:12px; margin-top:12px; }
.vision-models button { font:inherit; color:inherit; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:6px 10px; max-width:100%; white-space:normal; cursor:pointer; }
.vision-models button:disabled { opacity:.5; cursor:wait; }
.vision-models .error { color:var(--danger); }
.vision-models a { display:inline-block; color:var(--accent); margin-right:12px; }
</style>
