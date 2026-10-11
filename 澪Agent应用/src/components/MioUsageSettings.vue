<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { apiRequest } from '../services/api.js'
const days = ref(30), data = ref(null), loading = ref(false), error = ref('')
const labels = { chat: '聊天', agent: 'Agent 工作', record: '日记与回顾', proactive: '主动联系', memory: '记忆整理', vision: '屏幕视觉', translation: '语音翻译', profile: '人格属性' }
let controller, version = 0
const totals = computed(() => (data.value?.modes || []).reduce((sum, item) => ({ calls: sum.calls + Number(item.calls || 0), tokens: sum.tokens + Number(item.prompt_tokens || 0) + Number(item.completion_tokens || 0), cost: sum.cost + Number(item.known_cost_yuan || 0), unknown: sum.unknown + Number(item.unknown_cost_calls || 0) }), { calls: 0, tokens: 0, cost: 0, unknown: 0 }))
async function load() {
  controller?.abort(); controller = new AbortController(); const n = ++version
  loading.value = true; error.value = ''; data.value = null
  try { const value = await apiRequest(`/api/runtime/usage?days=${days.value}`, { signal: controller.signal }); if (n === version) data.value = value }
  catch (cause) { if (n === version) error.value = cause.message }
  finally { if (n === version) loading.value = false }
}
watch(days, load, { immediate: true })
onBeforeUnmount(() => { ++version; controller?.abort() })
</script>
<template>
  <section class="mio-usage-settings">
    <header class="agent-setting-title"><h1>模型用量</h1><p>聊天和 Agent 共用的实际调用记录</p></header>
    <div class="usage-toolbar"><label>统计范围<select v-model.number="days" :disabled="loading"><option :value="1">近 1 天</option><option :value="7">近 7 天</option><option :value="30">近 30 天</option><option :value="90">近 90 天</option><option :value="365">近一年</option></select></label><button :disabled="loading" @click="load">刷新用量</button></div>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="loading" role="status">正在读取用量…</p>
    <template v-if="data"><div class="usage-totals"><article><small>调用次数</small><strong>{{ totals.calls.toLocaleString('zh-CN') }}</strong></article><article><small>输入与输出 Token</small><strong>{{ totals.tokens.toLocaleString('zh-CN') }}</strong></article><article><small>已知费用</small><strong>¥{{ totals.cost.toFixed(4) }}</strong><small v-if="totals.unknown">{{ totals.unknown }} 次费用待确认</small></article></div><div class="usage-table"><table><thead><tr><th>功能</th><th>调用 / 失败</th><th>输入 Token</th><th>输出 Token</th><th>已知费用</th><th>95% 请求耗时</th></tr></thead><tbody><tr v-for="item in data.modes" :key="item.mode"><th>{{ labels[item.mode] || item.mode }}</th><td>{{ item.calls }} / {{ item.failed }}</td><td>{{ Number(item.prompt_tokens || 0).toLocaleString('zh-CN') }}</td><td>{{ Number(item.completion_tokens || 0).toLocaleString('zh-CN') }}</td><td>¥{{ Number(item.known_cost_yuan || 0).toFixed(4) }}<small v-if="item.unknown_cost_calls">{{ item.unknown_cost_calls }} 次待确认</small></td><td>{{ item.p95_duration_ms == null ? '暂无记录' : `${(item.p95_duration_ms / 1000).toFixed(1)} 秒` }}</td></tr></tbody></table></div><p>只统计启用模型运行记录后的请求。未知费用不会按零费用计算；已知费用含供应商回传或已有价格估算，实际账单以供应商为准。</p></template>
  </section>
</template>
<style scoped>
.mio-usage-settings{display:grid;gap:18px;max-width:960px;margin:auto;padding:30px 34px;box-sizing:border-box;min-width:0}.agent-setting-title{margin-bottom:0}.usage-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.usage-toolbar label{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--text-muted)}select,button{font:inherit;font-size:12px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:8px;padding:9px 12px}button{cursor:pointer}button:disabled{opacity:.5}.usage-totals{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.usage-totals article{display:grid;gap:9px;padding:18px;border:1px solid var(--border);border-radius:12px;overflow-wrap:anywhere}.usage-totals strong{font-size:22px;font-weight:500}.usage-totals small,p{font-size:12px;color:var(--text-muted);line-height:1.7;margin:0}.usage-table{overflow:auto;border:1px solid var(--border);border-radius:12px}table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}th,td{padding:13px;text-align:left;border-bottom:1px solid var(--border);font-weight:400}thead th{color:var(--text-muted)}td small{display:block;color:var(--text-muted);margin-top:4px}.error{color:var(--danger)}@media(max-width:480px){.mio-usage-settings{padding:22px 16px}.usage-totals{grid-template-columns:1fr}}
</style>
