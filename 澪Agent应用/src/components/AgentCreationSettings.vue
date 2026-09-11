<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { CheckCircle2, Film, FolderOpen, ImagePlus, Play, RefreshCw, Save, Search, Trash2, Upload, X } from '@lucide/vue'
import * as api from '../services/creationApi.js'
import { apiRequest } from '../services/api.js'
import { selectLocalPath } from '../services/localPicker.js'

defineProps({ section: { type: String, default: 'creation' } })
const data = ref(null)
const ready = ref(false)
const busy = ref(false)
const scanning = ref(false)
const candidates = ref([])
const scanned = ref(false)
const localWorkflows = ref([])
const workflowQuery = ref('')
const workflowScanning = ref(false)
const workflowScanned = ref(false)
const workflowTruncated = ref(false)
const error = ref('')
const notice = ref('')
const preflight = ref(null)
let connectionTimer = null
let disposed = false
const environment = reactive({ comfyui_root: '', comfyui_base_url: 'http://127.0.0.1:8188' })
const defaults = reactive({ image_workflow_id: '', video_workflow_id: '' })
const savedEnvironment = reactive({ ...environment })
const savedDefaults = reactive({ ...defaults })
const environmentDirty = computed(() => JSON.stringify(environment) !== JSON.stringify(savedEnvironment))
const defaultsDirty = computed(() => JSON.stringify(defaults) !== JSON.stringify(savedDefaults))
const workflows = computed(() => data.value?.workflows || [])
const fileInput = ref(null)
const importing = ref(false)
const importForm = ref(null)
const importNotes = ref([])
const importSources = ref([])
const imported = reactive({ label: '', media_type: 'image', prompt: null, bindings: {} })
const fields = [ ['prompt', '正向提示词'], ['negative_prompt', '负向提示词'], ['reference_image', '参考图'], ['width', '宽度'], ['height', '高度'], ['seed', '随机种子'], ['steps', '采样步数'], ['cfg', 'CFG'], ['batch_size', '批量数量'], ['duration_seconds', '时长（秒）'], ['fps', '帧率'] ]
const targets = computed(() => Object.entries(imported.prompt || {}).flatMap(([nodeId, node]) => Object.entries(node.inputs || {}).filter(([, value]) => ['string', 'number'].includes(typeof value)).map(([input, value]) => ({ id: JSON.stringify([nodeId, input]), numeric: typeof value === 'number', label: `${nodeId} · ${node._meta?.title || node.class_type} · ${input}`, input }))))
function choices(parameter) { return targets.value.filter(item => item.numeric === !['prompt', 'negative_prompt', 'reference_image'].includes(parameter) && !['filename_prefix', 'save_output'].includes(item.input)) }
function message(cause) { error.value = cause.message; notice.value = '' }
async function load() {
  busy.value = true; error.value = ''
  try {
    const config = await api.loadCreationConfiguration()
    Object.assign(environment, config.environment); Object.assign(savedEnvironment, environment)
    Object.assign(defaults, config.defaults); Object.assign(savedDefaults, defaults)
    data.value = await api.loadCreationBootstrap()
    ready.value = true
  } catch (cause) { message(cause) }
  finally { busy.value = false }
}
async function discover() {
  scanning.value = true
  try { candidates.value = (await api.discoverComfyUi()).candidates || []; scanned.value = true }
  catch (cause) { message(cause) }
  finally { scanning.value = false }
}
async function chooseInstallation() {
  try {
    const result = await selectLocalPath('comfyui')
    if (result) { environment.comfyui_root = result.path; notice.value = '已选择安装目录，保存后生效。'; error.value = '' }
  } catch (cause) { message(cause) }
}
async function findWorkflows() {
  workflowScanning.value = true; error.value = ''
  try {
    const result = await api.discoverLocalWorkflows(workflowQuery.value)
    localWorkflows.value = result.files || []; workflowTruncated.value = Boolean(result.truncated); workflowScanned.value = true
  } catch (cause) { message(cause) }
  finally { workflowScanning.value = false }
}
async function addSearchFolder() {
  try {
    const result = await selectLocalPath('directory')
    if (!result) return
    const current = await apiRequest('/api/agent/work/file-roots')
    await apiRequest('/api/agent/work/file-roots', { method: 'PUT', body: JSON.stringify({ paths: [...new Set([...(current.roots || []).filter(p => p !== current.output_root), result.path])] }) })
    notice.value = '该目录已加入文件权限，澪可以搜索和读取其中的资料。'
    await findWorkflows()
  } catch (cause) { message(cause) }
}
async function openWorkflowPicker() {
  if (!window.pywebview?.api?.select_agent_path) { fileInput.value.click(); return }
  try {
    const result = await selectLocalPath('workflow')
    if (result) await prepareWorkflow(result.prompt, result.name)
  } catch (cause) { message(cause) }
}
async function openFoundWorkflow(item) {
  busy.value = true
  try { const result = await api.readLocalWorkflow(item.path); await prepareWorkflow(result.prompt, result.name) }
  catch (cause) { message(cause) }
  finally { busy.value = false }
}
async function prepareWorkflow(graph, name) {
  if (!graph || typeof graph !== 'object' || Array.isArray(graph)) throw new Error('请选择 ComfyUI 工作流 JSON。')
  busy.value = true
  try {
    const prepared = await api.previewCreationWorkflow(graph)
    importNotes.value = prepared.notes || []
    importSources.value = prepared.source_inputs || []
    Object.assign(imported, { label: name.replace(/\.json$/i, ''), media_type: prepared.media_type, prompt: prepared.prompt, bindings: Object.fromEntries(fields.map(([key]) => [key, (prepared.bindings[key] || []).map(item => JSON.stringify([item.node_id, item.input]))])) })
    importing.value = true; error.value = ''
    await nextTick()
    importForm.value?.scrollIntoView({ block: 'nearest' })
  } finally { busy.value = false }
}
async function saveEnvironment() {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api.saveCreationEnvironment({ ...environment })
    Object.assign(environment, result.environment); Object.assign(savedEnvironment, environment)
    notice.value = '连接配置已保存。'
    data.value = await api.loadCreationBootstrap(); preflight.value = null
  } catch (cause) { message(cause) }
  finally { busy.value = false }
}
async function saveDefaults() {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    Object.assign(defaults, (await api.saveWorkflowDefaults({ ...defaults })).defaults)
    Object.assign(savedDefaults, defaults); notice.value = '默认工作流已保存。'
  } catch (cause) { message(cause) }
  finally { busy.value = false }
}
async function start() {
  busy.value = true; error.value = ''
  try {
    const result = await api.startComfyUi()
    notice.value = result.message
    data.value = await api.loadCreationBootstrap()
    if (result.starting) pollConnection()
  }
  catch (cause) { message(cause) }
  finally { busy.value = false }
}
function pollConnection(attempt = 0) {
  clearTimeout(connectionTimer)
  if (disposed) return
  connectionTimer = setTimeout(async () => {
    try {
      const result = await api.loadCreationBootstrap()
      if (disposed) return
      if (data.value) data.value.comfyui = result.comfyui
      if (result.comfyui?.reachable) { notice.value = 'ComfyUI 已连接。'; return }
      if (attempt >= 59) { error.value = 'ComfyUI 尚未连接，请检查原启动器日志后重试。'; return }
      pollConnection(attempt + 1)
    } catch (cause) { if (!disposed) message(cause) }
  }, 3000)
}
async function check() {
  busy.value = true; error.value = ''
  try { preflight.value = await api.loadCreationPreflight(); notice.value = preflight.value.ok ? '工作流检查通过。' : '检查完成，部分依赖需要处理。' }
  catch (cause) { message(cause) }
  finally { busy.value = false }
}
async function readFile(event) {
  const file = event.target.files?.[0]; event.target.value = ''
  if (!file) return
  try {
    if (file.size > 2000000) throw new Error('工作流不能超过 2 MB。')
    const graph = JSON.parse((await file.text()).replace(/^\uFEFF/, ''))
    await prepareWorkflow(graph, file.name)
  } catch (cause) { message(cause) }
  finally { busy.value = false }
}
async function importWorkflow() {
  busy.value = true; error.value = ''
  try {
    const bindings = Object.fromEntries(Object.entries(imported.bindings).filter(([, values]) => values.length).map(([key, values]) => [key, values.map(value => { const [node_id, input] = JSON.parse(value); return { node_id, input } })]))
    const result = await api.importCreationWorkflow({ label: imported.label.trim(), media_type: imported.media_type, prompt: imported.prompt, bindings })
    data.value.workflows.push(result.workflow); importing.value = false; notice.value = '工作流已导入。'
  } catch (cause) { message(cause) }
  finally { busy.value = false }
}
async function remove(workflow) {
  busy.value = true; error.value = ''
  try { await api.removeCreationWorkflow(workflow.id); data.value.workflows = workflows.value.filter(item => item.id !== workflow.id); notice.value = '工作流已移除，历史任务保留。' }
  catch (cause) { message(cause) }
  finally { busy.value = false }
}
onMounted(() => { void load(); void discover() })
onUnmounted(() => { disposed = true; clearTimeout(connectionTimer) })
</script>

