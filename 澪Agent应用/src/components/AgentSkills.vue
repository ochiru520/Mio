<script setup>
import SelectField from './SelectField.vue'
import * as workspaceApi from '../services/workspaceApi.js'

import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

const props = defineProps({ mobile: Boolean })
const emit = defineEmits(['expired'])
const skills = ref([]), busy = ref(false), error = ref(''), notice = ref(''), editing = ref(false)
const query = ref(''), enabledFilter = ref('all'), sourceFilter = ref('all')
const selected = ref(null), sourcePath = ref(''), historyRevision = ref(0), history = ref(null)
const draft = reactive({ name: '', description: '', content: '', enabled: true })
let disposed = false
const statusLabel = value => ({ local: '本地编辑', current: '来源有效', stale: '来源已变化', unavailable: '来源不可用' }[value] || '来源待核对')
const filteredSkills = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  return skills.value.filter(item => {
    if (needle && !`${item.name || ''} ${item.description || ''}`.toLocaleLowerCase().includes(needle)) return false
    if (enabledFilter.value === 'enabled' && !item.enabled) return false
    if (enabledFilter.value === 'disabled' && item.enabled) return false
    const status = item.source_status
    if (sourceFilter.value === 'available' && !['local', 'current'].includes(status)) return false
    if (sourceFilter.value === 'changed' && status !== 'stale') return false
    if (sourceFilter.value === 'unavailable' && status !== 'unavailable') return false
    if (sourceFilter.value === 'unverified' && ['local', 'current', 'stale', 'unavailable'].includes(status)) return false
    return true
  })
})
async function withSession(operation) {
  try { return await operation }
  catch (cause) { if (cause.status === 401 && props.mobile) emit('expired'); throw cause }
}
async function refresh() { const response = await withSession(workspaceApi.loadSkills({ headers: props.mobile ? { 'X-Mio-Mobile': '1' } : {} })); if (!disposed) skills.value = response.skills || [] }
async function act(fn) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await fn() } catch (cause) { if (!disposed) error.value = cause.message }
  finally { if (!disposed) busy.value = false }
}
async function full(id, revision = 0) {
  let page = await withSession(workspaceApi.loadSkillByRevision(id, revision, { headers: props.mobile ? { 'X-Mio-Mobile': '1' } : {} }))
  const first = page; let content = page.content || ''
  while (page.has_more) {
    const offset = page.next_offset
    page = await withSession(workspaceApi.loadSkillByRevisionOffset(id, first.revision, offset, { headers: props.mobile ? { 'X-Mio-Mobile': '1' } : {} }))
    if (page.content_hash !== first.content_hash || page.current_revision !== first.current_revision || page.next_offset <= offset) throw new Error('读取期间技能已变化，请重新打开；原草稿保留。')
    content += page.content || ''
    if (content.length > 100000) throw new Error('技能正文超过可编辑容量。')
  }
  return { ...first, content }
}
async function edit(item) {
  await act(async () => {
    const value = await full(item.id)
    if (disposed) return
    selected.value = value; Object.assign(draft, { name: value.name, description: value.description, content: value.content, enabled: !!value.enabled })
    history.value = null; historyRevision.value = value.current_revision; editing.value = true
  })
}
function create() { selected.value = null; history.value = null; Object.assign(draft, { name: '', description: '', content: '', enabled: true }); editing.value = true; error.value = ''; notice.value = '' }
async function save() {
  const item = selected.value, payload = { ...draft, expected_revision: item?.current_revision || 0 }
  await act(async () => {
    await withSession(workspaceApi.saveSkill(item?.id || '', payload, { headers: props.mobile ? { 'X-Mio-Mobile': '1' } : {} }))
    if (disposed) return
    editing.value = false; selected.value = null; await refresh(); notice.value = '技能已保存，澪可按任务自主选用。'
  })
}
async function toggle(item) { await act(async () => { await withSession(workspaceApi.submitSkillEnabled(item.id, { enabled: !item.enabled, expected_revision: item.revision }, { headers: props.mobile ? { 'X-Mio-Mobile': '1' } : {} })); await refresh() }) }
async function importSource(item = null) {
  const path = item?.source_path || sourcePath.value
  await act(async () => { await withSession(workspaceApi.submitSkillsImport({ path, skill_id: item?.id || '', expected_revision: item?.revision || 0 }, { headers: props.mobile ? { 'X-Mio-Mobile': '1' } : {} })); await refresh(); if (!disposed) { sourcePath.value = ''; notice.value = '已导入授权文件，内容作为指导保存。' } })
}
async function readHistory() { const item = selected.value; await act(async () => { const value = await full(item.id, Number(historyRevision.value)); if (!disposed) history.value = value }) }
async function rollback(item) { await act(async () => { await withSession(workspaceApi.submitSkillRollback(item.id, { revision: item.revision - 1, expected_revision: item.revision }, { headers: props.mobile ? { 'X-Mio-Mobile': '1' } : {} })); await refresh(); notice.value = '已恢复上一版，原版本记录保留。' }) }
onMounted(() => act(refresh))
onUnmounted(() => { disposed = true })
</script>

