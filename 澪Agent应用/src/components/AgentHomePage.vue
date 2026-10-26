<script setup>
import * as tasksApi from '../services/tasksApi.js'

import { inject, nextTick, onMounted, ref } from 'vue'
import { ArrowRight, Archive, Bot, ChevronDown, FileText, FolderOpen, ListChecks, MessageSquareText, Paperclip, Plus, RefreshCw, Settings, Sparkles, X } from '../icons/index.js'
import { taskStatusLabel } from '../agentTaskState.js'

const emit = defineEmits(['navigate'])
const context = inject('mio-chat-page')
const recentTasks = ref([])
const prompt = ref('')
const loading = ref(false)
const error = ref('')
async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try { recentTasks.value = (await tasksApi.loadWorkByLimit6({})).tasks || [] }
  catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}
onMounted(load)

async function startWork(value = prompt.value) {
  const text = value.trim()
  if (text && context) context.draft = text
  emit('navigate', 'chat')
  await nextTick()
  document.querySelector('.composer-input')?.focus()
}
</script>

<template>
  <section class="agent-home">
    <header class="agent-home-header">
      <div class="agent-home-breadcrumb"><Bot :size="15" /><span>Mio Agent</span><i>/</i><strong>工作</strong></div>
      <div class="agent-mode-switch" role="tablist" aria-label="工作模式"><button type="button" role="tab" aria-selected="false" @click="emit('navigate', 'chat')">聊天</button><button type="button" class="active" role="tab" aria-selected="true">工作</button></div>
      <div class="agent-home-heading"><span>新任务</span><h1>我们要做什么？</h1><p>描述一个目标，澪会整理计划、调用现有工具并把结果留在项目里。</p></div>
    </header>
    <form class="agent-home-prompt" @submit.prevent="startWork()">
      <textarea v-model="prompt" rows="3" aria-label="描述要完成的任务" placeholder="描述你想完成的任务…" @keydown.enter.exact.prevent="startWork()" />
      <div class="agent-composer-tools"><div><button type="button" title="添加文件" @click="startWork('请处理我接下来提供的文件。')"><Plus :size="17" /></button><button type="button" title="选择工作流" @click="emit('navigate', 'settings')"><Sparkles :size="15" /></button><span>可附文件、图片和工作流</span></div><div><button type="button" class="agent-model-chip" title="当前 Agent 模型">自动 <ChevronDown :size="13" /></button><button type="submit" class="agent-primary-action"><MessageSquareText :size="16" />{{ prompt.trim() ? '开始任务' : '新建任务' }}</button></div></div>
    </form>
    <section class="agent-suggestions" aria-label="常用工作入口">
      <button type="button" @click="startWork('请帮我整理这份资料，保留可核对的来源。')"><FileText :size="17" /><span><strong>整理资料</strong><small>索引文件并保留可核对来源</small></span><ArrowRight :size="15" /></button>
      <button type="button" @click="startWork('请根据我的要求处理图片、视频或文件，并说明实际使用的工作流。')"><Sparkles :size="17" /><span><strong>处理创作任务</strong><small>使用已配置的图片或视频工作流</small></span><ArrowRight :size="15" /></button>
      <button type="button" @click="emit('navigate', 'projects')"><FolderOpen :size="17" /><span><strong>打开项目资料</strong><small>查看任务、成果和长期记录</small></span><ArrowRight :size="15" /></button>
    </section>
    <p v-if="error" class="agent-home-error" role="alert">
      <span>{{ error }}</span>
      <button type="button" title="关闭错误提示" aria-label="关闭错误提示" @click="error = ''"><X :size="15" /></button>
    </p>
    <div class="agent-home-grid">
      <section class="agent-start-panel">
        <header><h2>从这里开始</h2></header>
        <div class="agent-tool-list">
          <button type="button" @click="emit('navigate', 'chat')"><MessageSquareText :size="22" /><span><strong>告诉澪要完成什么</strong><small>进入任务对话，支持文件和图片</small></span><ArrowRight :size="16" /></button>
          <button type="button" @click="emit('navigate', 'projects')"><Archive :size="22" /><span><strong>查看项目工作区</strong><small>管理资料、成果、学习与流程</small></span><ArrowRight :size="16" /></button>
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