<template>
  <div class="agent-creation-settings">
    <p v-if="error" role="alert" class="agent-settings-message error">{{ error }}</p>
    <p v-else-if="notice" role="status" class="agent-settings-message">{{ notice }}</p>
    <div v-if="!ready" class="agent-settings-actions"><span>{{ busy ? '正在读取配置' : '配置读取失败' }}</span><button type="button" title="重新读取配置" :disabled="busy" @click="load"><RefreshCw :size="16" /></button></div>
    <section v-show="section === 'creation'" class="agent-settings-section">
      <header class="agent-section-heading"><h3>ComfyUI</h3><span>{{ data?.comfyui?.reachable ? '服务已连接' : '服务未连接' }}</span></header>
      <form id="agent-environment-form" @submit.prevent="saveEnvironment">
        <fieldset :disabled="!ready || busy" class="agent-settings-group">
          <div class="agent-setting-row"><label for="agent-comfy-path">本机目录</label><div class="local-path-control"><input id="agent-comfy-path" v-model="environment.comfyui_root" type="text" required placeholder="选择 ComfyUI 程序目录" /><button type="button" @click="chooseInstallation"><FolderOpen :size="15" />选择文件夹</button></div></div>
          <label class="agent-setting-row"><span>服务地址</span><input v-model="environment.comfyui_base_url" type="url" required /></label>
        </fieldset>
      </form>
      <div class="agent-settings-actions"><button class="primary" type="button" :disabled="busy || !ready || environmentDirty || data?.comfyui?.reachable" @click="start"><Play :size="15" />启动并连接 ComfyUI</button><button type="button" title="刷新连接状态" :disabled="busy || environmentDirty" @click="load"><RefreshCw :size="15" /></button></div>
      <p v-if="data?.comfyui?.error" class="agent-setting-note">{{ data.comfyui.error }}</p>
      <header class="agent-section-heading"><h3>本机安装</h3><button type="button" :disabled="scanning" @click="discover"><Search :size="15" />{{ scanning ? '正在检测' : '自动检测' }}</button></header>
      <p v-if="scanned && !candidates.length" class="agent-setting-note">常用目录中未发现 ComfyUI。点击上方“选择文件夹”定位其他安装目录。</p>
      <article v-for="item in candidates" :key="item.root" class="agent-discovered-install"><CheckCircle2 :size="18" /><div><strong>已安装 ComfyUI</strong><small>{{ item.root }}</small><small>{{ item.launchable ? '配套 Python 已找到' : '未找到配套 Python，需使用原启动器' }}</small></div><button type="button" :disabled="busy || !ready || environment.comfyui_root === item.root" @click="environment.comfyui_root = item.root">使用此目录</button></article>
      <footer class="agent-form-footer"><button type="button" :disabled="busy || !environmentDirty" @click="Object.assign(environment, savedEnvironment)"><X :size="15" />取消</button><button type="submit" form="agent-environment-form" class="primary" :disabled="busy || !ready || !environmentDirty"><Save :size="15" />保存设置</button></footer>
    </section>
    <section v-show="section === 'workflows'" class="agent-settings-section">
      <header class="agent-section-heading"><h3>默认工作流</h3></header>
      <div class="agent-settings-group">
        <label class="agent-setting-row"><span>图片生成</span><select v-model="defaults.image_workflow_id" aria-label="图片生成" :disabled="!ready || busy"><option v-for="workflow in workflows.filter(item => item.media_type === 'image')" :key="workflow.id" :value="workflow.id">{{ workflow.label }}</option></select></label>
        <label class="agent-setting-row"><span>视频生成</span><select v-model="defaults.video_workflow_id" aria-label="视频生成" :disabled="!ready || busy"><option v-for="workflow in workflows.filter(item => item.media_type === 'video')" :key="workflow.id" :value="workflow.id">{{ workflow.label }}</option></select></label>
      </div>
      <header class="agent-section-heading"><h3>本机工作流</h3><div class="agent-inline-actions"><button type="button" :disabled="busy || !ready" @click="openWorkflowPicker"><FolderOpen :size="15" />选择文件导入</button><button type="button" :disabled="workflowScanning || !ready" @click="findWorkflows"><Search :size="15" />{{ workflowScanning ? '正在搜索' : '搜索本机工作流' }}</button></div></header>
      <form class="local-workflow-search" @submit.prevent="findWorkflows"><input v-model="workflowQuery" aria-label="搜索工作流名称" placeholder="按名称搜索，例如 人物、视频、BiRefNet" /><button type="submit" :disabled="workflowScanning">搜索</button><button type="button" @click="addSearchFolder"><FolderOpen :size="15" />选择并授权搜索文件夹</button></form>
      <p class="agent-setting-note">搜索 ComfyUI 的工作流、节点示例及已授权资料目录。也可以直接对澪说“找到人物抠图工作流并导入”。</p>
      <p v-if="workflowScanned && !localWorkflows.length" class="agent-setting-note">未找到匹配的工作流，可以选择其他文件夹或直接导入文件。</p>
      <p v-if="workflowTruncated" class="agent-setting-note">当前只显示部分结果，请缩小文件夹范围或按名称搜索。</p>
      <div class="local-workflow-results"><article v-for="item in localWorkflows" :key="item.path" class="agent-workflow-row local-found-workflow"><FolderOpen :size="18" /><div><strong>{{ item.name }}</strong><small class="local-workflow-path">{{ item.path }}</small></div><button type="button" :disabled="busy" @click="openFoundWorkflow(item)">打开并添加</button></article></div>
      <header class="agent-section-heading"><h3>已登记工作流</h3><div class="agent-inline-actions"><button type="button" :disabled="busy || !ready" @click="check"><RefreshCw :size="15" />检查依赖</button></div></header>
      <input ref="fileInput" type="file" accept=".json,application/json" hidden @change="readFile" />
      <form v-if="importing" ref="importForm" class="agent-workflow-import" @submit.prevent="importWorkflow">
        <header class="agent-section-heading"><h3>导入工作流</h3><button type="button" title="取消导入" :disabled="busy" @click="importing = false"><X :size="16" /></button></header>
        <label class="agent-setting-row"><span>名称</span><input v-model="imported.label" type="text" required maxlength="100" /></label>
        <label class="agent-setting-row"><span>输出类型</span><select v-model="imported.media_type"><option value="image">图片</option><option value="video">视频</option></select></label>
        <p class="agent-setting-note">已识别可调参数，可以直接保存。展开高级设置可调整绑定；未绑定的输入保留文件原值。</p>
        <p v-for="note in importNotes" :key="note" class="agent-setting-note">{{ note }}</p>
        <label v-for="source in importSources" :key="`${source.node_id}.${source.input}`" class="agent-setting-row"><span>{{ source.label }}</span><select v-model="imported.prompt[source.node_id].inputs[source.input]" required><option v-if="!source.options.includes(imported.prompt[source.node_id].inputs[source.input])" :value="imported.prompt[source.node_id].inputs[source.input]" disabled>原文件不可用，请选择视频</option><option v-for="value in source.options" :key="value" :value="value">{{ value }}</option></select></label>
        <details><summary>高级参数绑定</summary><div class="agent-binding-grid"><label v-for="[key, label] in fields" :key="key"><span>{{ label }}</span><select v-model="imported.bindings[key]" multiple :aria-label="`${label}节点绑定`"><option v-for="target in choices(key)" :key="target.id" :value="target.id">{{ target.label }}</option></select></label></div></details>
        <div class="agent-settings-actions"><button type="submit" class="primary" :disabled="busy"><Save :size="15" />保存工作流</button></div>
      </form>
      <article v-for="workflow in workflows" :key="workflow.id" class="agent-workflow-row"><component :is="workflow.media_type === 'video' ? Film : ImagePlus" :size="20" /><div><strong>{{ workflow.label }}</strong><small>{{ workflow.custom ? '自定义' : workflow.filename }}</small><small>{{ preflight?.workflows?.find(item => item.id === workflow.id)?.status === 'ready' ? '检查通过' : workflow.available ? '已登记' : '文件不可用' }}</small><p v-for="issue in preflight?.workflows?.find(item => item.id === workflow.id)?.errors || []" :key="issue">{{ issue }}</p><p v-for="node in preflight?.workflows?.find(item => item.id === workflow.id)?.nodes?.missing || []" :key="node">缺少节点：{{ node }}</p></div><button v-if="workflow.custom" type="button" :title="savedDefaults.image_workflow_id === workflow.id || savedDefaults.video_workflow_id === workflow.id ? '请先切换默认工作流' : `移除 ${workflow.label}`" :disabled="busy || savedDefaults.image_workflow_id === workflow.id || savedDefaults.video_workflow_id === workflow.id" @click="remove(workflow)"><Trash2 :size="16" /></button></article>
      <footer class="agent-form-footer"><button type="button" :disabled="busy || !defaultsDirty" @click="Object.assign(defaults, savedDefaults)"><X :size="15" />取消</button><button type="button" class="primary" :disabled="busy || !ready || !defaultsDirty" @click="saveDefaults"><Save :size="15" />保存设置</button></footer>
    </section>
  </div>
