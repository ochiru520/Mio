<script setup>
import { mergeLoadedItems } from '../scrollCollections.js'
import WorkspacePager from './WorkspacePager.vue'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Archive, RefreshCw } from '@lucide/vue'
import { apiRequest } from '../services/api.js'
const props = defineProps({ projectId: { type: String, default: '' }, mobile: Boolean })
const emit = defineEmits(['changed', 'expired', 'open-task'])
const artifacts = ref([]), offset = ref(0), hasMore = ref(false), busy = ref(false), notes = ref({})
const loading = ref(false)
const error = ref('')
let timer, version = 0, disposed = false
const quality = status => ({ not_reviewed: '尚未评价', user_approved: '已认可', user_rejected: '需改进' }[status] || '尚未评价')
const integrity = status => ({ verified: '文件完整', failed: '文件校验失败', unknown: '文件待核验' }[status] || '文件待核验')
async function api(url, method = 'GET', body) {
  try { return await apiRequest(url, { method, headers: props.mobile ? { 'X-Mio-Mobile': '1' } : {}, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }) }
  catch (cause) { if (cause.status === 401 && props.mobile) emit('expired'); throw cause }
}
function sizeLabel(value) { const bytes = Number(value || 0); return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(2)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB` }
async function load() {
  const requestVersion = ++version, projectId = props.projectId, pageOffset = offset.value
  const current = () => !disposed && requestVersion === version && projectId === props.projectId && pageOffset === offset.value
  loading.value = true
  try {
    const data = await api(`/api/artifacts?project_id=${encodeURIComponent(projectId)}&limit=30&offset=${pageOffset}`)
    if (!current()) return
    artifacts.value = mergeLoadedItems(artifacts.value,data.artifacts || [],pageOffset); hasMore.value = !!data.has_more; error.value = ''
    for (const item of artifacts.value) if (!(item.id in notes.value)) notes.value[item.id] = item.quality_note || ''
  }
  catch (cause) { if (current()) error.value = cause.message }
  finally { if (current()) loading.value = false }
}
async function changePage(direction) { if (busy.value || loading.value) return; offset.value = Math.max(0, offset.value + direction * 30); notes.value = {}; await load() }
async function mutate(item, operation, status) {
  if (busy.value) return
  ++version; loading.value = false; busy.value = true; error.value = ''
  const projectId = props.projectId
  try {
    const updated = await api(`/api/artifacts/${item.id}/${operation}`, 'POST', operation === 'review' ? { status, note: status === 'not_reviewed' ? '' : notes.value[item.id] || '' } : undefined)
    if (disposed || projectId !== props.projectId) return
    artifacts.value = artifacts.value.map(row => row.id === updated.id ? updated : row)
    notes.value[updated.id] = updated.quality_note || ''; emit('changed')
  } catch (cause) { if (!disposed && projectId === props.projectId) error.value = cause.message }
  finally { busy.value = false }
}
watch(() => props.projectId, () => { offset.value = 0; artifacts.value = []; notes.value = {}; hasMore.value = false; void load() })
onMounted(() => { void load(); timer = window.setInterval(() => { if (!document.hidden && !busy.value && !loading.value) void load() }, 6000) })
onBeforeUnmount(() => { disposed = true; ++version; window.clearInterval(timer) })
</script>

<template>
  <section class="artifact-library">
    <header><div><h3><Archive :size="17" />成果库</h3><small>归档文件与质量评价保存在这里，可回到来源任务继续修改。</small></div><button type="button" title="刷新成果库" :disabled="loading || busy" @click="load"><RefreshCw :class="{ spin: loading }" :size="15" /></button></header>
    <p v-if="error" class="artifact-error">{{ error }}</p><p v-else-if="!artifacts.length && !loading" class="artifact-empty">还没有归档成果</p>
    <div v-else class="artifact-list"><article v-for="item in artifacts" :key="item.id">
      <strong>{{ item.name }}</strong><small>{{ sizeLabel(item.size) }} · v{{ item.version }} · {{ quality(item.quality_status) }} · {{ integrity(item.integrity_status) }}</small>
      <div class="artifact-actions"><a v-if="item.integrity_status !== 'failed'" :href="item.url" :download="item.name">下载</a><button v-if="item.task_id" :disabled="busy" @click="emit('open-task', item.task_id)">查看来源任务</button><button :disabled="busy" @click="mutate(item, 'verify')">重新校验文件</button></div>
      <details><summary>评价与来源</summary><label>质量说明<textarea v-model="notes[item.id]" :disabled="busy" maxlength="2000" rows="2" :aria-label="`${item.name}质量说明`" /></label><div class="artifact-actions"><button :disabled="busy" @click="mutate(item, 'review', 'user_approved')">认可</button><button :disabled="busy" @click="mutate(item, 'review', 'user_rejected')">需改进</button><button :disabled="busy" @click="mutate(item, 'review', 'not_reviewed')">清除评价</button></div><small>归档：{{ item.created_at }}<template v-if="item.reviewed_at"> · 评价：{{ item.reviewed_at }}</template><template v-if="item.verified_at"> · 校验：{{ item.verified_at }}</template></small><small>来源任务：{{ item.task_id || '暂无可确认的记录' }} · 生成：{{ item.job_id || '无' }}</small><small>内容哈希：{{ item.sha256 }}</small><small v-if="item.metadata?.formulas">公式：{{ item.metadata.formulas }} 个，{{ item.metadata.formula_values_recalculated ? `已由 ${item.metadata.recalculation_engine} 重算` : '未完成公式重算，请在 Excel 中复核' }}</small></details>
    </article></div>
    <WorkspacePager label="成果" :has-more="hasMore" :busy="busy || loading" @page="changePage(1)" />
  </section>
</template>

<style scoped>
.artifact-library{padding:18px 0;border-top:1px solid var(--border)}.artifact-library>header{display:flex;justify-content:space-between;align-items:center}.artifact-library h3{display:flex;align-items:center;gap:7px;margin:0;font-size:calc(15px * var(--fs, 1))}.artifact-library header small{display:block;color:var(--text-muted);margin-top:4px}.artifact-library header button{display:flex;border:1px solid var(--border);background:var(--surface);border-radius:5px;padding:6px;cursor:pointer}.artifact-list{display:grid;gap:6px;margin-top:12px}.artifact-list article{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px;border-bottom:1px solid var(--border)}.artifact-list article div{min-width:0}.artifact-list strong,.artifact-list small{display:block;overflow-wrap:anywhere}.artifact-list small{color:var(--text-muted);margin-top:3px;font-size:calc(11px * var(--fs, 1))}.artifact-list a{color:var(--success);font-size:calc(12px * var(--fs, 1));white-space:nowrap}.artifact-error{color:var(--danger)}.artifact-empty{color:var(--text-muted);font-size:calc(12px * var(--fs, 1))}
</style>
<style scoped>
.artifact-library{color:inherit}.artifact-library>header{gap:12px}.artifact-library header small,.artifact-list small{color:var(--muted,var(--text-muted))}.artifact-list article{display:block;border:1px solid var(--line,var(--border));border-radius:8px;padding:14px}.artifact-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:10px}.artifact-pages{justify-content:flex-end}.artifact-library button{font:inherit;border:1px solid var(--line,var(--border));border-radius:6px;padding:6px 10px;background:var(--surface);color:inherit;cursor:pointer;min-height:36px}.artifact-library button:disabled{opacity:.5;cursor:default}.artifact-list details{margin-top:10px;overflow-wrap:anywhere}.artifact-list summary{cursor:pointer}.artifact-list label{display:grid;gap:5px;margin-top:10px}.artifact-list textarea{width:100%;box-sizing:border-box;resize:vertical;color:inherit;background:var(--surface);border:1px solid var(--line,var(--border));border-radius:6px;padding:8px;font:inherit}.artifact-list a{color:var(--accent,var(--success))}@media(max-width:640px){.artifact-list textarea{font-size:16px}.artifact-library button{min-height:40px}}
</style>
