<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { PauseCircle, RefreshCw } from '@lucide/vue'
import { apiRequest } from '../services/api.js'
const active = ref([])
const loading = ref(false)
const error = ref('')
let timer
const labels = { chat: '聊天', agent: 'Agent', record: '记录整理', memory: '记忆整理', proactive: '主动联系', vision: '视觉', translation: '翻译', profile: '属性' }
const purposes = { run_agent_loop: '推进任务', continue_task: '继续任务', generate_diary_for_date_payload: '生成日记', generate_weekly_review: '生成周记', generate_monthly_review: '生成月记', generate_review_for_date: '生成回顾', analyze_once: '分析画面', _run_companion_actions: '整理聊天记录' }
function load() {
  if (loading.value) return
  loading.value = true
  apiRequest('/api/privacy/status').then((data) => { active.value = data.active_operations || []; error.value = '' }).catch((cause) => { error.value = cause.message }).finally(() => { loading.value = false })
}
onMounted(() => { load(); timer = window.setInterval(load, 3000) })
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section class="privacy-operations-panel">
    <header><div><h2><PauseCircle :size="18" />在途自动操作</h2><p>暂停时会停止这里列出的自动模型调用；普通聊天仍可由你主动发起。</p></div><button type="button" title="刷新在途操作" :disabled="loading" @click="load"><RefreshCw :class="{ spin: loading }" :size="15" /></button></header>
    <p v-if="error" class="privacy-panel-error">{{ error }}</p><p v-else-if="!active.length && !loading" class="privacy-panel-empty">当前没有在途自动操作</p>
    <ul v-else><li v-for="item in active" :key="item.id"><span><strong>{{ purposes[item.purpose] || '模型操作' }}</strong><small>{{ labels[item.mode] || item.mode }} · {{ item.request_id || '无请求编号' }}</small></span><b>运行中</b></li></ul>
  </section>
</template>

<style scoped>
.privacy-operations-panel{border:1px solid #d7deda;border-radius:6px;padding:14px;margin-bottom:18px}.privacy-operations-panel header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.privacy-operations-panel h2{display:flex;align-items:center;gap:7px;margin:0;font-size:15px}.privacy-operations-panel p{margin:5px 0 0;color:#66756e;font-size:12px}.privacy-operations-panel header button{display:flex;border:1px solid #cbd6d0;background:var(--surface,#fff);border-radius:5px;padding:6px;cursor:pointer}.privacy-operations-panel ul{list-style:none;padding:0;margin:12px 0 0;display:grid;gap:6px}.privacy-operations-panel li{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid #e4e9e5}.privacy-operations-panel li span{min-width:0}.privacy-operations-panel strong,.privacy-operations-panel small{display:block;overflow-wrap:anywhere}.privacy-operations-panel small{color:#718078;margin-top:3px;font-size:11px}.privacy-operations-panel b{font-size:11px;color:#a06b25;white-space:nowrap}.privacy-panel-error{color:#a24237}.privacy-panel-empty{padding:8px 0}
</style>
