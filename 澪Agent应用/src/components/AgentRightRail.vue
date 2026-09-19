<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Film, ImagePlus, MessageSquareText, Pin, PinOff, Play, RefreshCw, Settings, Sparkles } from '@lucide/vue'
import * as creationApi from '../services/creationApi.js'
import { apiRequest } from '../services/api.js'
import { taskStatusLabel } from '../agentTaskState.js'

const props = defineProps({ pinned: Boolean })
const emit = defineEmits(['navigate', 'toggle-pin'])
const data = ref(null)
const taskData = ref([])
const loading = ref(true)
const starting = ref(false)
const error = ref('')
let pollTimer = null

const comfyOnline = computed(() => Boolean(data.value?.comfyui?.reachable))
const workflows = computed(() => data.value?.workflows || [])
const imageWorkflow = computed(() => workflows.value.find((item) => item.id === data.value?.defaults?.image_workflow_id))
const videoWorkflow = computed(() => workflows.value.find((item) => item.id === data.value?.defaults?.video_workflow_id))
const latestTask = computed(() => taskData.value.find(t => !['completed', 'cancelled'].includes(t.status)) || taskData.value[0] || null)

function jobLabel(job) {
  return {
    created: '准备中', needs_confirmation: '等待确认', submitted: '已提交', queued: '排队中',
    running: '生成中', completed: '已完成', failed: '失败', cancelled: '已取消', timed_out: '超时', unknown: '结果待核对',
  }[job?.status] || '暂无任务'
}

async function load({ quiet = false } = {}) {
  if (!quiet) loading.value = true
  try {
    data.value = await creationApi.loadCreationBootstrap()
    taskData.value = (await apiRequest('/api/agent/work?limit=20')).tasks || []
    error.value = ''
  } catch (cause) {
    if (!quiet) error.value = cause.message
  } finally {
    loading.value = false
  }
}

async function startComfyUi() {
  starting.value = true
  error.value = ''
  try {
    const result = await creationApi.startComfyUi()
    if (!result.ok) error.value = result.message || 'ComfyUI 尚未响应。'
    await load({ quiet: true })
  } catch (cause) {
    error.value = cause.message
  } finally {
    starting.value = false
  }
}

onMounted(() => {
  void load()
  pollTimer = window.setInterval(() => void load({ quiet: true }), 10_000)
})
onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
  <header class="agent-rail-header">
    <div><Sparkles :size="18" /><span><small>Agent 状态</small><strong>{{ latestTask ? taskStatusLabel(latestTask.status) : '暂无任务' }}</strong></span></div>
    <button type="button" :title="props.pinned ? '取消固定展开' : '固定展开'" @click="emit('toggle-pin')"><component :is="props.pinned ? PinOff : Pin" :size="14" /></button>
  </header>
  <section class="agent-rail-services">
    <button type="button" title="ComfyUI 设置" @click="emit('navigate', 'settings')"><i :class="{ online: comfyOnline }" /><span><strong>ComfyUI</strong><small>{{ comfyOnline ? '已连接' : '未连接' }}</small></span></button>
    <button type="button" title="图片工作流设置" @click="emit('navigate', 'settings')"><ImagePlus :size="17" /><span><strong>图片工作流</strong><small>{{ imageWorkflow?.available ? '已登记，运行前检查依赖' : '需要配置' }}</small></span></button>
    <button type="button" title="视频工作流设置" @click="emit('navigate', 'settings')"><Film :size="17" /><span><strong>视频工作流</strong><small>{{ videoWorkflow?.available ? '已登记，运行前检查依赖' : '需要配置' }}</small></span></button>
  </section>
  <button v-if="!comfyOnline" class="agent-rail-connect" type="button" title="启动并连接 ComfyUI" :disabled="starting" @click="startComfyUi"><RefreshCw v-if="starting" class="spin" :size="16" /><Play v-else :size="16" /><span><small>本地服务</small><strong>{{ starting ? '正在连接' : '启动并连接' }}</strong></span></button>
  <button type="button" class="agent-rail-job" title="查看当前任务" @click="emit('navigate', 'tasks')">
    <MessageSquareText :size="17" /><span><small>当前任务</small><strong>{{ latestTask ? taskStatusLabel(latestTask.status) : '暂无任务' }}</strong><p v-if="latestTask">{{ latestTask.snapshot?.goal || latestTask.original_goal }}</p></span>
  </button>
  <button class="agent-rail-settings" type="button" title="设置与诊断" @click="emit('navigate', 'settings')"><Settings :size="17" /><span><small>Agent</small><strong>设置与诊断</strong></span></button>
  <p v-if="error" class="agent-rail-error">{{ error }}</p>
</template>
