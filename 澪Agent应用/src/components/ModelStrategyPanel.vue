<script setup>
import SelectField from './SelectField.vue'
import * as runtimeApi from '../services/runtimeApi.js'

import { computed, inject, onMounted, ref } from 'vue'
import { Check, RefreshCw } from '../icons/index.js'

const props = defineProps({ modes: { type: Array, default: () => ['chat', 'agent', 'record', 'proactive', 'memory', 'vision', 'translation', 'profile'] } })
const context = inject('mio-settings-page', null) || inject('mio-chat-page', null) || {}
const policies = ref({})
const usage = ref({})
const loading = ref(false)
const loaded = ref(false)
const saving = ref('')
const notice = ref('')
const error = ref('')
const labels = { chat: '日常聊天', agent: 'Agent 任务', record: '日记与回顾', proactive: '主动联系', memory: '记忆整理', vision: '屏幕视觉', translation: '语音翻译', profile: '人格属性' }
const models = computed(() => context.modelOptions || context.modelGroups?.flatMap(group => group.models) || [])
function modeLabel(mode) { return labels[mode] || mode }
function policy(mode) { return policies.value[mode] || { mode, revision: 0, selection: 'follow_chat', model_id: '', reasoning_level: 'inherit', enabled: true } }
function modeUsage(mode) { return (usage.value.modes || []).find(item => item.mode === mode) || {} }
function costLabel(mode) { return `已知费用 ¥${Number(modeUsage(mode).known_cost_yuan || 0).toFixed(4)}` }
async function load() {
  loading.value = true; loaded.value = false; error.value = ''; notice.value = ''
  try {
    const [policyData, usageData] = await Promise.all([runtimeApi.loadPolicies({}), runtimeApi.loadUsageByDays30({})])
    policies.value = Object.fromEntries((policyData.policies || []).map(item => [item.mode, item]))
    usage.value = usageData
    loaded.value = true
  } catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}
async function save(mode) {
  if (!loaded.value || loading.value || saving.value) return
  const item = policy(mode); saving.value = mode; notice.value = ''; error.value = ''
  try {
    const result = await runtimeApi.savePolicies(mode, { revision: item.revision || 0, selection: item.selection, model_id: item.model_id || '', reasoning_level: item.reasoning_level || 'inherit', enabled: item.enabled !== false }, {})
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
    <div class="strategy-list"><article v-for="mode in props.modes" :key="mode"><div class="strategy-main"><strong>{{ modeLabel(mode) }}</strong><small v-if="loaded && !loading">近 30 天：{{ modeUsage(mode).calls || 0 }} 次 · {{ (Number(modeUsage(mode).prompt_tokens || 0) + Number(modeUsage(mode).completion_tokens || 0)).toLocaleString('zh-CN') }} Token · {{ costLabel(mode) }}<span v-if="modeUsage(mode).unknown_cost_calls"> · {{ modeUsage(mode).unknown_cost_calls }} 次费用待确认</span></small><small v-else>{{ loading ? '正在读取用量…' : '用量尚未读取' }}</small></div><div class="strategy-controls"><SelectField :disabled="loading || !loaded || Boolean(saving)" v-model="policy(mode).selection" :aria-label="`${modeLabel(mode)}选择策略`"><option value="follow_chat">跟随聊天模型</option><option value="fixed">固定模型</option></SelectField><SelectField v-if="policy(mode).selection === 'fixed'" :disabled="loading || !loaded || Boolean(saving)" v-model="policy(mode).model_id" :aria-label="`${modeLabel(mode)}固定模型`"><option value="">请选择模型</option><option v-for="model in models" :key="model.id" :value="model.id">{{ model.display_name || model.model }}</option></SelectField><button type="button" :disabled="loading || !loaded || Boolean(saving)" :title="`保存${modeLabel(mode)}策略`" @click="save(mode)"><Check v-if="saving !== mode" :size="14" /><RefreshCw v-else class="spin" :size="14" /></button></div></article></div>
  </section>
</template>

<style scoped>
.model-strategy-panel{border-top:1px solid var(--border);margin-top:20px;padding-top:16px}.model-strategy-panel>header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.model-strategy-panel h3{margin:0;font-size:calc(15px * var(--fs, 1))}.model-strategy-panel p{margin:5px 0;color:var(--text-muted);font-size:calc(12px * var(--fs, 1))}.model-strategy-panel header button,.strategy-controls button{display:flex;align-items:center;justify-content:center;border:1px solid var(--border);background:var(--surface);border-radius:5px;padding:6px;cursor:pointer}.strategy-list{display:grid;gap:6px;margin-top:12px}.strategy-list article{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--border)}.strategy-main{min-width:130px}.strategy-main strong,.strategy-main small{display:block}.strategy-main small{color:var(--text-muted);font-size:calc(12px * var(--fs, 1));margin-top:3px}.strategy-controls{display:flex;gap:6px;align-items:center}.strategy-controls select{min-width:130px;padding:5px;border:1px solid var(--border);border-radius:4px;background:var(--surface)}.strategy-notice{color:var(--success);display:flex;gap:5px;align-items:center}.strategy-error{color:var(--danger)!important}
</style>
