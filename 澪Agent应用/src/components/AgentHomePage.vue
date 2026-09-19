<script setup>
import { onMounted, ref } from 'vue'
import { ArrowRight, Settings, ListChecks, MessageSquareText, RefreshCw } from '@lucide/vue'
import { apiRequest } from '../services/api.js'
import { taskStatusLabel } from '../agentTaskState.js'

const emit = defineEmits(['navigate'])
const recentTasks = ref([])
const loading = ref(false)
const error = ref('')
async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try { recentTasks.value = (await apiRequest('/api/agent/work?limit=6')).tasks || [] }
  catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <section class="agent-home">
    <header class="agent-home-header">
      <div><span>你的创作与任务空间</span><h1>Mio Agent</h1><p>把想法交给澪，一起完成下一件事。</p></div>
      <button type="button" class="agent-primary-action" @click="emit('navigate', 'chat')"><MessageSquareText :size="17" />进入对话</button>
    </header>
    <p v-if="error" class="agent-home-error" role="alert">{{ error }}</p>
    <div class="agent-home-grid">
      <section class="agent-start-panel">
        <header><h2>开始工作</h2></header>
        <div class="agent-tool-list">
          <button type="button" @click="emit('navigate', 'chat')"><MessageSquareText :size="22" /><span><strong>告诉澪要完成什么</strong><small>制作图片、视频或处理文件</small></span><ArrowRight :size="16" /></button>
          <button type="button" @click="emit('navigate', 'settings')"><Settings :size="22" /><span><strong>工具与工作流</strong><small>连接本地服务，配置创作能力</small></span><ArrowRight :size="16" /></button>
        </div>
      </section>
      <section class="agent-recent-panel">
        <header><h2>最近任务</h2><div class="agent-recent-actions"><button type="button" title="刷新最近任务" :disabled="loading" @click="load"><RefreshCw :class="{ spin: loading }" :size="16" /></button><button type="button" @click="emit('navigate', 'tasks')">全部任务<ArrowRight :size="15" /></button></div></header>
        <div v-if="loading" class="agent-home-empty"><RefreshCw class="spin" :size="18" />正在读取</div>
        <div v-else-if="recentTasks.length" class="agent-job-list">
          <button v-for="task in recentTasks" :key="task.id" type="button" :title="task.snapshot?.goal || task.original_goal" @click="emit('navigate', 'tasks')"><ListChecks :size="18" /><span><strong>{{ task.snapshot?.goal || task.original_goal }}</strong><small :class="['agent-task-status', `status-${task.status}`]">{{ taskStatusLabel(task.status) }}</small></span><ArrowRight :size="16" /></button>
        </div>
        <div v-else-if="!error" class="agent-home-empty">暂无任务</div>
      </section>
    </div>
  </section>
</template>
