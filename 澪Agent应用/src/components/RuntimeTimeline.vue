<script setup>
import * as runtimeApi from '../services/runtimeApi.js'

import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Activity, RefreshCw } from '../icons/index.js'

const props = defineProps({ mode: { type: String, default: 'agent' }, taskId: { type: String, default: '' } })
const operations = ref([])
const loading = ref(false)
const error = ref('')
let timer
const modeLabels = { chat: '聊天', agent: 'Agent', record: '记录整理', memory: '记忆整理', proactive: '主动联系', vision: '视觉', translation: '翻译', profile: '属性' }
const statusLabels = { running: '执行中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
const purposeLabels = { _chat_with_ai_unlocked: '回复用户', run_agent_loop: '推进任务', continue_task: '继续任务', generate_diary_for_date_payload: '生成日记', generate_weekly_review: '生成周记', generate_monthly_review: '生成月记', generate_review_for_date: '生成回顾', analyze_once: '分析画面', _run_companion_actions: '整理聊天记录' }
function labelPurpose(value) { return purposeLabels[value] || '模型运行' }
function timeLabel(value) { if (!value) return '时间未知'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
async function load() {
  if (loading.value) return
  loading.value = true
  try {
    const query = new URLSearchParams({ mode: props.mode, limit: '40' })
    if (props.taskId) query.set('task_id', props.taskId)
    operations.value = (await runtimeApi.loadOperations(query, {})).operations || []
    error.value = ''
  } catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}
onMounted(() => { void load(); timer = window.setInterval(load, 4000) })
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section class="runtime-timeline">
    <header><div><h3><Activity :size="17" />运行时间线</h3><small>只显示真实调用、任务状态和结果证据</small></div><button type="button" title="刷新时间线" :disabled="loading" @click="load"><RefreshCw :class="{ spin: loading }" :size="15" /></button></header>
    <p v-if="error" class="timeline-error">{{ error }}</p>
    <p v-else-if="!operations.length && !loading" class="timeline-empty">当前没有可展示的运行记录</p>
    <article v-for="operation in operations" :key="operation.id" class="timeline-operation">
      <div class="timeline-marker" />
      <div class="timeline-main"><div class="timeline-title"><strong>{{ labelPurpose(operation.purpose) }}</strong><span>{{ modeLabels[operation.mode] || operation.mode }}</span><b :class="`timeline-${operation.status}`">{{ statusLabels[operation.status] || operation.status }}</b></div><small>{{ timeLabel(operation.started_at) }} · {{ operation.request_id || '无请求编号' }}</small>
        <details v-if="operation.calls?.length"><summary>{{ operation.calls.length }} 次模型调用</summary><ul><li v-for="call in operation.calls" :key="call.id"><span>{{ call.actual_model_id || call.selected_model_id || '模型未知' }}</span><small>{{ call.status }} · {{ call.prompt_tokens + call.completion_tokens }} Token<span v-if="call.cost_yuan !== null && call.cost_yuan !== undefined"> · ¥{{ Number(call.cost_yuan).toFixed(4) }}</span></small><em v-if="call.error_code">{{ call.error_code }}</em></li></ul></details>
      </div>
    </article>
  </section>
</template>

<style scoped>
.runtime-timeline{margin:18px 0 24px;border-top:1px solid var(--border);border-bottom:1px solid var(--border);padding:14px 0}.runtime-timeline>header{display:flex;justify-content:space-between;align-items:center;gap:12px}.runtime-timeline h3{display:flex;align-items:center;gap:7px;margin:0;font-size:calc(15px * var(--fs, 1))}.runtime-timeline header small{display:block;color:var(--text-muted);margin-top:4px}.runtime-timeline header button{display:flex;border:1px solid var(--border);background:var(--surface);border-radius:5px;padding:6px;cursor:pointer}.timeline-operation{display:flex;gap:10px;position:relative;padding:13px 0 3px 12px}.timeline-marker{width:7px;height:7px;border-radius:50%;background:var(--success);margin-top:6px;flex:0 0 auto}.timeline-main{min-width:0;flex:1}.timeline-title{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.timeline-title span,.timeline-title b{font-size:calc(12px * var(--fs, 1));font-weight:500;color:var(--text-muted)}.timeline-title b.timeline-failed{color:var(--danger)}.timeline-title b.timeline-running{color:var(--warning)}.timeline-main>small{display:block;color:var(--text-muted);margin-top:4px;overflow-wrap:anywhere}.timeline-main details{margin-top:7px}.timeline-main summary{cursor:pointer;font-size:calc(12px * var(--fs, 1))}.timeline-main ul{margin:6px 0 0;padding-left:18px}.timeline-main li{display:flex;gap:8px;align-items:center;font-size:calc(12px * var(--fs, 1));flex-wrap:wrap}.timeline-main li small{color:var(--text-muted)}.timeline-main li em{color:var(--danger);font-style:normal}.timeline-error{color:var(--danger)}.timeline-empty{color:var(--text-muted);font-size:calc(12px * var(--fs, 1))}
</style>
