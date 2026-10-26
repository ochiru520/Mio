<script setup>
import * as memoryApi from '../services/memoryApi.js'

import { computed, onBeforeUnmount, ref } from 'vue'
const data = ref(null), draft = ref(''), baseline = ref(''), loading = ref(false), saving = ref(false), error = ref(''), notice = ref('')
const dirty = computed(() => draft.value !== baseline.value)
const groups = computed(() => ['L0', 'L1', 'L2'].map(layer => ({ layer, name: { L0: '核心记忆', L1: '近期状态', L2: '长期经历' }[layer], items: (data.value?.structured || []).filter(item => item.layer === layer) })))
let disposed = false, controller
async function load() {
  if (loading.value || saving.value) return
  loading.value = true; error.value = ''; controller = new AbortController()
  try {
    const value = await memoryApi.loadMemoryResponse({signal: controller.signal})
    if (disposed) return
    const preserve = dirty.value; data.value = value; baseline.value = value.runtime_summary?.content || ''
    if (!preserve) {
      draft.value = baseline.value
      const old = sessionStorage.getItem('mio_memory_settings_draft')
      if (old) { try { const saved = JSON.parse(old); if (saved.baseline === baseline.value) { draft.value = saved.draft; notice.value = '已恢复未保存的运行摘要。' } else sessionStorage.removeItem('mio_memory_settings_draft') } catch { sessionStorage.removeItem('mio_memory_settings_draft') } }
    }
  } catch (cause) { if (!disposed) error.value = cause.message }
  finally { if (!disposed) loading.value = false }
}
async function save() {
  if (saving.value || !draft.value.trim() || !dirty.value) return
  saving.value = true; error.value = ''; notice.value = ''
  try { const result = await memoryApi.saveRuntimeSummaryByPayload({ content: draft.value }, {}); if (!disposed) { const content = typeof result?.content === 'string' ? result.content : draft.value; draft.value = content; baseline.value = content; sessionStorage.removeItem('mio_memory_settings_draft'); notice.value = '运行摘要已保存，聊天和 Agent 共用。' } }
  catch (cause) { if (!disposed) error.value = cause.message }
  finally { if (!disposed) saving.value = false }
}
function reset() { draft.value = baseline.value; notice.value = ''; sessionStorage.removeItem('mio_memory_settings_draft') }
onBeforeUnmount(() => { if (dirty.value) sessionStorage.setItem('mio_memory_settings_draft', JSON.stringify({ baseline: baseline.value, draft: draft.value })); disposed = true; controller?.abort() })
void load()
</script>
<template>
  <section class="mio-memory-settings"><header class="agent-setting-title"><h1>记忆</h1><p>聊天与 Agent 共用的本地记忆；项目资料在知识库中管理。</p></header><button class="memory-refresh" :disabled="loading || saving" @click="load">刷新记忆</button><p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p><p v-if="loading && !data">正在读取记忆…</p><template v-if="data"><section class="memory-card"><h2>运行摘要</h2><p>让澪了解近期状态与重要背景，可在这里校正。保存后会供后续对话与任务使用。</p><textarea v-model="draft" aria-label="运行摘要" rows="8" maxlength="12000" :disabled="saving || loading" /><footer><span>{{ dirty ? '有未保存修改' : '已保存' }}</span><button :disabled="saving || loading || !dirty" @click="reset">撤销</button><button class="primary" :disabled="saving || loading || !dirty || !draft.trim()" @click="save">保存摘要</button></footer></section><section v-for="group in groups" :key="group.layer" class="memory-card"><header><h2>{{ group.name }}</h2><small>{{ group.items.length }} 条</small></header><p v-if="!group.items.length">暂无此层记忆。</p><article v-for="item in group.items" :key="item.id"><p>{{ item.content }}</p><small>{{ item.updated_at || item.created_at }}</small></article></section><p>单条记忆的编辑、归档、恢复及来源核对在主应用“记忆”页面中完成。这里不改变聊天历史与项目权限。</p></template></section>
</template>
<style scoped>
.mio-memory-settings{display:grid;gap:18px;max-width:960px;margin:auto;padding:30px 34px;box-sizing:border-box;min-width:0}.agent-setting-title{margin-bottom:0}.memory-card{display:grid;gap:14px;padding:18px;border:1px solid var(--border);border-radius:12px;min-width:0}h2{font-size:15px;font-weight:600;margin:0}p,small,footer{font-size:12px;color:var(--text-muted);line-height:1.7;margin:0;overflow-wrap:anywhere}textarea,button{font:inherit;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:8px;padding:9px 11px;box-sizing:border-box}textarea{width:100%;min-width:0;resize:vertical;line-height:1.7}button{cursor:pointer;font-size:12px}button:disabled{opacity:.5;cursor:default}.memory-refresh{justify-self:start}.primary{background:var(--accent);color:var(--accent-contrast);border-color:var(--accent)}footer,header{display:flex;align-items:center;gap:10px}header{justify-content:space-between}footer span{flex:1}article{padding-top:14px;border-top:1px solid var(--border)}article small{display:block;margin-top:8px}.error{color:var(--danger)}@media(max-width:480px){.mio-memory-settings{padding:22px 16px}}
</style>
