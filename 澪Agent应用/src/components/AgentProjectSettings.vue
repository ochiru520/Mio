<script setup>
import SelectField from './SelectField.vue'
import * as workspaceApi from '../services/workspaceApi.js'

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { selectLocalPath } from '../services/localPicker.js'

const props = defineProps({ section: String, initialProjectId: String, projects: { type: Array, default: () => [] } })
const projectId = ref(props.initialProjectId || ''), project = ref(null)
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref('')
const hosts = ref(''), allowPublic = ref(false), loginUrl = ref('https://'), sourcePath = ref(''), query = ref(''), results = ref(null)
const documents = ref([]), more = ref(false), offset = ref(0)
const baseline = ref(''), loadedId = ref('')
const browserValue = () => JSON.stringify({ hosts: hosts.value.split(/\n/).map(value => value.trim()).filter(Boolean), allow_public: allowPublic.value })
const dirty = computed(() => loadedId.value && browserValue() !== baseline.value)
let version = 0, controller = null, disposed = false
function remember() {
  if (dirty.value) sessionStorage.setItem(`mio_browser_draft_${loadedId.value}`, JSON.stringify({ baseline: baseline.value, value: JSON.parse(browserValue()) }))
}
async function load() {
  remember(); controller?.abort(); controller = new AbortController()
  const n = ++version, id = projectId.value
  project.value = null; loadedId.value = ''; results.value = null; documents.value = []; more.value = false; offset.value = 0; notice.value = ''; error.value = ''
  if (!id) { loading.value = false; return }
  loading.value = true
  try {
    const data = await workspaceApi.loadProject(id, {signal: controller.signal})
    if (disposed || n !== version) return
    project.value = data.project; documents.value = data.documents || []; more.value = !!data.documents_has_more; offset.value = documents.value.length
    hosts.value = (data.browser?.hosts || []).join('\n'); allowPublic.value = !!data.browser?.allow_public; baseline.value = browserValue(); loadedId.value = id
    const previous = sessionStorage.getItem(`mio_browser_draft_${id}`)
    if (previous) {
      try { const saved = JSON.parse(previous); if (saved.baseline === baseline.value) { hosts.value = saved.value.hosts.join('\n'); allowPublic.value = saved.value.allow_public; notice.value = '已恢复未保存的浏览器范围。' } else sessionStorage.removeItem(`mio_browser_draft_${id}`) }
      catch { sessionStorage.removeItem(`mio_browser_draft_${id}`) }
    }
  } catch (cause) { if (!disposed && n === version) error.value = cause.message }
  finally { if (!disposed && n === version) loading.value = false }
}
async function act(operation, message = '') {
  if (busy.value || loading.value || loadedId.value !== projectId.value || !loadedId.value) return
  const n = version; busy.value = true; error.value = ''; notice.value = ''
  try { await operation(); if (!disposed && n === version) notice.value = message }
  catch (cause) { if (!disposed && n === version) error.value = cause.message }
  finally { if (!disposed) busy.value = false }
}
async function saveBrowser() {
  const id = loadedId.value, payload = JSON.parse(browserValue())
  await act(async () => {
    const data = await workspaceApi.saveProjectBrowser(id, payload, {})
    if (disposed || id !== loadedId.value) return
    hosts.value = data.hosts.join('\n'); allowPublic.value = !!data.allow_public; baseline.value = browserValue(); sessionStorage.removeItem(`mio_browser_draft_${id}`)
  }, '浏览器访问范围已保存。原浏览器会话已关闭，下次打开使用新范围。')
}
function resetBrowser() { const value = JSON.parse(baseline.value); hosts.value = value.hosts.join('\n'); allowPublic.value = value.allow_public; notice.value = ''; sessionStorage.removeItem(`mio_browser_draft_${loadedId.value}`) }
async function openBrowser() {
  if (dirty.value) { error.value = '请先保存浏览器访问范围，再打开专用浏览器。'; return }
  await act(() => workspaceApi.submitProjectBrowserLogin(loadedId.value, { url: loginUrl.value.trim() }, {timeoutMs: 60000}), '已打开项目专用浏览器，请在窗口中操作。')
}
async function reloadDocuments() {
  const id = loadedId.value, n = version
  const data = await workspaceApi.loadProjectHistoryDocumentsByLimit50(id, {})
  if (!disposed && n === version) { documents.value = data.documents || []; more.value = !!data.has_more; offset.value = data.next_offset }
}
async function addSource() {
  const id = loadedId.value, path = sourcePath.value.trim()
  await act(async () => { await workspaceApi.submitProjectKnowledge(id, { path }, {timeoutMs: 120000}); if (!disposed) sourcePath.value = ''; await reloadDocuments(); results.value = null }, '资料已建立索引。')
}
async function chooseSource() { try { const value = await selectLocalPath('file'); if (value && !disposed) sourcePath.value = value.path } catch (cause) { error.value = cause.message } }
async function upload(event) {
  const file = event.target.files?.[0], id = loadedId.value
  if (!file) return
  await act(async () => { await workspaceApi.uploadProjectFile(id, file, { encodeProjectId: true }); await reloadDocuments(); results.value = null }, '资料已保存并索引。')
  event.target.value = ''
}
async function search() {
  const id = loadedId.value, n = version
  await act(async () => { const data = await workspaceApi.loadProjectSearchByQ(id, query.value.trim(), {}); if (!disposed && n === version) results.value = data.results || [] })
}
async function reindex(item) { await act(async () => { await workspaceApi.submitKnowledgeReindex(item.id, {timeoutMs: 120000}); await reloadDocuments(); results.value = null }, '资料索引已更新。') }
async function loadMore() {
  const id = loadedId.value, n = version
  await act(async () => { const data = await workspaceApi.loadProjectHistoryDocumentsByLimit50Offset(id, offset.value, {}); if (!disposed && n === version) { documents.value.push(...(data.documents || []).filter(item => !documents.value.some(old => old.id === item.id))); more.value = !!data.has_more; offset.value = data.next_offset } })
}
watch(() => props.initialProjectId, value => { if (value) projectId.value = value })
watch(() => props.projects, items => { if (!projectId.value && items.length) projectId.value = items[0].id }, { immediate: true })
watch(projectId, load, { immediate: true })
onBeforeUnmount(() => { remember(); disposed = true; ++version; controller?.abort() })
</script>

