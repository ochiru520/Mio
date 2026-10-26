<script setup>
import AgentBrowserPanel from './AgentBrowserPanel.vue'
import * as experienceApi from '../services/agentExperienceApi.js'
import * as executionApi from '../services/executionApi.js'

import * as artifactsApi from '../services/artifactsApi.js'

import * as tasksApi from '../services/tasksApi.js'

import { mergeLoadedItems } from '../scrollCollections.js'
import WorkspacePager from './WorkspacePager.vue'
import AgentDocumentPreview from './AgentDocumentPreview.vue'
import { computed, inject, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { FileText, X, RefreshCw, ArrowLeft } from '../icons/index.js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
const props = defineProps({ projectId: String, conversationId: String, artifact: Object, reviewTarget:Object, browserTarget:Object, tasks: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'open-source','select-element','browser-activated'])
const tab = ref('files'), selected = ref(null), preview = ref(null), items = ref([]), codeFiles = ref([]), history = ref({ changes: [], jobs: [], observations: [] }), config = ref(null), error = ref(''), busy = ref(false), mediaError = ref('')
const terminalDraft=ref(''),terminalBusy=ref(false),reviewPath=ref(''),viewedPaths=ref(new Set())
const reviewState=new Map()
const terminalStateLabels={running:'运行中',waiting_input:'等待输入',idle_inferred:'暂时没有输出（推测空闲，仍在运行）',timeout:'已超时',exited:'已退出'}
async function sendTerminal(){if(!selected.value?.task_id || terminalBusy.value)return;terminalBusy.value=true;try{await experienceApi.terminalInput(selected.value.task_id,selected.value.id,terminalDraft.value+'\n');terminalDraft.value='';await show(selected.value,'terminal')}catch(cause){error.value=cause.message}finally{terminalBusy.value=false}}
async function closeTerminalInput(){if(!selected.value?.task_id || terminalBusy.value)return;terminalBusy.value=true;try{await experienceApi.terminalCloseInput(selected.value.task_id,selected.value.id);await show(selected.value,'terminal')}catch(cause){error.value=cause.message}finally{terminalBusy.value=false}}
function selectReview(path){reviewPath.value=path;viewedPaths.value=new Set([...viewedPaths.value,path]);if(selected.value)reviewState.set(selected.value.id,{path,viewed:[...viewedPaths.value]})}
const offset = ref(0), hasMore = ref(false), nextOffset = ref(0), sourceCursors = ref([0])
const currentTask = computed(() => props.tasks.find(task => task.conversation_id === props.conversationId))
const markdownPreview = computed(() => selected.value?.name?.toLowerCase().endsWith('.md') && preview.value?.kind === 'text')
const documentHtml = computed(() => DOMPurify.sanitize(marked.parse(preview.value?.content || '')))
let version = 0, disposed = false, timer
function query(extra = {}) { return new URLSearchParams({ conversation_id: props.conversationId || '', ...extra }) }
async function load(autoOpen = false) {
  const n = ++version, conversation = props.conversationId, project = props.projectId, category = tab.value
  busy.value = true
  const valid = () => !disposed && n === version && conversation === props.conversationId && project === props.projectId && category === tab.value
  try {
    let data = { artifacts: [], has_more: false }, files = { files: [] }, events = { changes: [], jobs: [], observations: [] }, result = null
    if (category === 'sources') {
      data = currentTask.value ? await tasksApi.loadWorkCitationsByAfterIdLimit50(currentTask.value.id, sourceCursors.value.at(-1), {}) : { sources: [] }
      if (!valid()) return
      items.value = mergeLoadedItems(items.value,data.sources || [],sourceCursors.value.length-1); hasMore.value = Boolean(data.has_more); nextOffset.value = data.next_id || 0
    } else {
      if (category === 'browser') {if(valid())busy.value=false;return}
      if (conversation) data = await artifactsApi.loadArtifacts(query({limit:'50',offset:String(offset.value)}), {})
      if (project && conversation) {
        result = await executionApi.loadProject(project, {})
        if (result.code.read_enabled && category === 'code') files = await executionApi.loadProjectFiles(project, query({offset:String(offset.value)}), {})
        events = await executionApi.loadProjectHistory(project, query(), {})
      }
      if (!valid()) return
      items.value = mergeLoadedItems(items.value,data.artifacts || [],offset.value); config.value = result; codeFiles.value = mergeLoadedItems(codeFiles.value,files.files || [],offset.value,'path'); history.value = events
      hasMore.value = Boolean(category === 'code' ? files.has_more : category === 'files' && data.has_more)
      nextOffset.value = category === 'code' ? files.next_offset || 0 : data.next_offset || 0
    }
    error.value = ''
    if (autoOpen && category === 'files' && items.value.length && !selected.value) await show(items.value[0])
  } catch (cause) { if (valid()) error.value = cause.message }
  finally { if (n === version) busy.value = false }
}
async function show(item, kind = 'artifact', pageOffset = 0, append = false) {
  const n = ++version, project = props.projectId, conversation = props.conversationId
  const previous=append ? preview.value : null
  selected.value = { ...item, kind, pageOffset:0 }; if (!append) preview.value = null; busy.value = true; error.value = ''; mediaError.value = ''
  try {
    if(project && !config.value){const [settings,events]=await Promise.all([executionApi.loadProject(project),executionApi.loadProjectHistory(project,query())]);if(!disposed && n===version){config.value=settings;history.value=events}}
    let result
    if (kind === 'artifact') result = await artifactsApi.loadPreview(item.id, {})
    else if (kind === 'code') result = { kind, ...await executionApi.loadProjectFile(project, query({ path: item.path, offset:String(pageOffset) }), {}) }
    else if (kind === 'diff') {
      const changes=await Promise.all((item.change_ids?.length ? item.change_ids : [item.id]).map(id=>executionApi.loadProjectChange(project,id,query())))
      const files=new Map();for(const change of changes)for(const file of change.files || []){const previous=files.get(file.path);files.set(file.path,{...file,diff:previous ? previous.diff+'\n'+file.diff : file.diff})}
      result={kind,files:[...files.values()]}
    }
    else if (kind === 'terminal') result = { kind, ...await executionApi.loadProjectJob(project, item.id, query({ task_id: item.task_id, offset:String(pageOffset) }), {}) }
    else if (kind === 'desktop') result = { kind: 'image', url: `/api/workspace/execution/desktop/observations/${item.id}/image`, name: '窗口观察' }
    else if (kind === 'source') result = { kind: 'source', ...item }
    if (!disposed && n === version && project === props.projectId && conversation === props.conversationId) {
      if (previous && kind==='code') {
        if (previous.sha256!==result.sha256) throw new Error('文件已改变，请刷新后继续读取。')
        result.content=previous.content+'\n'+result.content
        result.lines=[...(previous.lines || []),...(result.lines || [])]
      }
      if (previous && kind==='terminal') {
        result.stdout=(previous.stdout || '')+(result.stdout || '')
        result.stderr=(previous.stderr || '')+(result.stderr || '')
      }
      preview.value = result
      if(kind==='diff'){const saved=reviewState.get(item.id);viewedPaths.value=new Set(saved?.viewed || []);selectReview(item.path && result.files?.some(file=>file.path===item.path) ? item.path : saved?.path || result.files?.[0]?.path || '')}
    }
  } catch (cause) { if (n === version) error.value = cause.message }
  finally { if (n === version) busy.value = false }
}
async function changeTab(value) { ++version; selected.value = null; preview.value = null; tab.value = value; items.value=[]; codeFiles.value=[]; offset.value = 0; sourceCursors.value = [0]; await load() }
async function listPage(direction) {
  if (tab.value === 'sources') { if (direction > 0) sourceCursors.value.push(nextOffset.value); else sourceCursors.value.pop() }
  else offset.value = Math.max(0, offset.value + direction * (tab.value === 'code' ? 100 : 50))
  await load()
}
async function stopCommand() { try { await executionApi.stopProjectJob(props.projectId, selected.value.id, query({ task_id: selected.value.task_id }), {}); await show(selected.value, 'terminal') } catch (cause) { error.value = cause.message } }
watch(() => [props.projectId, props.conversationId], () => {
  ++version; selected.value = null; preview.value = null; config.value = null; codeFiles.value = []; items.value = []; history.value = {changes:[],jobs:[],observations:[]}; tab.value = 'files'; offset.value = 0; sourceCursors.value = [0]
  if (props.reviewTarget) {tab.value='code';void show(props.reviewTarget,'diff')} else if (props.artifact) void show(props.artifact); else void load(true)
}, { immediate: true })
watch(()=>props.reviewTarget,item=>{if(item){tab.value='code';void show(item,'diff')}})
watch(()=>props.browserTarget,item=>{if(item && item.task_id===currentTask.value?.id){tab.value='browser';selected.value=null;preview.value=null}},{immediate:true})
watch(() => props.artifact, item => { if (item) { tab.value = 'files'; void show(item) } else { selected.value = null; preview.value = null; void load() } })
watch(() => currentTask.value?.id, () => { if (tab.value === 'sources' && !selected.value) { sourceCursors.value = [0]; void load() } })
timer = window.setInterval(() => { if (!document.hidden && !busy.value) { if(!selected.value && tab.value!=='browser')void load(); else if(selected.value?.kind==='terminal' && preview.value?.status==='running' && !preview.value?.has_more)void show(selected.value,'terminal') } }, 6000)
onBeforeUnmount(() => { disposed = true; ++version; clearInterval(timer) })
</script>
<template>
  <section class="agent-file-preview"><header><span><FileText :size="16" />{{ selected?.name || selected?.path || '文件与来源' }}</span><div><button type="button" aria-label="刷新文件" @click="selected ? show(selected, selected.kind, selected.pageOffset) : load()"><RefreshCw :size="14" /></button><button type="button" aria-label="收起文件预览" @click="emit('close')"><X :size="15" /></button></div></header>
    <nav class="agent-preview-tabs" aria-label="预览分类"><button type="button" :class="{ selected: tab === 'files' }" @click="changeTab('files')">文件</button><button type="button" :class="{ selected: tab === 'sources' }" @click="changeTab('sources')">来源</button><button v-if="config?.code.read_enabled" type="button" :class="{ selected: tab === 'code' }" @click="changeTab('code')">代码</button><button v-if="config?.code.execute_enabled || history.jobs.length" type="button" :class="{ selected: tab === 'terminal' }" @click="changeTab('terminal')">终端</button><button v-if="config?.desktop.enabled || history.observations.length" type="button" :class="{ selected: tab === 'desktop' }" @click="changeTab('desktop')">电脑</button><button type="button" :class="{ selected: tab === 'browser' }" @click="changeTab('browser')">浏览器</button></nav>
    <div class="agent-preview-scroll"><p v-if="error" role="alert">{{ error }}</p><p v-if="busy" class="agent-muted">正在读取…</p>
      <AgentBrowserPanel v-if="tab==='browser'" :task-id="currentTask?.id" :conversation-id="conversationId" :target="browserTarget" @activated="emit('browser-activated')" @select="emit('select-element',$event)" />
      <template v-else-if="selected"><button type="button" class="agent-preview-back" @click="selected = null; preview = null; changeTab(tab)"><ArrowLeft :size="14" />文件列表</button><details v-if="selected.kind === 'artifact'"><summary>文件信息</summary><p>{{ selected.mime_type }} · v{{ selected.version }} · {{ selected.size }} 字节</p><p>{{ selected.sha256 }}</p><a v-if="preview && !error && selected.integrity_status !== 'failed'" :href="selected.url" :download="selected.name">下载原文件</a><button type="button" @click="emit('open-source', selected)">来源对话</button></details>
        <template v-if="preview"><AgentDocumentPreview v-if="preview.kind === 'document'" :preview="preview" /><div v-else-if="markdownPreview" class="agent-document-preview markdown-body" v-html="documentHtml" /><pre v-else-if="['text','code'].includes(preview.kind)">{{ preview.kind === 'code' ? (preview.lines || []).map(line => `${line.number}  ${line.text}`).join('\n') : preview.content }}</pre><WorkspacePager v-if="['code','terminal'].includes(preview.kind)" label="内容" :has-more="preview.has_more && !error" :busy="busy" @page="show(selected,selected.kind,preview.next_offset,true)" /><p v-if="preview.kind !== 'document' && (preview.truncated || preview.has_more)" class="agent-muted">当前展示片段，完整文件可下载或由工具分页读取。</p><img v-if="preview.kind === 'image'" class="agent-media-preview" :src="preview.url" :alt="preview.name" @error="mediaError = '图片暂时无法显示，请刷新文件后重试。'" /><p v-if="mediaError" role="alert">{{ mediaError }}</p><video v-if="preview.kind === 'video'" :src="preview.url" controls /><audio v-if="preview.kind === 'audio'" :src="preview.url" controls /><iframe v-if="preview.kind === 'pdf'" :src="preview.url" title="PDF 预览" sandbox="" /><div v-if="preview.kind === 'download'" class="agent-preview-unsupported"><p>{{ preview.notice || '此文件格式暂不支持在侧栏浏览，可以下载原文件。' }}</p><a :href="preview.url" :download="preview.name">下载原文件</a></div><template v-if="preview.kind === 'source'"><h3>{{ preview.name }}</h3><p>{{ preview.status }} · {{ preview.reason }}</p><p>第 {{ preview.page || '—' }} 页 · 第 {{ preview.line || '—' }} 行</p><small>{{ preview.sha256 }}</small><a v-if="preview.url && preview.status === 'current'" :href="preview.url" target="_blank" rel="noopener">打开已核对来源</a></template><template v-if="preview.kind === 'diff'"><nav class="review-file-list" aria-label="改动文件"><button v-for="file in preview.files" :key="file.path" type="button" :aria-current="reviewPath===file.path ? 'page' : undefined" @click="selectReview(file.path)">{{ file.path }}{{ viewedPaths.has(file.path) ? ' · 已查看' : '' }}</button></nav><div v-for="file in preview.files.filter(file=>file.path===reviewPath)" :key="file.path"><h3>{{ file.path }}</h3><pre>{{ file.diff }}</pre></div></template><template v-if="preview.kind === 'terminal'"><p>{{ preview.command }}</p><p v-if="preview.evidence?.files_unchanged === false" role="alert">验证后文件或授权已改变，当前源码须重新验证。</p><small>{{ terminalStateLabels[preview.interaction_state] || preview.status }} · 退出码 {{ preview.exit_code ?? '尚未退出' }} · {{ Number(preview.duration || 0).toFixed(2) }} 秒</small><pre>{{ preview.stdout }}</pre><pre v-if="preview.stderr">{{ preview.stderr }}</pre><p v-if="preview.error" role="alert">{{ preview.error }}</p><form v-if="preview.status === 'running' && preview.interactive" class="terminal-input" @submit.prevent="sendTerminal"><label>终端输入<textarea v-model="terminalDraft" rows="2" :disabled="terminalBusy" aria-label="终端标准输入" /></label><button type="submit" :disabled="terminalBusy">发送到终端</button><button type="button" :disabled="terminalBusy" @click="closeTerminalInput">结束输入</button></form><button v-if="preview.status === 'running'" type="button" @click="stopCommand">停止命令</button></template></template>
      </template>
      <template v-else><template v-if="tab === 'files'"><button v-for="item in items" :key="item.id" class="agent-preview-file" type="button" @click="show(item)"><FileText :size="15" /><span>{{ item.name }}<small>v{{ item.version }}</small></span></button><p v-if="!items.length && !busy" class="agent-muted">此对话还没有可预览的成果。</p></template><template v-if="tab === 'sources'"><button v-for="item in items" :key="item.id" class="agent-preview-file" type="button" @click="show(item, 'source')">{{ item.name || item.title || item.source_id || item.id }}</button><p v-if="!items.length" class="agent-muted">此对话还没有可核对的来源。</p></template><template v-if="tab === 'code'"><small>源码</small><button v-for="file in codeFiles" :key="file.path" class="agent-preview-file" type="button" @click="show(file, 'code')">{{ file.path }}</button><small>修改记录</small><button v-for="change in history.changes" :key="change.id" class="agent-preview-file" type="button" @click="show(change, 'diff')">{{ change.status }} · {{ change.created_at?.slice(0,16) }}</button></template><template v-if="tab === 'terminal'"><button v-for="job in history.jobs" :key="job.id" class="agent-preview-file" type="button" @click="show(job, 'terminal')"><span>{{ job.command }}<small>{{ job.status }} · {{ job.exit_code ?? '运行中' }}</small></span></button><p v-if="!history.jobs.length" class="agent-muted">此对话还没有运行命令。</p></template><template v-if="tab === 'desktop'"><button v-for="observation in history.observations" :key="observation.id" class="agent-preview-file" type="button" @click="show(observation, 'desktop')">窗口观察 · {{ observation.created_at?.slice(11,19) }}</button><p v-if="!history.observations.length" class="agent-muted">此对话还没有电脑观察。</p></template></template>
      <WorkspacePager v-if="!selected && ['files','code','sources'].includes(tab)" :has-more="hasMore" :busy="busy" @page="listPage(1)" />
    </div>
  </section>
</template>

<style scoped>
.review-file-list{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.review-file-list button{font:inherit;background:transparent;color:var(--text-muted);border:1px solid var(--border);border-radius:7px;padding:8px;cursor:pointer}.review-file-list button[aria-current=page]{color:var(--accent);background:var(--accent-soft)}.terminal-input{display:grid;gap:10px;margin:20px 0}.terminal-input label{display:grid;gap:6px}.terminal-input textarea{padding:10px;font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:7px}

.agent-media-preview { display: block; max-width: 100%; height: auto; margin: 18px auto; border-radius: 6px; }
.agent-preview-scroll > iframe { display: block; width: 100%; min-height: 65vh; border: 0; margin-top: 16px; background: var(--surface); }
.agent-preview-unsupported { margin-top: 20px; font-size: calc(13px * var(--fs, 1)); line-height: 1.8; color: var(--text-muted); }
</style>