</template>

<style scoped>
.local-workflow-results{max-height:360px;overflow:auto;scrollbar-width:thin}
.local-path-control{display:flex;gap:8px;align-items:center;min-width:0;flex-wrap:wrap;width: min(480px,65%)}.local-path-control input{flex:1;min-width:120px!important;width:auto!important}.local-path-control button,.local-workflow-search button,.local-found-workflow>button{display:inline-flex;align-items:center;justify-content:center;gap:5px;white-space:nowrap;border:1px solid #e4d8de;border-radius:6px;padding:8px 11px;background:var(--surface,#fff);color:#8b586d;font:inherit;font-size:12px;min-height:34px;cursor:pointer}.local-workflow-search{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;font-size:13px}.local-workflow-search input{flex:1;min-width:150px;border:1px solid #d1dce0;background:var(--surface,#fff);border-radius:6px;padding:8px 10px;font:inherit}.local-workflow-path{overflow-wrap:anywhere;white-space:normal!important}.agent-creation-settings .local-found-workflow{grid-template-columns:22px minmax(0,1fr) auto}.agent-creation-settings .local-found-workflow>button{width:auto;padding:8px 11px}.local-path-control button:disabled,.local-workflow-search button:disabled{opacity:.5;cursor:default}@media(max-width:640px){.local-path-control{width:100%}.local-workflow-search input{width:100%;flex-basis:100%}}
</style>