<template>
  <section class="agent-detail-settings" :aria-busy="loading || busy">
    <header class="agent-setting-title"><h1>{{ section === 'browser' ? '浏览器' : '知识库' }}</h1><p>{{ section === 'browser' ? '按项目管理独立浏览器与网站访问范围' : '项目资料、索引状态和带来源的检索' }}</p></header>
    <label>当前项目<SelectField v-model="projectId" :disabled="busy"><option value="">选择项目</option><option v-for="item in projects" :key="item.id" :value="item.id">{{ item.name }}</option></SelectField></label>
    <p v-if="error" role="alert" class="error">{{ error }} <button v-if="!loadedId" :disabled="loading" @click="load">重新读取</button></p><p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="loading">正在读取项目设置…</p><p v-else-if="!projectId">先创建或选择项目。</p>
    <template v-else-if="loadedId === projectId">
      <template v-if="section === 'browser'">
        <section class="detail-card"><h2>访问范围</h2><p>域名逐行填写，如 github.com。未开启公开网站时，只能访问这里登记的网站；本机和内网地址始终不可访问。</p><label>允许访问的域名<textarea v-model="hosts" rows="5" :disabled="busy" placeholder="每行一个域名，不含协议、路径或通配符" /></label><label class="detail-row"><span>允许访问其他公开网站</span><input v-model="allowPublic" type="checkbox" :disabled="busy" /></label><footer><span>{{ dirty ? '有未保存修改' : '访问范围已保存' }}</span><button :disabled="busy || !dirty" @click="resetBrowser">撤销</button><button class="primary" :disabled="busy || !dirty" @click="saveBrowser">保存范围</button></footer></section>
        <section class="detail-card"><h2>项目专用浏览器</h2><p>使用独立的 Edge 会话，不读取日常浏览器的登录状态。项目需已有任务，提交和登录操作仍按任务确认。</p><label>打开网址<input v-model="loginUrl" :disabled="busy" type="url" /></label><button :disabled="busy || dirty || !loginUrl.trim()" @click="openBrowser">打开专用浏览器</button></section>
      </template>
      <template v-else>
        <section class="detail-card"><h2>添加项目资料</h2><p>支持 PDF、DOCX、XLSX、Markdown 与常用文本。选择电脑文件需有读取授权；上传文件会保存到 Mio 项目资料中。</p><label>本机文件路径<div class="detail-actions"><input v-model="sourcePath" :disabled="busy" placeholder="选择已经授权的资料文件" /><button :disabled="busy" @click="chooseSource">选择文件</button><button :disabled="busy || !sourcePath.trim()" @click="addSource">建立索引</button></div></label><label>上传资料<input type="file" accept=".pdf,.docx,.xlsx,.txt,.md,.csv,.tsv,.json,.yaml,.yml" :disabled="busy" @change="upload" /></label></section>
        <section class="detail-card"><header class="detail-actions"><h2>已索引资料</h2><button :disabled="busy" @click="act(reloadDocuments)">刷新资料</button></header><p v-if="!documents.length">还没有项目资料。</p><article v-for="item in documents" :key="item.id" class="detail-document"><strong>{{ item.name }}</strong><small>{{ ({ ready: '索引就绪', failed: '索引失败', stale: '来源已更新' })[item.status] || item.status }} · {{ item.indexed_at }}</small><p v-if="item.error" class="error">{{ item.error }}</p><div class="detail-actions"><a :href="`/api/workspace/knowledge/${encodeURIComponent(item.id)}/source`" target="_blank" rel="noopener">查看来源</a><button :disabled="busy" @click="reindex(item)">更新索引</button></div></article><button v-if="more" :disabled="busy" @click="loadMore">加载更多资料</button></section>
        <section class="detail-card"><h2>检索资料</h2><form class="detail-actions" @submit.prevent="search"><input v-model="query" aria-label="知识库搜索" :disabled="busy" maxlength="500" placeholder="输入要查找的内容" /><button :disabled="busy || !query.trim()">搜索</button></form><p v-if="results && !results.length">没有找到匹配资料。</p><article v-for="item in results" :key="item.id" class="detail-document"><a :href="item.url" target="_blank" rel="noopener">{{ item.citation }}</a><small v-if="item.stale" class="error">来源已变化，请更新索引后再引用。</small><p class="detail-excerpt">{{ item.content }}</p></article></section>
      </template>
    </template>
  </section>