<template>
  <section class="agent-skills" :aria-busy="busy">
    <header><div><h2>技能</h2><p>按目标选用的可复用指导，澪可以调整步骤。</p></div><div class="actions"><button :disabled="busy" @click="act(refresh)">刷新技能</button><button :disabled="busy || editing" @click="create">新建技能</button></div></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <details v-if="!mobile"><summary>高级：导入与筛选</summary><form class="import" @submit.prevent="importSource()"><label>授权文件路径<input v-model="sourcePath" placeholder="已授权目录中的 .md 或 .txt" :disabled="busy || editing" /></label><button :disabled="busy || editing || !sourcePath.trim()">导入技能</button></form>
    <div class="filters" role="search" aria-label="筛选技能">
      <label class="search">搜索技能<input v-model="query" type="search" placeholder="名称或简介" /></label>
      <label>启用状态<SelectField v-model="enabledFilter"><option value="all">全部</option><option value="enabled">已启用</option><option value="disabled">已停用</option></SelectField></label>
      <label>来源状态<SelectField v-model="sourceFilter"><option value="all">全部来源</option><option value="available">可用</option><option value="changed">来源已变化</option><option value="unavailable">来源不可用</option><option value="unverified">待核对</option></SelectField></label>
    </div>
    </details>
    <form v-if="editing" class="editor" @submit.prevent="save">
      <h3>{{ selected ? '编辑技能' : '新建技能' }}</h3>
      <p v-if="selected?.source_path">从 {{ selected.source_path }} 导入。保存修改后将成为独立的本地技能；重新导入仍须读取已授权文件。</p>
      <label>技能名称<input v-model="draft.name" required maxlength="100" :disabled="busy" /></label>
      <label>技能简介<input v-model="draft.description" maxlength="500" :disabled="busy" /></label>
      <label>技能正文<textarea v-model="draft.content" required maxlength="100000" rows="12" :disabled="busy" /></label>
      <label class="check"><input v-model="draft.enabled" type="checkbox" :disabled="busy" />启用此技能</label>
      <div class="actions"><button :disabled="busy">保存技能</button><button type="button" :disabled="busy" @click="editing = false; selected = null; history = null">关闭编辑</button></div>
      <div v-if="selected" class="history"><label>历史版本<input v-model="historyRevision" type="number" min="1" :max="selected.current_revision" :disabled="busy" /></label><button type="button" :disabled="busy" @click="readHistory">查看版本</button><pre v-if="history">版本 {{ history.revision }}（只读）
{{ history.content }}</pre></div>
    </form>
    <div v-if="filteredSkills.length" class="cards"><article v-for="item in filteredSkills" :key="item.id"><h3>{{ item.name }}</h3><p>{{ item.description }}</p><small>{{ item.enabled ? '已启用' : '已停用' }} · 版本 {{ item.revision }} · {{ statusLabel(item.source_status) }}</small><div class="actions"><button :disabled="busy || editing" :aria-label="`编辑 ${item.name}`" @click="edit(item)">编辑</button><button :disabled="busy || editing" :aria-label="`${item.enabled ? '停用' : '启用'} ${item.name}`" @click="toggle(item)">{{ item.enabled ? '停用' : '启用' }}</button><button :disabled="busy || editing || item.revision < 2" @click="rollback(item)">恢复上一版</button><button v-if="!mobile && item.source_path" :disabled="busy || editing" @click="importSource(item)">重新导入 {{ item.name }}</button></div></article></div>
    <div v-else class="empty" role="status"><strong>{{ skills.length ? '没有符合条件的技能' : '还没有技能' }}</strong><span>{{ skills.length ? '调整搜索词或筛选条件后再试。' : '新建技能，或导入已授权文件中的指导。' }}</span><button v-if="skills.length && (query || enabledFilter !== 'all' || sourceFilter !== 'all')" type="button" @click="query = ''; enabledFilter = 'all'; sourceFilter = 'all'">清除筛选</button></div>
    <p class="hint">技能不授予权限，也不自动执行脚本。导入来源变化或撤销授权后，澪会停止使用旧内容。</p>
  </section>
</template>

<style scoped>
.agent-skills { display:grid; gap:16px; min-width:0 } header,.actions,.import,.history { display:flex; align-items:center; gap:10px; flex-wrap:wrap } header { justify-content:space-between } h2,h3,p { margin:0 0 8px } label { display:grid; gap:6px; min-width:0 } .import label { flex:1; min-width:180px } .filters { display:grid; grid-template-columns:minmax(180px,1fr) repeat(2,minmax(150px,220px)); gap:10px; align-items:end } .filters .search { min-width:0 } .editor,article { padding:18px; border:1px solid var(--border); border-radius:16px; display:grid; gap:12px; min-width:0 } .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr)); gap:12px } input,select,textarea,button { font:inherit; box-sizing:border-box; color:inherit; border:1px solid var(--border); border-radius:8px; padding:9px 12px; background:var(--surface); max-width:100% } textarea,input,select { width:100%; min-width:0 } button { cursor:pointer } button:disabled { opacity:.55; cursor:default } .check { display:flex; align-items:center } .check input { width:auto } p,small,.hint { overflow-wrap:anywhere } small,.hint { opacity:.75 } .empty { display:grid; justify-items:start; gap:8px; padding:24px 0; color:inherit } .empty span { opacity:.75 } .error { color:var(--danger) } .history pre { width:100%; white-space:pre-wrap; overflow-wrap:anywhere; max-height:350px; overflow:auto }
@media (max-width:700px) { .filters { grid-template-columns:1fr 1fr } .filters .search { grid-column:1 / -1 } }
</style>
