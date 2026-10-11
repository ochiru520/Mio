<script setup>
import appMetadata from '../../package.json' with { type: 'json' }
import { computed, inject, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from 'vue'
import { Home, Clock3, FolderOpen, ImagePlus, Plus, ChevronDown, ChevronRight, Settings, PanelLeft, PanelRight, Minus, Maximize2, X, Search, Check, ArrowLeft, Bell, FileText, BookOpen, UserRound, Sun, Sparkles } from '@lucide/vue'
import { apiRequest } from '../services/api.js'
import ChatPage from './ChatPage.vue'
import ProjectCreationDialog from './ProjectCreationDialog.vue'
import ProjectFolderMenu from './ProjectFolderMenu.vue'
import ConversationMenu from './ConversationMenu.vue'
import AgentFilesPreview from './AgentFilesPreview.vue'
import AgentArtifactsPage from './AgentArtifactsPage.vue'
import AgentRoutinesPage from './AgentRoutinesPage.vue'
import AgentLearningPanel from './AgentLearningPanel.vue'
import AgentTasksPage from './AgentTasksPage.vue'
import AgentTaskProgress from './AgentTaskProgress.vue'
import AgentWorkspaceHub from './AgentWorkspaceHub.vue'
import MioSettingsWindow from './MioSettingsWindow.vue'
import AgentCapabilities from './AgentCapabilities.vue'
import { beginWindowMove, blockStandardWindowDrag } from '../desktopWindowDrag.js'
import { taskStatusLabel } from '../agentTaskState.js'
import { findAgentModeConversation } from '../workspaceState.js'

defineProps({ error: String })
const emit = defineEmits(['workspace', 'window-control', 'close-error'])
const context = inject('mio-chat-page'), settings = inject('mio-settings-page')
const page = ref(({ settings: 'settings', projects: 'tools', tasks: 'task-detail' })[context.activeView] || 'conversation')
const projects = ref([]), index = reactive({ projects: {}, modes: {} }), tasks = ref([]), currentTasks = ref([])
const expandedProjects = ref(new Set()), sidebar = ref(window.innerWidth > 650), right = ref(false), personal = ref(false)
const previewWidth = ref(Math.min(520, Math.max(240, Number(localStorage.getItem('mio_agent_preview_width')) || 300)))
const previewResizing = ref(false)
const artifacts = ref([])
const projectPicker = ref(false), projectSearch = ref(''), newProject = ref(false), projectName = ref('')
const currentArtifact = ref(null), learningOpen = ref(false), busy = ref(false), notice = ref(''), pendingCount = ref(0)
const artifactTask = ref(null)
const appearance = ref({}), imageNonce = ref(Date.now()), hubTab = ref('tasks')
let version = 0, disposed = false, timer, taskTimer, taskPolling = false, modeSwitch = 0
async function refreshLiveTask() {
  if (!context.sending || taskPolling || document.hidden) return
  taskPolling=true; const id=context.selectedConversationId
  try { const data=await apiRequest(`/api/agent/work?conversation_id=${encodeURIComponent(id)}&limit=1`); if (!disposed && id===context.selectedConversationId) currentTasks.value=data.tasks || [] } catch {} finally { taskPolling=false }
}
let artifactPreviewVersion = 0
const drafts = new Map()
const mode = computed(() => context.agentSurfaceMode || 'work')
const currentProject = computed(() => projects.value.find(item => item.id === index.projects[context.selectedConversationId]))
const currentTask = computed(() => currentTasks.value[0] || null)
const previewConversationId = computed(() => currentArtifact.value ? currentArtifact.value.conversation_id || '' : context.selectedConversationId)
const previewProjectId = computed(() => currentArtifact.value ? currentArtifact.value.project_id || '' : currentProject.value?.id)
const previewTasks = computed(() => currentArtifact.value
  ? artifactTask.value ? [artifactTask.value] : tasks.value.filter(task => task.conversation_id === previewConversationId.value)
  : currentTasks.value)
const projectOptions = computed(() => projects.value.filter(item => item.name.toLowerCase().includes(projectSearch.value.trim().toLowerCase())))
const showArchived = ref(false), conversationMenu = ref(null), projectMenu = ref(null)
const sortedConversations = computed(() => context.conversations.filter(item => showArchived.value ? item.archived : !item.archived)
  .slice().sort((a,b) => Number(Boolean(b.pinned))-Number(Boolean(a.pinned))))
const ungrouped = computed(() => sortedConversations.value.filter(item => !index.projects[item.id]))
function menuFor(event, conversation) {
  event.preventDefault(); event.stopPropagation()
  conversationMenu.value = { conversation, x:event.clientX || event.currentTarget.getBoundingClientRect().right, y:event.clientY || event.currentTarget.getBoundingClientRect().bottom }
  projectMenu.value = null
}
function projectMenuFor(event, project) {
  event.preventDefault(); event.stopPropagation(); conversationMenu.value = null
  projectMenu.value = { project, x: event.clientX || event.currentTarget.getBoundingClientRect().right, y: event.clientY || event.currentTarget.getBoundingClientRect().bottom }
}
async function removeProject(project) {
  if (busy.value || context.sending) return
  projectMenu.value = null
  if (!window.confirm(`删除项目「${project.name}」？\n对话会移到「未归入项目」，电脑上的文件夹和文件会保留。`)) return
  busy.value = true
  try {
    await apiRequest(`/api/workspace/projects/${project.id}`, { method: 'DELETE' })
    expandedProjects.value = new Set([...expandedProjects.value].filter(id => id !== project.id))
    await load(); notice.value = '项目已删除，对话和本机文件已保留。'
  } catch (cause) { notice.value = cause.message }
  finally { busy.value = false }
}
async function removeProjectDirectory(project) {
  if (busy.value || context.sending || !project?.directory_created) return
  projectMenu.value = null
  const path = project.directory || '这个项目文件夹'
  if (!window.confirm(`永久删除项目文件夹「${path}」？\n文件夹中的文件会被删除，项目和对话仍会保留。此操作不能撤销。`)) return
  busy.value = true
  try {
    await apiRequest(`/api/workspace/projects/${encodeURIComponent(project.id)}/directory`, { method: 'DELETE' })
    await load(); notice.value = '项目文件夹已删除，项目和对话仍已保留。'
  } catch (cause) { notice.value = cause.message }
  finally { busy.value = false }
}
function openProjectCreation() { projectPicker.value = false; newProject.value = true; projectMenu.value = null; conversationMenu.value = null }
async function reloadConversations() { context.conversations = await apiRequest('/api/agent/conversations').then(items => items.filter(item => item.kind === 'agent')) }
async function conversationAction(action, value, target = conversationMenu.value?.conversation) {
  if (!target || busy.value || context.sending) return
  conversationMenu.value = null
  try {
    if (action === 'rename') await context.renameConversation(target)
    else if (action === 'delete') await context.deleteConversation(target)
    else if (['pin','unread','archive'].includes(action)) {
      const key = {pin:'pinned',unread:'unread',archive:'archived'}[action]
      await apiRequest(`/api/agent/conversations/${target.id}/state`, {method:'PATCH',body:JSON.stringify({[key]:!target[key]})})
      await reloadConversations()
    } else if (action === 'project') {
      await apiRequest(value ? `/api/workspace/projects/${value}/conversations/${target.id}` : `/api/workspace/conversations/${target.id}/project`, {method:value ? 'POST' : 'DELETE'})
      await load()
    } else if (action === 'fork') {
      busy.value = true
      let fork
      try { fork = await apiRequest(`/api/agent/conversations/${target.id}/fork`,{method:'POST'}); await reloadConversations() } finally { busy.value = false }
      await load(); await openConversation(fork.id)
    } else if (action === 'parent') await openConversation(target.parent_id)
    else if (action === 'window') {
      if (window.pywebview?.api?.open_agent_conversation) {
        const result = await window.pywebview.api.open_agent_conversation(target.id)
        if (!result?.ok) throw new Error(result?.error || '新窗口打开失败')
      } else {
        const url = new URL(location.href); url.searchParams.set('workspace','agent'); url.searchParams.set('conversation_id',target.id)
        window.open(url.toString(),'_blank')
      }
    } else {
      let text = ''
      if (action === 'markdown') text = (await apiRequest(`/api/agent/conversations/${target.id}/export`)).markdown
      if (action === 'directory') {
        const project = projects.value.find(item => item.id === index.projects[target.id])
        const config = project ? await apiRequest(`/api/workspace/execution/projects/${project.id}`) : null
        text = config?.code?.root || project?.directory || ''
        if (!text) throw new Error('此项目尚未关联文件夹，请在代码与电脑设置中选择目录。')
      }
      if (action === 'link') { const url = new URL(location.href); url.searchParams.set('workspace','agent'); url.searchParams.set('conversation_id',target.id); text=url.toString() }
      await navigator.clipboard.writeText(text); notice.value='已复制。'
    }
  } catch (cause) { notice.value=cause.message }
}
function conversationShortcut(event) {
  if (event.target.closest('input,textarea,[contenteditable=true]') || page.value === 'settings') return
  const action = event.ctrlKey && event.altKey ? ({r:'rename',p:'pin',l:'link'})[event.key.toLowerCase()] : event.ctrlKey && event.shiftKey ? ({u:'unread',a:'archive',c:'directory'})[event.key.toLowerCase()] : ''
  if (!action) return
  event.preventDefault(); void conversationAction(action,undefined,conversationMenu.value?.conversation || context.conversations.find(item => item.id===context.selectedConversationId))
}
const conversationTitle = computed(() => context.conversations.find(item => item.id === context.selectedConversationId)?.title || ({ chat: '聊天', work: '新任务', learn: '学习', image: '生图' }[mode.value]))
const heroImage = computed(() => appearance.value.hero_url ? `${appearance.value.hero_url}?v=${imageNonce.value}` : context.avatarUrl)
const personalImage = computed(() => appearance.value.personal_url ? `${appearance.value.personal_url}?v=${imageNonce.value}` : context.userAvatarCustom ? context.userAvatarUrl : context.avatarUrl)
const greeting = computed(() => { const hour = new Date().getHours(); return hour < 5 ? '夜深了，我还在这里。' : hour < 12 ? '早上好，很高兴见到你。' : hour < 18 ? '下午好，很高兴见到你。' : '晚上好，很高兴见到你。' })
const sentence = computed(() => ({ chat: '今天想聊些什么？我在这里听你说。', work: '今天也一起，把想做的事慢慢完成。', learn: '今天想学些什么？我陪你读懂、练习，再一起复习。', image: '把你想看到的画面告诉我，我们一起把它画出来。' }[mode.value]))
provide('mio-agent-surface', reactive({ mode, currentTask }))

async function load() {
  const n = ++version, conversation = context.selectedConversationId
  try {
    const data = await apiRequest('/api/workspace')
    const links = await apiRequest('/api/workspace/conversations')
    const selected = conversation ? await apiRequest(`/api/agent/work?conversation_id=${encodeURIComponent(conversation)}&limit=30`) : { tasks: [] }
    const pending = await apiRequest('/api/workspace/pending?limit=1')
    const outputs = conversation ? await apiRequest(`/api/artifacts?conversation_id=${encodeURIComponent(conversation)}&limit=50`) : { artifacts: [] }
    if (disposed || n !== version || conversation !== context.selectedConversationId) return
    projects.value = data.projects || []; tasks.value = data.tasks || []; Object.assign(index, { projects: links.projects || {}, modes: links.modes || {} }); currentTasks.value = selected.tasks || []
    pendingCount.value = pending.total || 0; artifacts.value = outputs.artifacts || []
    if (!expandedProjects.value.size && projects.value.length) expandedProjects.value = new Set([links.projects?.[conversation] || projects.value[0].id])
    if (index.modes[conversation] && !busy.value) context.agentSurfaceMode = index.modes[conversation]
    notice.value = ''
  } catch (cause) { if (n === version) notice.value = cause.message }
}
async function loadAppearance() { try { appearance.value = await apiRequest('/api/agent/surface'); imageNonce.value = Date.now() } catch (cause) { notice.value = cause.message } }
function stash() { if (context.selectedConversationId) drafts.set(context.selectedConversationId, { draft: context.draft, attachments: [...context.attachments] }) }
async function openConversation(id) {
  if (context.sending || busy.value) return
  busy.value = true; stash(); page.value = 'conversation'; personal.value = false; currentArtifact.value = null
  try {
    await context.selectConversation(id)
    if (context.selectedConversationId !== id) return
    const conversation = context.conversations.find(item => item.id === id)
    if (conversation?.unread) {
      await apiRequest(`/api/agent/conversations/${id}/state`, {method:'PATCH',body:JSON.stringify({unread:false})})
      conversation.unread = false
    }
    const saved = drafts.get(id); context.draft = saved?.draft || ''; context.attachments = saved?.attachments || []
    context.agentSurfaceMode = index.modes[id] || 'work'; await load()
    if (index.projects[id]) expandedProjects.value = new Set([...expandedProjects.value,index.projects[id]])
  } catch (cause) { notice.value = cause.message }
  finally { busy.value = false }
}
async function saveMode() {
  const id = context.selectedConversationId
  if (!id) return
  await apiRequest(`/api/workspace/conversations/${id}/mode`, { method: 'PUT', body: JSON.stringify({ mode: mode.value }) })
  index.modes[id] = mode.value; localStorage.setItem('mio_agent_surface_mode', mode.value)
}
async function startConversation(projectId = currentProject.value?.id || '', nextMode = mode.value) {
  if (context.sending || busy.value) return
  busy.value = true; stash(); notice.value = ''
  try {
    const oldId = context.selectedConversationId
    await context.createNewConversation(nextMode)
    if (!context.selectedConversationId || context.selectedConversationId === oldId) throw new Error('未能创建新对话，请检查连接。')
    context.agentSurfaceMode = nextMode; context.draft = ''; context.attachments = []; page.value = 'conversation'; currentArtifact.value = null
    if (projectId) { await apiRequest(`/api/workspace/projects/${projectId}/conversations/${context.selectedConversationId}`, { method: 'POST' }); index.projects[context.selectedConversationId] = projectId }
    await saveMode(); await load(); await nextTick(); document.querySelector('.mio-agent-surface .composer-input')?.focus()
  } catch (cause) { notice.value = cause.message }
  finally { busy.value = false }
}
async function switchMode(value) {
  if (context.sending || busy.value) return
  // Explicit workflows belong to the current mode. The picker reads the
  // configured default again when we return to image.
  if (value !== mode.value) {
    context.creationWorkflowId = ''
  }
  const n = ++modeSwitch
  const target = findAgentModeConversation(context.conversations, index, value, currentProject.value?.id || '')
  if (target) await openConversation(target.id)
  else if (!context.visibleMessages.length && !context.draft.trim() && !context.attachments.length) { context.agentSurfaceMode = value; page.value = 'conversation'; await saveMode() }
  else await startConversation(currentProject.value?.id || '', value)
  if (n === modeSwitch) { context.agentSurfaceMode = value; localStorage.setItem('mio_agent_surface_mode', value); page.value = 'conversation'; context.activeView = 'chat' }
}
function toggleProject(id) { const next = new Set(expandedProjects.value); next.has(id) ? next.delete(id) : next.add(id); expandedProjects.value = next }
async function chooseProject(id) {
  if (context.sending || busy.value) return
  busy.value = true
  try { if (id) await apiRequest(`/api/workspace/projects/${id}/conversations/${context.selectedConversationId}`, { method: 'POST' })
    else await apiRequest(`/api/workspace/conversations/${context.selectedConversationId}/project`, { method: 'DELETE' })
    if (id) index.projects[context.selectedConversationId] = id; else delete index.projects[context.selectedConversationId]
    projectPicker.value = false; newProject.value = false; await load()
  } catch (cause) { notice.value = cause.message }
  finally { busy.value = false }
}
async function createProject(payload) {
  if (busy.value) return
  busy.value = true
  try { const result = await apiRequest('/api/workspace/projects', { method: 'POST', body: JSON.stringify(payload) }); busy.value = false; projects.value.unshift(result); expandedProjects.value = new Set([...expandedProjects.value,result.id]); projectName.value = ''; await chooseProject(result.id) }
  catch (cause) { notice.value = cause.message }
  finally { busy.value = false }
}
function navigate(value) {
  personal.value = false; learningOpen.value = false; currentArtifact.value = null
  if (value === 'home') { page.value = 'conversation'; context.activeView = 'chat'; if (context.visibleMessages.length) void startConversation(); return }
  if (value === 'image') { void switchMode('image'); return }
  page.value = value
  context.activeView = value === 'settings' ? 'settings' : 'chat'
}
async function openSource(item) {
  if (context.sending || busy.value) return
  try {
    let id = item.conversation_id
    if (!id && item.task_id) { const task = await apiRequest(`/api/agent/work/${item.task_id}`); id = task.conversation_id || task.task?.conversation_id }
    if (id) await openConversation(id); else notice.value = '此成果没有可确认的来源对话。'
  } catch (cause) { notice.value = cause.message || '来源对话读取失败，请重试。' }
}
async function preview(item) {
  const n = ++artifactPreviewVersion
  artifactTask.value = null
  currentArtifact.value = { ...item, project_id: item.project_id || index.projects[item.conversation_id] || '' }
  right.value = true
  if (!item.task_id) return
  try {
    const result = await apiRequest(`/api/agent/work/${item.task_id}`)
    if (disposed || n !== artifactPreviewVersion || currentArtifact.value?.id !== item.id) return
    const task = result.task || result
    const conversation = item.conversation_id || task.conversation_id || ''
    artifactTask.value = task
    currentArtifact.value = { ...item, conversation_id: conversation, project_id: item.project_id || index.projects[conversation] || task.snapshot?.project_id || '' }
  } catch (cause) {
    if (!disposed && n === artifactPreviewVersion && currentArtifact.value?.id === item.id) notice.value = cause.message || '成果来源读取失败，请重试。'
  }
}
function isTitlebarControl(event) { return event.target.closest('button, input, select, textarea, a, nav, [role=button]') }
function maximizeWindow(event) { if (!isTitlebarControl(event)) emit('window-control', 'maximize') }
function beginWindowDrag(event) {
  if (window.pywebview?.api?.window_move_by) {
    beginWindowMove(event, message => { notice.value = message })
    return
  }
  if (event.button !== 0 || isTitlebarControl(event)) return
  const drag = window.pywebview?.api?.window_drag
  if (!drag) return
  event.preventDefault()
  event.stopPropagation()
  void drag().then(result => { if (result?.ok === false) notice.value = result.error || '窗口拖动失败' }).catch(cause => { notice.value = cause.message })
}
function showSettings(section = 'budget') { page.value = 'settings'; personal.value = false; settings.openSettingsSection(section) }
function closeOutside(event) { if (!event.target.closest('.agent-project-picker')) projectPicker.value = false; if (!event.target.closest('.agent-personal')) personal.value = false }
function fitWindowPanels() { if (window.innerWidth <= 820) right.value = false; if (window.innerWidth <= 650) sidebar.value = false; previewWidth.value = clampPreviewWidth(previewWidth.value) }
function previewWidthBounds() {
  const viewportWidth = document.querySelector('.mio-agent-surface')?.clientWidth || window.innerWidth
  if (viewportWidth <= 820) {
    const iconbarWidth = viewportWidth <= 1000 ? 44 : 48
    const projectSidebarWidth = viewportWidth > 650 && sidebar.value && !['artifacts', 'routines'].includes(page.value)
      ? (viewportWidth <= 1000 ? 166 : 196)
      : 0
    const reservedWidth = viewportWidth <= 650 ? 12 : 180
    const maximum = Math.min(520, Math.max(0, viewportWidth - iconbarWidth - projectSidebarWidth - reservedWidth))
    return { minimum: Math.min(120, maximum), maximum }
  }
  const iconbarWidth = viewportWidth <= 1000 ? 44 : 48
  const projectSidebarWidth = sidebar.value && !['artifacts', 'routines'].includes(page.value)
    ? (viewportWidth <= 1000 ? 154 : 196)
    : 0
  const minimum = 240
  const maximum = Math.min(520, Math.max(minimum, viewportWidth - iconbarWidth - projectSidebarWidth - 420))
  return { minimum, maximum }
}
function clampPreviewWidth(value) {
  const { minimum, maximum } = previewWidthBounds()
  return Math.round(Math.min(maximum, Math.max(minimum, value)))
}
function resizePreviewBy(delta) {
  previewWidth.value = clampPreviewWidth(previewWidth.value + delta)
  localStorage.setItem('mio_agent_preview_width', String(previewWidth.value))
}
function onPreviewResizeKeydown(event) {
  if (event.key === 'ArrowLeft') resizePreviewBy(event.shiftKey ? 64 : 24)
  else if (event.key === 'ArrowRight') resizePreviewBy(event.shiftKey ? -64 : -24)
  else if (event.key === 'Home') previewWidth.value = previewWidthBounds().minimum
  else if (event.key === 'End') previewWidth.value = previewWidthBounds().maximum
  else return
  event.preventDefault()
  localStorage.setItem('mio_agent_preview_width', String(previewWidth.value))
}
watch(() => [sidebar.value, page.value], () => { previewWidth.value = clampPreviewWidth(previewWidth.value) })
let stopPreviewResize = null
function startPreviewResize(event) {
  if (event.button !== 0) return
  stopPreviewResize?.()
  if (!right.value || page.value === 'settings') return
  event.preventDefault(); event.stopPropagation()
  previewResizing.value = true
  const startX = event.clientX, startWidth = previewWidth.value
  const move = (moveEvent) => { previewWidth.value = clampPreviewWidth(startWidth - (moveEvent.clientX - startX)) }
  const end = () => {
    previewResizing.value = false
    previewWidth.value = clampPreviewWidth(previewWidth.value)
    localStorage.setItem('mio_agent_preview_width', String(previewWidth.value))
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', end); window.removeEventListener('pointercancel', end); stopPreviewResize = null
  }
  stopPreviewResize = end
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', end, { once: true }); window.addEventListener('pointercancel', end, { once: true })
}
watch(() => context.selectedConversationId, () => { currentTasks.value = []; artifacts.value = []; currentArtifact.value = null; void load() })
watch(() => context.activeView, value => { if (value === 'settings') page.value = 'settings' })
onMounted(() => { previewWidth.value = clampPreviewWidth(previewWidth.value); void load(); void loadAppearance(); document.addEventListener('keydown',conversationShortcut); document.addEventListener('pointerdown', closeOutside); window.addEventListener('mio-agent-appearance', loadAppearance); window.addEventListener('mio-agent-workspace-changed', load); window.addEventListener('resize', fitWindowPanels); taskTimer=window.setInterval(refreshLiveTask,900); timer = window.setInterval(() => { if (!document.hidden && !busy.value) void load() }, 6000) })
onBeforeUnmount(() => { stopPreviewResize?.(); disposed = true; ++version; clearInterval(timer); clearInterval(taskTimer); document.removeEventListener('keydown',conversationShortcut); document.removeEventListener('pointerdown', closeOutside); window.removeEventListener('mio-agent-appearance', loadAppearance); window.removeEventListener('mio-agent-workspace-changed', load); window.removeEventListener('resize', fitWindowPanels) })
</script>
<template>
  <section :data-mode="mode" :style="{ '--agent-preview-width': `${previewWidth}px` }" :class="['mio-agent-surface', { 'sidebar-closed': !sidebar, 'preview-closed': !right || page === 'capabilities', 'is-settings': page === 'settings', 'is-library': ['artifacts','routines','capabilities'].includes(page), 'is-routines': page === 'routines', 'preview-resizing': previewResizing }]">
    <ProjectCreationDialog v-if="newProject" :busy="busy" @submit="createProject" @close="newProject = false" />
    <ProjectFolderMenu v-if="projectMenu" :key="projectMenu.project.id" v-bind="projectMenu" :directory-created="Boolean(projectMenu.project.directory_created)" :busy="busy || context.sending" @close="projectMenu = null" @delete="removeProject" @delete-directory="removeProjectDirectory" />
    <header class="mio-agent-titlebar pywebview-drag-region" @pointerdown.left="beginWindowDrag" @mousedown.left="blockStandardWindowDrag" @dblclick="maximizeWindow"><button v-if="page !== 'settings'" type="button" aria-label="收起或展开项目和会话栏" @click="sidebar = !sidebar"><PanelLeft :size="16" /></button><div class="mio-agent-brand pywebview-drag-region" @pointerdown.left="beginWindowDrag" @mousedown.left="blockStandardWindowDrag" @dblclick.stop="maximizeWindow">Mio Agent</div><div class="mio-agent-drag-strip pywebview-drag-region" aria-label="拖动 Mio Agent 窗口" @pointerdown.left="beginWindowDrag" @mousedown.left="blockStandardWindowDrag" @dblclick.stop="maximizeWindow"></div><nav v-if="!['settings','artifacts','routines'].includes(page)" aria-label="功能切换"><button v-for="(label,value) in {chat:'聊天',work:'工作',learn:'学习'}" :key="value" :class="{ selected: mode === value }" :aria-pressed="mode === value" :disabled="context.sending || busy" type="button" @click="switchMode(value)">{{ label }}</button></nav><div class="mio-agent-window-controls"><button v-if="page !== 'settings' && page !== 'routines'" type="button" aria-label="收起或展开文件预览" @click="right = !right"><PanelRight :size="16" /></button><button type="button" aria-label="最小化" @click="emit('window-control','minimize')"><Minus :size="15" /></button><button type="button" aria-label="最大化或还原" @click="emit('window-control','maximize')"><Maximize2 :size="13" /></button><button type="button" aria-label="关闭到后台" @click="emit('window-control','close')"><X :size="16" /></button></div></header>
    <ConversationMenu v-if="conversationMenu" :key="conversationMenu.conversation.id" v-bind="conversationMenu" :projects="projects" :project-id="index.projects[conversationMenu.conversation.id]" :busy="busy || context.sending" @close="conversationMenu = null" @action="conversationAction" /><MioSettingsWindow v-if="page === 'settings'" agent :projects="projects" :project-id="currentProject?.id || ''" @return="page = 'conversation'; context.activeView = 'chat'" />
    <template v-else><nav class="mio-agent-iconbar" aria-label="应用导航"><button type="button" aria-label="首页" :class="{ selected: page === 'conversation' && mode !== 'image' }" @click="navigate('home')"><Home :size="18" /></button><button type="button" aria-label="定时任务" :class="{ selected: page === 'routines' }" @click="navigate('routines')"><Clock3 :size="18" /></button><button type="button" aria-label="项目空间" :class="{ selected: page === 'artifacts' }" @click="navigate('artifacts')"><FolderOpen :size="18" /></button><button type="button" :disabled="busy || context.sending" aria-label="生图" :class="{ selected: mode === 'image' && page === 'conversation' }" @click="navigate('image')"><ImagePlus :size="18" /></button><div class="agent-personal"><button type="button" aria-label="我的空间" :aria-expanded="personal" @click="personal = !personal"><img :src="personalImage" alt="我的空间" /></button><div v-if="personal" class="agent-personal-menu"><div class="agent-personal-header"><UserRound :size="16" /><strong>我的空间</strong></div><button type="button" @click="hubTab = 'pending'; navigate('tools')"><Bell :size="15" />消息与待处理<span>{{ pendingCount }}</span></button><button type="button" @click="right = false; navigate('capabilities')"><Sparkles :size="16" />能力</button><button type="button" @click="showSettings('self-upgrade')"><FileText :size="16" />改造 Mio</button><button type="button" @click="showSettings('profile')"><Sparkles :size="16" />人设与表达</button><button type="button" @click="showSettings('appearance')"><Sun :size="16" />外观主题</button><button type="button" @click="showSettings()"><Settings :size="15" />设置</button><button type="button" @click="emit('workspace')"><ArrowLeft :size="15" />返回 Mio 聊天页面</button><small class="agent-personal-version"><span>版本</span><span>{{ settings.runtimeIdentity?.app_version || settings.runtimeIdentity?.version || settings.runtimeIdentity?.build?.version || appMetadata.version }}</span></small></div></div></nav>
      <aside v-if="sidebar && !['artifacts','routines','capabilities'].includes(page)" class="mio-agent-project-sidebar">
        <button type="button" class="agent-new-conversation" :disabled="busy || context.sending" @click="startConversation()"><Plus :size="16" />新对话</button>
        <div class="agent-sidebar-label"><small>项目</small><button type="button" aria-label="新建项目" :disabled="busy || context.sending" @click="openProjectCreation"><Plus :size="14" /></button></div>
        <section v-for="project in projects" :key="project.id" class="agent-project-folder">
          <header class="agent-project-folder-heading" @contextmenu="projectMenuFor($event, project)">
            <button type="button" :aria-expanded="expandedProjects.has(project.id)" @click="toggleProject(project.id)"><component :is="expandedProjects.has(project.id) ? ChevronDown : ChevronRight" :size="13" /><FolderOpen :size="15" /><span>{{ project.name }}</span></button>
            <button type="button" class="agent-project-menu-trigger" :aria-label="`${project.name}的项目菜单`" @click="projectMenuFor($event, project)">···</button>
          </header>
          <div v-if="expandedProjects.has(project.id)">
            <div v-for="conversation in sortedConversations.filter(item => index.projects[item.id] === project.id)" :key="conversation.id" class="agent-conversation-item" @contextmenu="menuFor($event, conversation)">
              <button type="button" :title="conversation.title" :class="{ selected: context.selectedConversationId === conversation.id }" @click="openConversation(conversation.id)"><span v-if="conversation.pinned" title="已置顶">⌃ </span>{{ conversation.title }}<i v-if="conversation.unread" class="agent-unread-dot" aria-label="未读" /></button><button type="button" :aria-label="`${conversation.title}的菜单`" @click="menuFor($event,conversation)">···</button>
            </div>
            <button type="button" class="agent-project-new" :disabled="busy || context.sending" @click="startConversation(project.id)"><Plus :size="13" />新对话</button>
          </div>
        </section>
        <button type="button" class="agent-sidebar-label" @click="showArchived = !showArchived">{{ showArchived ? '返回活动对话' : '已归档对话' }}</button>
        <small class="agent-sidebar-label">{{ showArchived ? '已归档' : '未归入项目' }}</small>
        <div v-for="conversation in ungrouped" :key="conversation.id" class="agent-conversation-item" @contextmenu="menuFor($event, conversation)"><button type="button" :title="conversation.title" :class="{ selected: context.selectedConversationId === conversation.id }" @click="openConversation(conversation.id)"><span v-if="conversation.pinned" title="已置顶">⌃ </span>{{ conversation.title }}<i v-if="conversation.unread" class="agent-unread-dot" aria-label="未读" /></button><button type="button" :aria-label="`${conversation.title}的菜单`" @click="menuFor($event,conversation)">···</button></div>
        <p v-if="!context.conversations.length" class="agent-muted">还没有对话</p><button type="button" class="agent-sidebar-bottom" @click="hubTab = 'tasks'; navigate('tools')">项目工具</button>
      </aside>
      <main class="mio-agent-main"><div v-if="error || notice" class="agent-surface-error" role="alert"><span>{{ error || notice }}</span><button type="button" aria-label="关闭提示" @click="notice = ''; emit('close-error')"><X :size="14" /></button></div>
        <AgentArtifactsPage v-if="page === 'artifacts'" :projects="projects" @preview="preview" @open-source="openSource" @tools="hubTab = 'tasks'; navigate('tools')" />
        <AgentRoutinesPage v-else-if="page === 'routines'" :projects="projects" />
        <AgentWorkspaceHub v-else-if="page === 'tools'" :initial-project-id="currentProject?.id || ''" :initial-tab="hubTab" @open-conversation="openConversation" />
        <AgentCapabilities v-else-if="page === 'capabilities'" />
        <section v-else-if="page === 'task-detail'" class="agent-task-detail"><button type="button" class="agent-back-to-chat" @click="page = 'conversation'">返回对话</button><AgentTasksPage :conversation-id="context.selectedConversationId" @open-settings="showSettings()" /></section>
        <template v-else><div class="agent-conversation-heading"><span>{{ conversationTitle }}</span><div class="agent-inline-actions"><button v-if="currentTask" type="button" @click="page = 'task-detail'">查看任务</button><button v-if="mode === 'learn'" type="button" :aria-expanded="learningOpen" @click="learningOpen = !learningOpen"><BookOpen :size="15" />学习记录</button></div></div><AgentLearningPanel v-if="learningOpen" :project-id="currentProject?.id || ''" />
          <ChatPage :artifacts="artifacts" @preview-artifact="preview"><template #welcome><div class="agent-welcome"><img :src="heroImage" alt="首页图片" :class="{ circle: appearance.hero_shape === 'circle' }" /><h1>{{ greeting }}</h1><p>{{ sentence }}</p></div></template><template #execution-summary><AgentTaskProgress inline-chat v-if="currentTask && !context.sending && !['completed','cancelled'].includes(currentTask.status)" :key="currentTask.id" :task-id="currentTask.id" :task="currentTask" /></template>
            <template #task-controls><div v-if="currentTask && ['waiting_confirmation','waiting_user','waiting_jobs','waiting_terminal','paused','failed','budget_exhausted'].includes(currentTask.status)" class="agent-current-task-controls"><span>{{ taskStatusLabel(currentTask.status) }}</span><button type="button" @click="page = 'task-detail'">{{ currentTask.status === 'waiting_confirmation' ? '查看并确认操作' : '查看执行详情' }}</button></div></template>
            <template #composer-context><div class="agent-project-picker" @keydown.esc.stop="projectPicker = false">
              <button type="button" :aria-expanded="projectPicker" :disabled="context.sending || busy" aria-label="选择项目" @click="projectPicker = !projectPicker"><FolderOpen :size="14" /><span>{{ currentProject?.name || '选择项目' }}</span><ChevronDown :size="12" /></button>
              <div v-if="projectPicker" class="agent-project-popover" aria-label="项目列表">
                <label class="agent-search"><Search :size="14" /><input v-model="projectSearch" type="search" aria-label="搜索项目" placeholder="搜索项目" /></label>
                <div class="agent-project-options"><button v-for="project in projectOptions" :key="project.id" type="button" :class="{ selected: currentProject?.id === project.id }" :title="project.directory || project.name" :disabled="busy" @click="chooseProject(project.id)"><FolderOpen :size="15" /><span>{{ project.name }}</span><Check v-if="currentProject?.id === project.id" :size="15" /></button><p v-if="!projectOptions.length" class="agent-muted">{{ projectSearch ? '没有匹配的项目' : '还没有项目' }}</p></div>
                <footer><button type="button" :disabled="busy" @click="openProjectCreation"><Plus :size="15" />新建项目</button><button type="button" :disabled="busy" @click="chooseProject('')"><X :size="15" />不在项目中工作</button></footer>
              </div>
            </div></template>
            <template #composer-tools><button v-if="mode === 'learn'" type="button" @click="learningOpen = !learningOpen">学习记录与复习</button><button type="button" @click="hubTab = 'library'; navigate('tools')">项目资料</button><button type="button" @click="showSettings()">工具设置</button></template>
          </ChatPage>
        </template>
      </main>
      <aside v-if="right && !['routines','capabilities'].includes(page)" class="mio-agent-preview"><div class="agent-preview-resizer" role="separator" tabindex="0" aria-orientation="vertical" aria-label="调整文件预览宽度" :aria-valuenow="previewWidth" :aria-valuemin="previewWidthBounds().minimum" :aria-valuemax="previewWidthBounds().maximum" @keydown="onPreviewResizeKeydown" @pointerdown="startPreviewResize"></div><AgentFilesPreview :project-id="previewProjectId" :conversation-id="previewConversationId" :artifact="currentArtifact" :tasks="previewTasks" @close="right = false" @open-source="openSource" /></aside>
    </template>
  </section>
</template>
