<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { Check, RefreshCw } from '@lucide/vue'
import { apiRequest } from '../services/api.js'

const props = defineProps({ modes: { type: Array, default: () => ['chat', 'agent', 'record', 'proactive', 'memory', 'vision', 'translation', 'profile'] } })
const context = inject('mio-settings-page', null) || inject('mio-chat-page', null) || {}
const policies = ref({})
const usage = ref({})
const loading = ref(false)
const saving = ref('')
const notice = ref('')
const error = ref('')
const labels = { chat: '日常聊天', agent: 'Agent 任务', record: '日记与回顾', proactive: '主动联系', memory: '记忆整理', vision: '屏幕视觉', translation: '语音翻译', profile: '人格属性' }
const models = computed(() => context.modelOptions || context.modelGroups?.flatMap(group => group.models) || [])
function modeLabel(mode) { return labels[mode] || mode }
function policy(mode) { return policies.value[mode] || { mode, revision: 0, selection: 'follow_chat', model_id: '', reasoning_level: 'inherit', enabled: true } }
function modeUsage(mode) { return (usage.value.modes || []).find(item => item.mode === mode) || {} }
async function load() {
  loading.value = true; error.value = ''
  try {
    const [policyData, usageData] = await Promise.all([apiRequest('/api/runtime/policies'), apiRequest('/api/runtime/usage?days=30')])
    policies.value = Object.fromEntries((policyData.policies || []).map(item => [item.mode, item]))
    usage.value = usageData
  } catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}
async function save(mode) {
  const item = policy(mode); saving.value = mode; notice.value = ''; error.value = ''
  try {
    const result = await apiRequest(`/api/runtime/policies/${encodeURIComponent(mode)}`, { method: 'PUT', body: JSON.stringify({ revision: item.revision || 0, selection: item.selection, model_id: item.model_id || '', reasoning_level: item.reasoning_level || 'inherit', enabled: item.enabled !== false }) })
    policies.value[mode] = result; notice.value = `${modeLabel(mode)}策略已保存`
  } catch (cause) { error.value = cause.message }
  finally { saving.value = '' }
}
onMounted(load)
</script>

<template>
  <section class="model-strategy-panel">
    <header><div><h3>模型策略</h3><p>模型负责判断和执行；这里控制各类入口使用哪个模型，以及失败时的可见状态。</p></div><button type="button" title="刷新模型策略" :disabled="loading" @click="load"><RefreshCw :class="{ spin: loading }" :size="15" /></button></header>
    <p v-if="notice" class="strategy-notice"><Check :size="14" />{{ notice }}</p><p v-if="error" class="strategy-error">{{ error }}</p>
    <div class="strategy-list"><article v-for="mode in props.modes" :key="mode"><div class="strategy-main"><strong>{{ modeLabel(mode) }}</strong><small>近 30 天：{{ modeUsage(mode).calls || 0 }} 次 · {{ (Number(modeUsage(mode).prompt_tokens || 0) + Number(modeUsage(mode).completion_tokens || 0)).toLocaleString('zh-CN') }} Token<span v-if="modeUsage(mode).unknown_cost_calls"> · {{ modeUsage(mode).unknown_cost_calls }} 次费用待确认</span></small></div><div class="strategy-controls"><select v-model="policy(mode).selection" :aria-label="`${modeLabel(mode)}选择策略`"><option value="follow_chat">跟随聊天模型</option><option value="fixed">固定模型</option></select><select v-if="policy(mode).selection === 'fixed'" v-model="policy(mode).model_id" :aria-label="`${modeLabel(mode)}固定模型`"><option value="">请选择模型</option><option v-for="model in models" :key="model.id" :value="model.id">{{ model.display_name || model.model }}</option></select><button type="button" :disabled="saving === mode" :title="`保存${modeLabel(mode)}策略`" @click="save(mode)"><Check v-if="saving !== mode" :size="14" /><RefreshCw v-else class="spin" :size="14" /></button></div></article></div>
  </section>
</template>

<style scoped>
.model-strategy-panel{border-top:1px solid #d7deda;margin-top:20px;padding-top:16px}.model-strategy-panel>header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.model-strategy-panel h3{margin:0;font-size:15px}.model-strategy-panel p{margin:5px 0;color:#66756e;font-size:12px}.model-strategy-panel header button,.strategy-controls button{display:flex;align-items:center;justify-content:center;border:1px solid #cbd6d0;background:var(--surface,#fff);border-radius:5px;padding:6px;cursor:pointer}.strategy-list{display:grid;gap:6px;margin-top:12px}.strategy-list article{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid #e4e9e5}.strategy-main{min-width:130px}.strategy-main strong,.strategy-main small{display:block}.strategy-main small{color:#718078;font-size:11px;margin-top:3px}.strategy-controls{display:flex;gap:6px;align-items:center}.strategy-controls select{min-width:130px;padding:5px;border:1px solid #cbd6d0;border-radius:4px;background:var(--surface,#fff)}.strategy-notice{color:#356957;display:flex;gap:5px;align-items:center}.strategy-error{color:#a24237!important}
</style>