</template>

<style scoped>
.agent-detail-settings{display:grid;gap:18px;max-width:960px;margin:auto;padding:30px 34px;box-sizing:border-box;min-width:0}.agent-setting-title{margin-bottom:0}.detail-card{display:grid;gap:14px;padding:18px;border:1px solid var(--border);border-radius:12px;min-width:0}h2{font-size:15px;font-weight:600;margin:0}p{font-size:12px;line-height:1.7;color:var(--text-muted);margin:0;overflow-wrap:anywhere}label{display:grid;gap:8px;font-size:12px;color:var(--text-muted);min-width:0}input,select,textarea,button{font:inherit;box-sizing:border-box;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:8px;padding:9px 11px;max-width:100%;min-width:0}input,select,textarea{width:100%}textarea{resize:vertical}button{cursor:pointer;font-size:12px}button:disabled{opacity:.5;cursor:default}.primary{background:var(--accent);color:var(--accent-contrast);border-color:var(--accent)}.detail-actions,footer,.detail-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.detail-actions>input{flex:1;min-width:150px}.detail-row{justify-content:space-between}.detail-row input{width:auto}footer>span{flex:1;font-size:12px;color:var(--text-muted)}.detail-document{display:grid;gap:8px;padding-top:14px;border-top:1px solid var(--border);min-width:0;overflow-wrap:anywhere}.detail-document strong{font-size:13px}.detail-document small{font-size:12px;color:var(--text-muted)}a{color:var(--accent);font-size:12px}.error{color:var(--danger)!important}.detail-excerpt{white-space:pre-wrap;max-height:240px;overflow:auto}@media(max-width:480px){.agent-detail-settings{padding:22px 16px}}
</style>
