<script setup>
import SelectField from './SelectField.vue'
import * as creationApi from '../services/creationApi.js'

import * as tasksApi from '../services/tasksApi.js'

import * as workspaceApi from '../services/workspaceApi.js'

import { mergeLoadedItems } from '../scrollCollections.js'
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { taskStatusLabel, canResumeTask } from '../agentTaskState.js'
import ArtifactLibrary from './ArtifactLibrary.vue'
import ProjectDirectoryForm from './ProjectDirectoryForm.vue'
import WorkspacePager from './WorkspacePager.vue'
import AgentSkills from './AgentSkills.vue'
import AgentTaskProgress from './AgentTaskProgress.vue'
import LearningExercise from './LearningExercise.vue'
import KnowledgeRetrievalSettings from './KnowledgeRetrievalSettings.vue'
import LearningReviewPanel from './LearningReviewPanel.vue'

const props = defineProps({ initialProjectId: { type: String, default: '' }, initialTab: { type: String, default: 'tasks' } })
const emit = defineEmits(['open-conversation'])
const overview = ref({ projects: [], tasks: [], templates: [], routines: [], browser_pending: [] })
const projectId = ref(props.initialProjectId), project = ref(null), tab = ref(['tasks', 'pending', 'results', 'library', 'learning', 'templates', 'skills', 'routines', 'browser'].includes(props.initialTab) ? props.initialTab : 'tasks'), error = ref(''), notice = ref(''), busy = ref(false)
const pending = ref({ items: [], total: 0, has_more: false }), pendingOffset = ref(0)
const taskPage = ref({ tasks: [], has_more: false }), taskOffset = ref(0)
const jobPage = ref({ jobs: [], has_more: false }), jobOffset = ref(0)
const collectionOffsets = reactive({ notes: 0, selections: 0, documents: 0, batches: 0, assemblies: 0 })
const sourcePage = ref({ sources: [], reply_citations: [], next_id: 0, has_more: false }), sourceCursors = ref([0])
const learningExercise=ref(null),learningExerciseVersion=ref(0)
const learning = ref({ sessions: [] }), learningSession = ref(null), learningReview = ref(null)
const learningForm = reactive({ title: '', goal: '', source_document_id: '' }), learningItemForm = reactive({ prompt: '', answer_key: '', source_ref: '' }), learningAnswers = reactive({})
const tabs = computed(() => [['tasks', '任务'], ['pending', `待处理${pending.value.total ? `（${pending.value.total}）` : ''}`], ['results', '成果'], ['library', '资料'], ['learning', '学习'], ['templates', '流程'], ['skills', '技能'], ['routines', '自动任务'], ['browser', '浏览器']])
const goal = ref(''), templateId = ref(''), feedback = reactive({}), expanded = ref(''), taskDetail = ref(null)
const query = ref(''), path = ref(''), hits = ref([]), compare = ref([])
const externalRoots = ref([]), externalNotes = ref([]), externalNote = ref(null)
const externalRootPath = ref(''), externalRootLabel = ref(''), externalLinkPath = ref(''), externalRootId = ref('')
const projectForm = reactive({ name: '', description: '', instructions: '', archived: false })
const showProjectForm = ref(false), editingProject = ref(false)
const templateForm = reactive({ name: '', goal: '', instructions: '' }), editingTemplate = ref('')
const routineForm = reactive({ name: '', kind: 'schedule', goal: '', mode: 'daily', time: '20:00', minutes: 60, weekday: 0, path: '', pattern: '*.pdf' })
const editingRoutine = ref('')
const noteForm = reactive({ title: '', content: '' })
const browserHosts = ref(''), allowPublic = ref(false), loginUrl = ref('https://')
const taskItems = computed(() => {
  const items = taskPage.value.tasks
  if (!taskDetail.value) return items
  return items.some(task => task.id === taskDetail.value.id)
    ? items.map(task => task.id === taskDetail.value.id ? taskDetail.value : task)
    : [taskDetail.value, ...items]
})
const sourceStatus = status => ({ current: '来源版本有效', stale: '原文已变化', blocked: '旧片段已阻止引用', unavailable: '来源不可用', unknown: '来源版本未知' }[status] || '来源待核对')
function currentSnapshotSource(snapshot) {
  return sourcePage.value.sources.find(source => source.document_id === snapshot.document_id && source.sha256 === snapshot.sha256)
}
function snapshotSourceUrl(snapshot) {
  const current = currentSnapshotSource(snapshot)
  return current?.status === 'current' ? current.url : ''
}
const routineItems = computed(() => overview.value.routines.filter(r => !projectId.value || r.project_id === projectId.value))
const templateItems = computed(() => overview.value.templates.filter(t => !projectId.value || !t.project_id || t.project_id === projectId.value))
const goalTemplateItems = computed(() => templateItems.value.filter(t => !t.project_id || t.project_id === projectId.value))
const previews = computed(() => {
  const items = (project.value?.artifacts || []).map(a => ({ ...a, key: a.id, artifact_id: a.id, media: a.mime_type?.split('/')[0] }))
  const identities = new Map(items.flatMap(item => {
    const keys = []
    if (item.artifact_id) keys.push(`artifact:${item.artifact_id}`)
    if (item.sha256) keys.push(`sha256:${item.sha256}`)
    return keys.map(key => [key, item])
  }))
  for (const job of jobPage.value.jobs || []) for (const [index, output] of (job.outputs || []).entries()) {
    const duplicate = (output.artifact_id && identities.get(`artifact:${output.artifact_id}`)) || (output.sha256 && identities.get(`sha256:${output.sha256}`))
    if (duplicate) {
      duplicate.job_id ||= job.id
      duplicate.output_index ??= index
      duplicate.task_id ||= job.task_id || output.task_id || ''
      continue
    }
    const item = { ...output, key: `${job.id}:${index}`, job_id: job.id, output_index: index, task_id: job.task_id || output.task_id || '', name: output.name || `${job.media_type === 'video' ? '视频' : '图片'} · ${index + 1}`, media: job.media_type || (output.mime_type || '').split('/')[0], created_at: job.created_at }
    items.push(item)
    if (item.artifact_id) identities.set(`artifact:${item.artifact_id}`, item)
    if (item.sha256) identities.set(`sha256:${item.sha256}`, item)
  }
  return items
})
const comparison = computed(() => previews.value.filter(a => compare.value.includes(a.key)))
const requestKeys = new Map()
function requestKey(payload) { const signature = JSON.stringify(payload); if (!requestKeys.has(signature)) requestKeys.set(signature, crypto.randomUUID()); return requestKeys.get(signature) }
const queueLabel = status => ({ queued: '排队中', pending: '尚未开始', created: '已创建', submitted: '已提交', running: '执行中', needs_confirmation: '等待确认', needs_attention: '部分项目需处理', unknown: '结果待核对', timed_out: '执行超时', cancel_requested: '正在取消', completed: '已完成', failed: '失败', paused: '已暂停', cancelled: '已取消' }[status] || taskStatusLabel(status))
const json = value => JSON.stringify(value, null, 2)
let refreshVersion = 0, disposed = false, polling = false, projectChosenLocally = false
async function refresh() {
  const version = ++refreshVersion, selectedProject = projectId.value, selectedTask = expanded.value
  const selectedOffset = pendingOffset.value, sourceAfter = sourceCursors.value.at(-1)
  const selectedTaskOffset = taskOffset.value, selectedJobOffset = jobOffset.value
  const current = () => !disposed && version === refreshVersion && selectedProject === projectId.value && selectedTask === expanded.value
  try {
    const nextOverview = await workspaceApi.loadWorkspace({ timeoutMs: 120000 })
    const nextProject = selectedProject ? await workspaceApi.loadWorkspaceProject(selectedProject, { timeoutMs: 120000 }) : null
    if (nextProject) for (const [kind, offset] of Object.entries(collectionOffsets)) if (offset) {
      const page = await workspaceApi.loadProjectHistoryByLimit50Offset(selectedProject, kind, offset, { timeoutMs: 120000 })
      nextProject[kind] = mergeLoadedItems(project.value?.[kind] || [],page[kind] || [],offset); nextProject[kind + '_has_more'] = page.has_more
    }
    const nextTaskPage = await workspaceApi.loadTasksByProjectIdLimit50Offset(selectedProject, selectedTaskOffset, { timeoutMs: 120000 })
    const nextJobPage = selectedProject ? await workspaceApi.loadProjectJobsByLimit50Offset(selectedProject, selectedJobOffset, { timeoutMs: 120000 }) : { jobs: [], has_more: false }
    const nextTask = selectedTask ? await tasksApi.loadWork(selectedTask, { timeoutMs: 120000 }) : null
    let nextPending = await workspaceApi.loadPendingByProjectIdOffsetLimit50(selectedProject, selectedOffset, { timeoutMs: 120000 })
    if (selectedOffset && !nextPending.items?.length) {
      const offset = Math.max(0, Math.floor((Math.max(1, nextPending.total || 0) - 1) / 50) * 50)
      nextPending = await workspaceApi.loadPendingByProjectIdOffsetLimit50(selectedProject, offset, { timeoutMs: 120000 })
    }
    const nextSources = selectedTask ? await tasksApi.loadWorkCitationsByAfterIdLimit50(selectedTask, sourceAfter, { timeoutMs: 120000 }) : null
    const nextLearning = selectedProject ? await workspaceApi.loadProjectLearning(selectedProject, { timeoutMs: 120000 }) : { sessions: [] }
    const nextExternalRoots = await workspaceApi.loadExternalRoots({ timeoutMs: 120000 })
    const nextExternalNotes = selectedProject ? await workspaceApi.loadProjectExternalNotes(selectedProject, { timeoutMs: 120000 }) : { notes: [] }
    if (!current()) return
    if (nextProject && project.value?.project.id !== selectedProject) {
      browserHosts.value = nextProject.browser.hosts.join('\n')
      allowPublic.value = nextProject.browser.allow_public
    }
    overview.value = nextOverview; project.value = nextProject; taskDetail.value = nextTask
    taskPage.value = { ...nextTaskPage,tasks:mergeLoadedItems(taskPage.value.tasks,nextTaskPage.tasks || [],selectedTaskOffset) }; jobPage.value = { ...nextJobPage,jobs:mergeLoadedItems(jobPage.value.jobs,nextJobPage.jobs || [],selectedJobOffset) }
    pending.value = { ...nextPending,items:mergeLoadedItems(pending.value.items,nextPending.items || [],selectedOffset) }
    pendingOffset.value = nextPending.offset || 0
    sourcePage.value = nextSources ? {...nextSources,sources:mergeLoadedItems(sourcePage.value.sources,nextSources.sources || [],sourceAfter)} : { sources: [], reply_citations: [], next_id: 0, has_more: false }
    learning.value = nextLearning
    externalRoots.value = nextExternalRoots.roots || []
    externalNotes.value = nextExternalNotes.notes || []
    if (externalNote.value) {
      const currentNote = externalNotes.value.find(item => item.id === externalNote.value.id)
      if (!currentNote || currentNote.conflict) externalNote.value = currentNote ? await workspaceApi.loadProjectExternalNote(selectedProject, currentNote.id, { timeoutMs: 120000 }) : null
    }
  } catch (cause) { if (current()) throw cause }
}
async function act(fn, message = '') {
  if (busy.value) return
  ++refreshVersion
  busy.value = true; error.value = ''; notice.value = ''
  try { await fn(); if (disposed) return; await refresh(); if (!disposed) notice.value = message }
  catch (cause) { if (!disposed) error.value = cause.message }
  finally { busy.value = false }
}
function resetProjectState() {
  ++refreshVersion
  project.value = null
  expanded.value = ''; taskDetail.value = null; hits.value = []; compare.value = []
  pending.value = { items: [], total: 0, has_more: false }; pendingOffset.value = 0
  taskPage.value = { tasks: [], has_more: false }; taskOffset.value = 0; jobPage.value = { jobs: [], has_more: false }; jobOffset.value = 0
  for (const kind of Object.keys(collectionOffsets)) collectionOffsets[kind] = 0
  sourcePage.value = { sources: [], reply_citations: [], next_id: 0, has_more: false }; sourceCursors.value = [0]
  learningSession.value = null; learningReview.value = null
  templateId.value = ''; editingTemplate.value = ''; editingRoutine.value = ''
  Object.assign(templateForm, { name: '', goal: '', instructions: '' })
  Object.assign(routineForm, { name: '', kind: 'schedule', goal: '', mode: 'daily', time: '20:00', minutes: 60, weekday: 0, path: '', pattern: '*.pdf' })
  showProjectForm.value = false; editingProject.value = false
  Object.assign(noteForm, { title: '', content: '' })
  goal.value = ''; query.value = ''; path.value = ''; browserHosts.value = ''; allowPublic.value = false; loginUrl.value = 'https://'
}
async function selectProject() {
  if (busy.value) return
  projectChosenLocally = true
  resetProjectState()
  await act(async () => {})
}
function newProject(edit = false) {
  if (busy.value || (edit && !project.value)) return
  projectChosenLocally = true
  editingProject.value = edit
  Object.assign(projectForm, edit ? { name: project.value.project.name, description: project.value.project.description, instructions: project.value.project.instructions, archived: false } : { name: '', description: '', instructions: '', archived: false })
  showProjectForm.value = true
}
async function saveProject(payload = {}) {
  const draft = { ...projectForm,...payload }, id = editingProject.value ? projectId.value : ''
  await act(async () => { const saved = await workspaceApi.saveProject(id, draft, { timeoutMs: 120000 }); if (projectId.value !== saved.id) resetProjectState(); projectId.value = saved.id; if (!id || json(projectForm) === json(draft)) showProjectForm.value = false }, '项目已保存')
}
async function removeProject() {
  const current = project.value?.project
  if (!current || busy.value || !window.confirm(`删除项目「${current.name}」？\n对话会移到「未归入项目」，电脑上的文件夹和文件会保留。`)) return
  await act(async () => {
    await workspaceApi.deleteProject(current.id, { timeoutMs: 120000 })
    projectId.value = ''; resetProjectState()
    window.dispatchEvent(new CustomEvent('mio-agent-workspace-changed'))
  }, '项目已删除，对话和本机文件已保留。')
}
async function startGoal() {
  const submittedGoal = goal.value, submittedTemplate = templateId.value, signature = [projectId.value, submittedTemplate, submittedGoal]
  await act(async () => { const task = await workspaceApi.submitGoals({ goal: submittedGoal, project_id: signature[0], template_id: submittedTemplate, request_key: requestKey(signature) }, { timeoutMs: 120000 }); expanded.value = task.id; taskDetail.value = null; sourcePage.value = { sources: [], reply_citations: [], next_id: 0, has_more: false }; sourceCursors.value = [0]; requestKeys.delete(JSON.stringify(signature)); if (goal.value === submittedGoal) goal.value = ''; tab.value = 'tasks' }, '任务已交给澪，完成后会保留在这里')
}
async function openTask(task) { if (busy.value) return; const closing = expanded.value === task.id; expanded.value = closing ? '' : task.id; taskDetail.value = null; sourcePage.value = { sources: [], reply_citations: [], next_id: 0, has_more: false }; sourceCursors.value = [0]; await act(async () => {}) }
async function openPendingTask(item) { if (busy.value) return; tab.value = 'tasks'; if (expanded.value !== item.task_id) await openTask({ id: item.task_id }); else await act(async () => {}) }
async function pendingPage(direction) { if (busy.value) return; pendingOffset.value = Math.max(0, pendingOffset.value + direction * 50); await act(async () => {}) }
async function changeTaskPage(direction) { if (busy.value) return; taskOffset.value = Math.max(0, taskOffset.value + direction * 50); expanded.value = ''; taskDetail.value = null; sourcePage.value = { sources: [], reply_citations: [], next_id: 0, has_more: false }; sourceCursors.value = [0]; await act(async () => {}) }
async function changeJobPage(direction) { if (busy.value) return; jobOffset.value = Math.max(0, jobOffset.value + direction * 50); compare.value = []; await act(async () => {}) }
async function changeCollectionPage(kind, direction) { if (busy.value) return; collectionOffsets[kind] = Math.max(0, collectionOffsets[kind] + direction * 50); await act(async () => {}) }
async function openArtifactTask(id) { if (busy.value) return; tab.value = 'tasks'; if (expanded.value !== id) await openTask({ id }); else await act(async () => {}) }
async function changeSourcePage(direction) { if (busy.value) return; if (direction > 0) sourceCursors.value.push(sourcePage.value.next_id); else if (sourceCursors.value.length > 1) sourceCursors.value.pop(); await act(async () => {}) }
async function searchLibrary() { hits.value = (await workspaceApi.searchProjectLibrary(projectId.value, query.value, { timeoutMs: 120000 })).results || [] }
async function authorizeExternalRoot() {
  await act(async () => { await workspaceApi.submitExternalRoots({ path: externalRootPath.value, label: externalRootLabel.value }, { timeoutMs: 120000 }); externalRootPath.value = ''; externalRootLabel.value = '' }, 'Markdown 目录已授权')
}
async function linkExternalNote() {
  await act(async () => { const note = await workspaceApi.submitProjectExternalNotes(projectId.value, { root_id: externalRootId.value, relative_path: externalLinkPath.value }, { timeoutMs: 120000 }); externalNote.value = note; externalLinkPath.value = '' }, '外部笔记已关联')
}
async function openExternalNote(note) {
  if (busy.value) return
  try { externalNote.value = await workspaceApi.loadProjectExternalNote(projectId.value, note.id, { timeoutMs: 120000 }) }
  catch (cause) { error.value = cause.message }
}
async function saveExternalNote() {
  const note = externalNote.value
  if (!note) return
  await act(async () => { externalNote.value = await workspaceApi.saveProjectExternalNotes(projectId.value, note.id, { content: note.content, expected_revision: note.revision, expected_sha256: note.current_sha256 }, { timeoutMs: 120000 }) }, '外部笔记已保存')
}
async function acceptExternalNoteChange() {
  const note = externalNote.value
  if (!note) return
  await act(async () => { externalNote.value = await workspaceApi.submitProjectExternalNotesRefresh(projectId.value, note.id, { timeoutMs: 120000 }) }, '已接受外部文件当前版本')
}
async function reindexSource(documentId) {
  await act(async () => { await workspaceApi.submitKnowledgeReindexByDocumentId(documentId, { timeoutMs: 120000 }); if (query.value.trim()) await searchLibrary() }, '索引已更新。给原任务补充“请重新检索资料”，即可使用新版本。')
}
async function follow(task) {
  const message = feedback[task.id], signature = [task.id, message]
  await act(async () => { await workspaceApi.submitTaskFollowUpByIdAndPayload(task.id, { message, request_key: requestKey(signature) }, { timeoutMs: 120000 }); requestKeys.delete(JSON.stringify(signature)); if (feedback[task.id] === message) feedback[task.id] = '' }, '补充要求已加入原任务')
}
async function upload(event) {
  const file = event.target.files?.[0]; if (!file) return
  await act(async () => { await workspaceApi.uploadProjectFile(projectId.value, file) }, '资料已保存并索引')
  event.target.value = ''
}
function editTemplate(template) { if (busy.value) return; editingTemplate.value = template.id; Object.assign(templateForm, { name: template.name, goal: template.goal, instructions: template.instructions }) }
async function useTemplate(template) {
  if (busy.value) return
  if (template.project_id && template.project_id !== projectId.value) { projectId.value = template.project_id; await selectProject() }
  templateId.value = template.id; tab.value = 'tasks'
}
async function saveTemplate() {
  const draft = { ...templateForm }, id = editingTemplate.value
  const owner = id ? overview.value.templates.find(item => item.id === id)?.project_id : projectId.value
  await act(async () => { await workspaceApi.saveTemplate(id, { ...draft, project_id: owner || '' }, { timeoutMs: 120000 }); if (json(templateForm) === json(draft)) { editingTemplate.value = ''; Object.assign(templateForm, { name: '', goal: '', instructions: '' }) } }, '流程已保存，澪可以根据实际结果调整步骤')
}
function editRoutine(r) { if (busy.value) return; editingRoutine.value = r.id; Object.assign(routineForm, { name: r.name, kind: r.kind, goal: r.goal, ...r.config }) }
async function saveRoutine() {
  const form = { ...routineForm }, id = editingRoutine.value, owner = projectId.value
  const config = form.kind === 'schedule' ? { mode: form.mode, time: form.time, minutes: Number(form.minutes), weekday: Number(form.weekday) } : form.kind === 'files' ? { path: form.path, pattern: form.pattern } : {}
  await act(async () => { await workspaceApi.saveRoutine(id, { name: form.name, project_id: owner, kind: form.kind, goal: form.goal, config, enabled: false }, { timeoutMs: 120000 }); if (json(routineForm) === json(form)) { editingRoutine.value = ''; routineForm.name = ''; routineForm.goal = '' } }, '已保存为停用状态，点击启用后开始运行')
}
async function selectOutput(item) { await act(() => workspaceApi.submitProjectSelections(projectId.value, { artifact_id: item.artifact_id || '', job_id: item.job_id || '', output_index: item.output_index || 0, task_id: item.task_id || '', note: '用户在成果面板选定' }, { timeoutMs: 120000 }), '已选定。继续给任务提要求时，澪会看到这项选择') }
async function saveNote() {
  const draft = { ...noteForm }, owner = projectId.value
  await act(async () => { await workspaceApi.submitProjectNotes(owner, draft, { timeoutMs: 120000 }); if (json(noteForm) === json(draft)) Object.assign(noteForm, { title: '', content: '' }) }, '记录已保存')
}
async function createLearningSession() {
  const draft = { ...learningForm }
  await act(async () => { const value = await workspaceApi.submitProjectLearning(projectId.value, draft, { timeoutMs: 120000 }); learningSession.value = value; Object.assign(learningForm, { title: '', goal: '', source_document_id: '' }) }, '学习会话已创建')
}
async function openLearning(value) { if(learningExercise.value && !await learningExercise.value.flush())return; learningExerciseVersion.value++; await act(async () => { learningSession.value = await workspaceApi.loadLearningSession(projectId.value, value.id, { timeoutMs: 120000 }) }) }
async function openLearningReview() { await act(async () => { learningReview.value = await workspaceApi.loadProjectLearningReviewByLimit50(projectId.value, { timeoutMs: 120000 }) }) }
async function addLearningItem() {
  const draft = { ...learningItemForm }, current = learningSession.value
  if (!current) return
  await act(async () => { await workspaceApi.submitProjectLearningItems(projectId.value, current.id, draft, { timeoutMs: 120000 }); learningSession.value = await workspaceApi.loadLearningSession(projectId.value, current.id, { timeoutMs: 120000 }); Object.assign(learningItemForm, { prompt: '', answer_key: '', source_ref: '' }) }, '练习已加入')
}
async function submitLearningAnswer(item) {
  const answer = learningAnswers[item.id]
  if (!answer?.trim()) return
  await act(async () => { await workspaceApi.submitProjectLearningItemAnswer(projectId.value, item.id, { answer }, { timeoutMs: 120000 }); learningSession.value = await workspaceApi.loadLearningSession(projectId.value, learningSession.value.id, { timeoutMs: 120000 }); learningAnswers[item.id] = '' }, '作答已保存')
}
watch(() => props.initialProjectId, async value => { if (!projectChosenLocally && value !== projectId.value) { projectId.value = value; resetProjectState(); await refresh() } })
watch(() => props.initialTab, value => { if (tabs.value.some(item => item[0] === value)) tab.value = value })
let timer
onMounted(async () => { await act(async () => {}); if (disposed) return; document.querySelector('.hub-upload input[type="file"]')?.setAttribute('accept', '.pdf,.docx,.xlsx,.txt,.md,.csv,.tsv,.json,.yaml,.yml'); timer = setInterval(async () => { if (busy.value || polling || document.hidden) return; polling = true; try { await refresh() } catch (cause) { if (!disposed) error.value = cause.message } finally { polling = false } }, 5000) })
onUnmounted(() => { disposed = true; ++refreshVersion; clearInterval(timer) })
</script>

<template>
  <section class="workspace-hub" :aria-busy="busy">
    <header class="hub-heading"><div><span class="hub-eyebrow">MIO WORKSPACE</span><h1>项目工作台</h1><p>目标交给澪，进度与成果留在这里。</p></div><button :disabled="busy" @click="act(async () => {})">刷新</button></header>
    <div class="hub-project-bar"><label>当前项目<SelectField v-model="projectId" aria-label="当前项目" :disabled="busy" @change="selectProject"><option value="">全部任务</option><option v-for="p in overview.projects" :key="p.id" :value="p.id">{{ p.name }}</option></SelectField></label><button :disabled="busy" @click="newProject()">新建项目</button><button v-if="project" :disabled="busy" @click="newProject(true)">项目设置</button><button v-if="project" :disabled="busy" @click="removeProject">删除项目</button></div>
    <p v-if="error" role="alert" class="hub-error">{{ error }}</p><p v-if="notice" role="status" class="hub-notice">{{ notice }}</p>
    <ProjectDirectoryForm v-if="showProjectForm && !editingProject" :busy="busy" @submit="saveProject" @cancel="showProjectForm = false" /><form v-if="showProjectForm && editingProject" class="hub-panel hub-form" @submit.prevent="saveProject"><h2>{{ editingProject ? '编辑项目' : '新建项目' }}</h2><label>项目名称<input v-model="projectForm.name" required maxlength="160" /></label><label>简介<textarea v-model="projectForm.description" rows="2" /></label><label>长期要求与偏好<textarea v-model="projectForm.instructions" rows="3" placeholder="例如：保留来源、偏好的画面风格、交付格式。澪会自主安排具体步骤。" /></label><div class="hub-actions"><button class="hub-primary" :disabled="busy">保存项目</button><button type="button" @click="showProjectForm = false">取消</button></div></form>
    <nav class="hub-tabs" aria-label="项目功能"><button v-for="[id, label] in tabs" :key="id" :aria-current="tab === id ? 'page' : undefined" @click="tab = id">{{ label }}</button></nav>
    <section v-if="tab === 'library' && project" class="hub-panel hub-form hub-ocr-status" aria-label="资料文字识别状态"><h2>资料文字识别状态</h2><p class="hub-muted">扫描 PDF 会记录 OCR 是否使用、失败原因或未识别到文字。</p><div v-for="doc in project.documents" :key="`ocr-${doc.id}`" class="hub-queue-item"><span><strong>{{ doc.name }}</strong><small>{{ ({ used: '已使用 OCR', failed: 'OCR 失败', empty: 'OCR 未识别到文字', not_needed: '无需 OCR' }[doc.ocr_status] || '状态未知') }}</small><small v-if="doc.ocr_error" class="hub-error">{{ doc.ocr_error }}</small></span></div></section>
    <AgentSkills v-if="tab === 'skills'" />
    <LearningReviewPanel v-if="tab === 'learning' && projectId" :project-id="projectId" />

    <div v-if="tab === 'tasks'" class="hub-stack">
      <form class="hub-panel hub-form" @submit.prevent="startGoal"><h2>让澪做件事</h2><textarea v-model="goal" aria-label="任务目标" rows="3" placeholder="描述你想得到的结果，也可以随时补充要求。" :required="!templateId" /><div class="hub-actions"><SelectField v-model="templateId" aria-label="选择可复用流程"><option value="">由澪自主规划</option><option v-for="t in goalTemplateItems" :key="t.id" :value="t.id">{{ t.name }}</option></SelectField><button class="hub-primary" :disabled="busy || (!goal.trim() && !templateId)">开始任务</button></div></form>
      <button v-if="pending.total" class="hub-panel hub-task-title" @click="tab = 'pending'">{{ pending.total }} 项待处理与后台等待，点击查看</button>
      <p v-if="!taskItems.length" class="hub-empty">还没有任务。写下目标，澪就可以开始。</p>
      <article v-for="task in taskItems" :key="task.id" class="hub-panel"><button class="hub-task-title" @click="openTask(task)"><strong>{{ task.snapshot?.goal || task.original_goal }}</strong><span class="hub-badge">{{ taskStatusLabel(task.status) }}</span></button><p v-if="task.snapshot?.next_step" class="hub-muted">下一步：{{ task.snapshot.next_step }}</p><p v-if="task.snapshot?.blocker" class="hub-break">{{ task.snapshot.blocker }}</p>
        <template v-if="expanded === task.id"><ol v-if="task.snapshot?.plan?.length"><li v-for="step in task.snapshot.plan" :key="step">{{ step }}</li></ol><p v-if="task.last_response || taskDetail?.last_response" class="hub-response">{{ task.last_response || taskDetail?.last_response }}</p>
          <div class="hub-actions"><button v-if="!['completed','cancelled','paused'].includes(task.status)" :disabled="busy" @click="act(() => tasksApi.submitWorkPause(task.id, { timeoutMs: 120000 }))">暂停</button><button v-if="canResumeTask(task)" :disabled="busy" @click="act(() => tasksApi.submitWorkResume(task.id, { timeoutMs: 120000 }))">继续</button><button v-if="!['completed','cancelled'].includes(task.status)" :disabled="busy" @click="act(() => tasksApi.submitWorkCancel(task.id, { timeoutMs: 120000 }))">取消任务</button><button @click="emit('open-conversation', task.conversation_id)">打开对话</button></div>
          <AgentTaskProgress :task-id="task.id" :task="task" />
          <form v-if="task.status !== 'cancelled'" class="hub-form hub-follow" @submit.prevent="follow(task)"><label>继续提要求<textarea v-model="feedback[task.id]" rows="2" placeholder="例如：选用第二张，再做成视频；或说明需要调整的地方。" required /></label><p class="hub-muted">补充会先保存，在当前动作结束后的下一次规划读取；不会重复提交任务。</p><button :disabled="busy || !feedback[task.id]?.trim()">发送补充</button></form>
          <section v-if="sourcePage.reply_citations?.length" class="hub-sources" aria-label="最终回复引用"><h3>最终回复引用</h3><p class="hub-muted">引用保存的是回复生成时的来源快照；原文变化后仍保留当时版本。</p><article v-for="reply in sourcePage.reply_citations" :key="reply.message_id" class="hub-observation"><strong>{{ reply.created_at }}</strong><p v-for="sentence in reply.sentences" :key="sentence.index" class="hub-response">{{ sentence.index + 1 }}. {{ sentence.text }}</p><details v-if="reply.source_snapshot?.length"><summary>查看这次回复使用的来源</summary><article v-for="source in reply.source_snapshot" :key="source.key" class="hub-citation"><p><a v-if="snapshotSourceUrl(source)" :href="snapshotSourceUrl(source)" target="_blank" rel="noopener">{{ source.name }}</a><span v-else>{{ source.name }}</span> · 生成时：{{ sourceStatus(source.status) }} · 当前：{{ sourceStatus(currentSnapshotSource(source)?.status || 'unknown') }} · {{ source.sha256 || '未记录版本' }}</p><small v-if="source.page || source.line" class="hub-muted">{{ source.page ? `第 ${source.page} 页` : `第 ${source.line} 行起` }}</small><p v-if="source.excerpt" class="hub-excerpt">{{ source.excerpt }}</p></article></details></article></section>
          <section v-if="sourcePage.sources.length || sourcePage.has_more || sourceCursors.length > 1" class="hub-sources" aria-label="任务资料来源"><h3>任务资料来源</h3><p class="hub-muted">这里列出澪实际检索的资料；来源有效不代表内容结论已获确认。</p><article v-for="source in sourcePage.sources" :key="source.key" class="hub-observation"><strong>{{ source.name }}</strong><span class="hub-badge">{{ sourceStatus(source.status) }}</span><p v-if="source.page || source.line">{{ source.page ? `第 ${source.page} 页` : `第 ${source.line} 行起` }}</p><details><summary>查看来源版本</summary><small class="hub-break">SHA-256：{{ source.sha256 || '未记录' }}</small></details><p v-if="source.reason">{{ source.reason }}</p><div class="hub-actions"><a v-if="source.status === 'current' && source.url" :href="source.url" target="_blank" rel="noopener">查看原文</a><button v-if="source.can_reindex && ['stale','blocked'].includes(source.status)" :disabled="busy" @click="reindexSource(source.document_id)">更新索引</button></div></article><div class="hub-actions"><WorkspacePager label="来源" :has-more="sourcePage.has_more" :busy="busy" @page="changeSourcePage(1)" /></div></section>
          <details v-if="taskDetail?.observations?.length"><summary>查看执行记录</summary><div v-for="(observation, index) in taskDetail.observations" :key="index" class="hub-observation"><strong>{{ observation.tool_name || observation.name }} · {{ observation.status }}</strong><pre>{{ json(observation.result) }}</pre><button v-if="observation.action_id && observation.status === 'needs_confirmation'" :disabled="busy" @click="act(() => tasksApi.submitWorkActionsApprove(task.id, observation.action_id, { timeoutMs: 120000 }))">确认这项工具操作</button></div></details>
        </template>
      </article>
      <WorkspacePager label="任务" :has-more="taskPage.has_more" :busy="busy" @page="changeTaskPage(1)" />
    </div>

    <div v-else-if="tab === 'pending'" class="hub-stack"><p class="hub-muted">需你处理的事项优先显示。后台等待会自动更新，无需重复发送任务。</p><p v-if="!pending.items.length" class="hub-empty">当前没有待处理事项。</p><article v-for="item in pending.items" :key="item.id" class="hub-panel" :class="{ 'hub-confirm': item.requires_action }"><div class="hub-actions"><h3>{{ item.kind === 'browser' ? '浏览器操作待确认' : taskStatusLabel(item.status) }}</h3><span v-if="!item.requires_action" class="hub-badge">正在等待后台</span></div><p class="hub-break">{{ item.title }}</p><small v-if="item.project_name">{{ item.project_name }}</small><p v-if="item.reason" class="hub-break">{{ item.reason }}</p><template v-if="item.kind === 'browser'"><p class="hub-break">{{ item.url }}</p><pre>{{ json(item.action) }}</pre></template><div class="hub-actions"><button :disabled="busy" @click="openPendingTask(item)">查看任务 / 补充要求</button><template v-if="item.kind === 'browser'"><button :disabled="busy" @click="act(() => workspaceApi.submitBrowserConfirm(item.confirmation_id, { approve: false }, { timeoutMs: 120000 }))">拒绝</button><button class="hub-primary" :disabled="busy" @click="act(() => workspaceApi.submitBrowserConfirm(item.confirmation_id, { approve: true }, { timeoutMs: 120000 }))">确认执行此操作</button></template></div></article><WorkspacePager label="待处理" :has-more="pending.has_more" :busy="busy" @page="pendingPage(1)" /></div>

    <template v-else-if="tab === 'results'"><ArtifactLibrary :project-id="projectId" @changed="act(async () => {})" @open-task="openArtifactTask" /><p v-if="!project" class="hub-muted">选择项目，还可以预览生成版本与处理队列。</p><div v-else class="hub-stack"><div v-if="comparison.length" class="hub-panel"><div class="hub-actions"><h2>版本对比</h2><button @click="compare = []">清除对比</button></div><div class="hub-gallery"><figure v-for="item in comparison" :key="item.key"><img v-if="item.media === 'image'" :src="item.url" :alt="item.name" /><video v-else-if="item.media === 'video'" :src="item.url" controls preload="metadata" /><figcaption>{{ item.name }} · {{ item.created_at }}</figcaption></figure></div></div><div class="hub-gallery"><article v-for="item in previews" :key="item.key" class="hub-panel"><img v-if="item.media === 'image'" :src="item.url" :alt="item.name" loading="lazy" /><video v-else-if="item.media === 'video'" :src="item.url" controls preload="metadata" /><p class="hub-break">{{ item.name }}</p><div class="hub-actions"><a :href="item.url" target="_blank" rel="noopener">查看 / 下载</a><button :disabled="busy" @click="selectOutput(item)">选用</button><label><input v-model="compare" type="checkbox" :value="item.key" /> 对比</label></div></article></div><p v-if="!previews.length" class="hub-empty">生成结果和文件成果会保留在这里。</p>
      <WorkspacePager label="生成记录" :has-more="jobPage.has_more" :busy="busy" @page="changeJobPage(1)" />
      <article v-for="batch in project.batches" :key="batch.id" class="hub-panel"><div class="hub-actions"><h3>{{ batch.name }} · {{ queueLabel(batch.status) }}</h3><button :disabled="busy" @click="act(() => workspaceApi.submitBatchControl(batch.id, { action: batch.status === 'paused' ? 'resume' : 'pause' }, { timeoutMs: 120000 }))">{{ batch.status === 'paused' ? '继续队列' : '暂停队列' }}</button><button :disabled="busy" @click="act(() => workspaceApi.submitBatchControl(batch.id, { action: 'cancel' }, { timeoutMs: 120000 }))">取消队列</button></div><div v-for="item in batch.items" :key="item.ordinal" class="hub-queue-item"><span>{{ item.ordinal + 1 }} · {{ queueLabel(item.job?.status || item.status) }} {{ item.error }}</span><button v-if="item.job?.status === 'needs_confirmation'" :disabled="busy" @click="act(() => creationApi.submitJobConfirm(item.job_id, { timeoutMs: 120000 }))">确认生成</button><button v-if="['completed','failed','cancelled','timed_out'].includes(item.job?.status || item.status)" :disabled="busy" @click="act(() => workspaceApi.submitBatchControl(batch.id, { action: 'retry', index: item.ordinal }, { timeoutMs: 120000 }))">单独重做</button></div></article>
      <WorkspacePager label="队列" :offset="collectionOffsets.batches" :has-more="project.batches_has_more" :busy="busy" @page="changeCollectionPage('batches', $event)" />
      <article v-for="assembly in project.assemblies" :key="assembly.id" class="hub-panel"><h3>{{ assembly.spec.name }} · {{ queueLabel(assembly.status) }}</h3><p v-if="assembly.error" class="hub-error">{{ assembly.error }}</p><a v-if="assembly.artifact" :href="assembly.artifact.url">查看成片</a></article><WorkspacePager label="成片" :offset="collectionOffsets.assemblies" :has-more="project.assemblies_has_more" :busy="busy" @page="changeCollectionPage('assemblies', $event)" /><section class="hub-panel"><h3>已选用记录</h3><p v-if="!project.selections?.length" class="hub-muted">还没有选用记录</p><article v-for="selection in project.selections" :key="selection.id" class="hub-queue-item"><span>{{ selection.note || selection.created_at }}</span><a v-if="selection.artifact_id" :href="`/api/artifacts/${selection.artifact_id}/download`">查看选用成果</a><button v-if="selection.task_id" :disabled="busy" @click="openArtifactTask(selection.task_id)">查看来源任务</button></article><WorkspacePager label="选用" :offset="collectionOffsets.selections" :has-more="project.selections_has_more" :busy="busy" @page="changeCollectionPage('selections', $event)" /></section></div></template>

    <template v-else-if="tab === 'library'"><p v-if="!project" class="hub-empty">选择项目，添加资料与长期记录。</p><div v-else class="hub-stack"><section class="hub-panel hub-form"><h2>项目资料</h2><KnowledgeRetrievalSettings /><p class="hub-muted">PDF、DOCX、Markdown 和文本；检索保留来源页码或行号。扫描件需先识别文字。</p><label class="hub-upload">上传资料<input type="file" accept=".pdf,.docx,.xlsx,.txt,.md,.csv,.tsv,.json,.yaml,.yml" :disabled="busy" @change="upload" /></label><form class="hub-actions" @submit.prevent="act(() => workspaceApi.submitProjectKnowledgeByProjectIdAndPayload(projectId, { path }, { timeoutMs: 120000 }), '索引已更新')"><input v-model="path" aria-label="本机资料路径" placeholder="已授权目录中的文件路径" required /><button :disabled="busy">索引本机文件</button></form><div v-for="doc in project.documents" :key="doc.id" class="hub-queue-item"><a :href="`/api/workspace/knowledge/${doc.id}/source`" target="_blank" rel="noopener">{{ doc.name }}</a><small>{{ doc.indexed_at }}</small><button :disabled="busy" @click="act(() => workspaceApi.submitProjectKnowledgeByProjectIdAndPayload(projectId, { path: doc.path }, { timeoutMs: 120000 }))">更新索引</button></div><WorkspacePager label="资料" :offset="collectionOffsets.documents" :has-more="project.documents_has_more" :busy="busy" @page="changeCollectionPage('documents', $event)" /></section><form class="hub-panel hub-form" @submit.prevent="act(searchLibrary)"><label>检索项目资料<input v-model="query" required placeholder="关键词或问题" /></label><button :disabled="busy">搜索原文</button><article v-for="hit in hits" :key="hit.id"><a :href="hit.url" target="_blank" rel="noopener">{{ hit.citation }}</a><strong v-if="hit.stale"> · 原文件已更新，Agent 已阻止引用旧片段</strong><button v-if="hit.stale" type="button" :disabled="busy" @click="reindexSource(hit.document_id)">更新这份资料索引</button><p class="hub-response">{{ hit.content }}</p></article></form><section class="hub-panel hub-form"><h2>授权 Markdown / Obsidian 目录</h2><p class="hub-muted">只访问你明确授权的普通目录。保存前会核对 revision 和 SHA-256，目录中的外部修改不会被静默覆盖。</p><form class="hub-actions" @submit.prevent="authorizeExternalRoot"><input v-model="externalRootPath" placeholder="目录路径，例如 D:\\Notes" required /><input v-model="externalRootLabel" placeholder="名称（可选）" /><button :disabled="busy">授权目录</button></form><div v-for="root in externalRoots" :key="root.id" class="hub-queue-item"><span>{{ root.label || root.path }}<small v-if="!root.available" class="hub-error">目录不可用</small></span><template v-if="!mobile && root.available"><SelectField v-model="externalRootId" aria-label="选择外部笔记目录"><option value="">选择目录</option><option :value="root.id">{{ root.label || root.path }}</option></SelectField></template></div><form v-if="!mobile && externalRoots.some(root => root.available)" class="hub-actions" @submit.prevent="linkExternalNote"><SelectField v-model="externalRootId" required aria-label="外部笔记目录"><option value="">选择目录</option><option v-for="root in externalRoots.filter(root => root.available)" :key="root.id" :value="root.id">{{ root.label || root.path }}</option></SelectField><input v-model="externalLinkPath" placeholder="相对路径，例如 daily/2026-10-05.md" required /><button :disabled="busy">关联 Markdown</button></form><div v-for="note in externalNotes" :key="note.id" class="hub-queue-item"><span><strong>{{ note.title || note.relative_path }}</strong><small>{{ note.relative_path }} · revision {{ note.revision }}</small><small v-if="note.conflict" class="hub-error">外部文件已变化，请重新读取</small></span><button :disabled="busy" @click="openExternalNote(note)">打开</button></div><section v-if="externalNote" class="hub-observation"><strong>{{ externalNote.title || externalNote.relative_path }}</strong><small>来源：{{ externalNote.path }} · SHA-256 {{ externalNote.current_sha256 }}</small><p v-if="externalNote.conflict" class="hub-error">检测到目录中的修改。先接受当前外部版本，再编辑和保存。</p><textarea v-model="externalNote.content" rows="12" :disabled="externalNote.conflict" /><div class="hub-actions"><button v-if="externalNote.conflict" :disabled="busy" @click="acceptExternalNoteChange">接受当前外部版本</button><button :disabled="busy || externalNote.conflict" @click="saveExternalNote">保存到外部 Markdown</button></div></section></section><section class="hub-panel hub-form"><h2>项目记录</h2><article v-for="note in project.notes" :key="note.id"><strong>{{ note.title }}</strong><small> · {{ note.source === 'model_note' ? '澪的记录' : '你的记录' }}</small><p class="hub-response">{{ note.content }}</p></article><WorkspacePager label="记录" :offset="collectionOffsets.notes" :has-more="project.notes_has_more" :busy="busy" @page="changeCollectionPage('notes', $event)" /><form class="hub-form" @submit.prevent="saveNote"><input v-model="noteForm.title" aria-label="记录标题" placeholder="记录标题" required /><textarea v-model="noteForm.content" aria-label="记录内容" rows="3" placeholder="确定的要求、决定或进展" required /><button :disabled="busy">添加记录</button></form></section></div></template>

    <template v-else-if="tab === 'learning'"><p v-if="!project" class="hub-empty">选择项目，建立学习会话。</p><div v-else class="hub-stack"><form class="hub-panel hub-form" @submit.prevent="createLearningSession"><h2>新建学习会话</h2><label>标题<input v-model="learningForm.title" required /></label><label>目标<textarea v-model="learningForm.goal" rows="2" /></label><label>绑定资料<SelectField v-model="learningForm.source_document_id"><option value="">不绑定</option><option v-for="doc in project.documents" :key="doc.id" :value="doc.id">{{ doc.name }}</option></SelectField></label><button :disabled="busy">建立会话</button></form><section class="hub-panel"><h2>学习进度</h2><div class="hub-queue-item" v-for="item in learning.sessions" :key="item.id"><span>{{ item.title }} · {{ item.progress.answered }}/{{ item.progress.total }}，平均 {{ Math.round(item.progress.average_score * 100) }}%</span><button :disabled="busy" @click="openLearning(item)">打开</button></div><p v-if="!learning.sessions.length" class="hub-muted">还没有学习会话。</p></section><section v-if="learningSession" class="hub-panel hub-form"><h2>{{ learningSession.title }}</h2><p class="hub-muted">{{ learningSession.goal || '按题目作答，结果会跨会话保留。' }} · 已答 {{ learningSession.progress.answered }}/{{ learningSession.progress.total }}</p><LearningExercise ref="learningExercise" :key="`${projectId}:${learningSession.id}:${learningExerciseVersion}`" :project-id="projectId" :session="learningSession" @changed="openLearning(learningSession)" /><form class="hub-form" @submit.prevent="addLearningItem"><h3>添加练习</h3><textarea v-model="learningItemForm.prompt" rows="2" required placeholder="题目" /><input v-model="learningItemForm.answer_key" placeholder="可选核对要点，用逗号分隔" /><input v-model="learningItemForm.source_ref" placeholder="可选来源页码或笔记位置" /><button :disabled="busy">加入练习</button></form></section></div></template>

    <div v-else-if="tab === 'templates'" class="hub-stack"><p class="hub-muted">保存目标和偏好。每次执行由当前模型安排步骤，可根据新结果调整。</p><form class="hub-panel hub-form" @submit.prevent="saveTemplate"><h2>{{ editingTemplate ? '编辑流程' : '保存可复用流程' }}</h2><label>名称<input v-model="templateForm.name" required /></label><label>目标<textarea v-model="templateForm.goal" rows="2" required placeholder="例如：根据本次主题，制作三版封面供我选择，再把选定方案做成视频。" /></label><label>参考指导<textarea v-model="templateForm.instructions" rows="3" placeholder="画面偏好、需我选择的节点、交付标准。无需固定每一步。" /></label><button :disabled="busy">保存流程</button></form><article v-for="template in templateItems" :key="template.id" class="hub-panel"><h3>{{ template.name }}</h3><p class="hub-response">{{ template.goal }}</p><p class="hub-muted hub-response">{{ template.instructions }}</p><div class="hub-actions"><button  :disabled="busy" @click="useTemplate(template)">用于新任务</button><button @click="editTemplate(template)">编辑</button></div></article></div>

    <template v-else-if="tab === 'routines'"><p v-if="!project" class="hub-empty">选择项目，设置任务在何时自动开始。</p><div v-else class="hub-stack"><form class="hub-panel hub-form" @submit.prevent="saveRoutine"><h2>{{ editingRoutine ? '编辑自动任务' : '新的自动任务' }}</h2><label>名称<input v-model="routineForm.name" required /></label><label>触发条件<SelectField v-model="routineForm.kind"><option value="schedule">按时间</option><option value="files">文件新增或变化</option><option value="task_completed">项目任务完成</option><option value="generation_completed">项目生成完成</option></SelectField></label><div v-if="routineForm.kind === 'schedule'" class="hub-actions"><SelectField v-model="routineForm.mode" aria-label="时间周期"><option value="daily">每天</option><option value="weekly">每周</option><option value="interval">间隔</option></SelectField><label v-if="routineForm.mode === 'interval'">分钟<input v-model="routineForm.minutes" type="number" min="1" required /></label><input v-else v-model="routineForm.time" type="time" aria-label="执行时间" required /><SelectField v-if="routineForm.mode === 'weekly'" v-model="routineForm.weekday" aria-label="星期"><option v-for="(day, i) in ['一','二','三','四','五','六','日']" :key="i" :value="i">星期{{ day }}</option></SelectField></div><template v-if="routineForm.kind === 'files'"><label>电脑上的已授权目录<input v-model="routineForm.path" required placeholder="D:\资料\项目" /></label><label>文件匹配<input v-model="routineForm.pattern" required placeholder="*.pdf" /></label></template><label>触发后要完成什么<textarea v-model="routineForm.goal" rows="3" required /></label><p class="hub-muted">保存后需手动启用。电脑运行时检查，离线期间错过的定时触发合并为一次；自动任务不会循环触发同类完成任务。</p><button :disabled="busy">保存为草稿</button></form><article v-for="routine in routineItems" :key="routine.id" class="hub-panel"><h3>{{ routine.name }} · {{ routine.enabled ? '已启用' : '未启用' }}</h3><p>{{ routine.goal }}</p><small v-if="routine.next_run_at">下次：{{ routine.next_run_at }}</small><p v-if="routine.last_error" class="hub-error">{{ routine.last_error }}</p><div class="hub-actions"><button :disabled="busy" @click="act(() => workspaceApi.submitRoutineEnabled(routine.id, { enabled: !routine.enabled }, { timeoutMs: 120000 }))">{{ routine.enabled ? '停用' : '启用' }}</button><button @click="editRoutine(routine)">编辑</button></div></article></div></template>

    <template v-else-if="tab === 'browser'"><p v-if="!project" class="hub-empty">选择项目，配置它的专用浏览器。</p><section v-else class="hub-panel hub-form"><h2>项目专用浏览器</h2><p>让澪读取网页、操作页面和保存下载文件。登录状态按项目保留，对外提交先展示操作内容，等你确认。</p><label><input v-model="allowPublic" type="checkbox" /> 允许访问公开网站</label><label>或指定域名，每行一个<textarea v-model="browserHosts" rows="4" placeholder="example.com" /></label><button :disabled="busy" @click="act(() => workspaceApi.saveProjectBrowserByProjectIdAndPayload(projectId, { hosts: browserHosts.split(/\s+/).filter(Boolean), allow_public: allowPublic }, { timeoutMs: 120000 }), '浏览器权限已保存')">保存访问范围</button><form class="hub-form" @submit.prevent="act(() => workspaceApi.submitProjectBrowserLoginByProjectIdAndPayload(projectId, { url: loginUrl }, { timeoutMs: 120000 }), '已在电脑打开专用浏览器，请手动登录，完成后可关掉该页面')"><label>需要登录的网站<input v-model="loginUrl" type="url" required /></label><button :disabled="busy">在电脑打开登录窗口</button></form><p class="hub-muted">使用独立 Edge 会话；不读取日常浏览器。浏览器关闭或页面内容变化后，旧的提交确认会失效。</p></section></template>

  </section>
</template>

<style scoped>
.workspace-hub{max-width:1160px;width:100%;height:100%;min-height:0;margin:0 auto;padding:28px;box-sizing:border-box;color:var(--ink);font-size:14px;line-height:1.6;overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;touch-action:manipulation;scrollbar-gutter:stable;-webkit-overflow-scrolling:touch}.hub-heading,.hub-project-bar,.hub-actions,.hub-queue-item{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.hub-heading{justify-content:space-between;margin-bottom:20px}.hub-heading h1{margin:4px 0;font-size:27px}.hub-heading p{margin:0;color:var(--muted)}.hub-eyebrow{font-size:12px;letter-spacing:.14em;color:var(--accent)}.hub-project-bar{margin-bottom:16px}.hub-project-bar label{display:flex;align-items:center;gap:10px}.workspace-hub button,.workspace-hub select,.workspace-hub input,.workspace-hub textarea{font:inherit;color:inherit;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:9px 12px;box-sizing:border-box;max-width:100%}.workspace-hub button{cursor:pointer;min-height:40px}.workspace-hub button:disabled{opacity:.5;cursor:default}.workspace-hub button:hover:not(:disabled){border-color:var(--accent)}.workspace-hub textarea{resize:vertical;min-height:65px;width:100%}.workspace-hub a{color:var(--accent);text-decoration:underline;text-underline-offset:3px}.hub-tabs{display:flex;gap:6px;overflow:auto;border-bottom:1px solid var(--line);padding:0 0 10px;margin:20px 0}.hub-tabs button{white-space:nowrap;border-color:transparent;background:transparent}.hub-tabs [aria-current=page]{color:var(--accent);background:var(--surface);border-color:var(--line);font-weight:650}.hub-panel{border:1px solid var(--line);border-radius:13px;background:var(--surface);padding:20px;min-width:0}.hub-panel h2,.hub-panel h3{margin:0 0 10px;font-size:17px}.hub-stack,.hub-form{display:grid;gap:16px}.hub-form label{display:grid;gap:6px}.hub-form label:has([type=checkbox]){display:flex;align-items:center}.hub-form input:not([type=checkbox]),.hub-form select{width:100%}.hub-form button{justify-self:start}.hub-actions input:not([type=checkbox]){flex:1;min-width:160px}.hub-actions select{width:auto;max-width:100%}.hub-primary{background:var(--accent)!important;color:var(--accent-contrast)!important;border-color:transparent!important}.hub-task-title{display:flex;justify-content:space-between;gap:16px;text-align:left;width:100%;padding:0!important;border:0!important;background:none!important}.hub-task-title strong{overflow-wrap:anywhere}.hub-badge{font-size:12px;white-space:nowrap;color:var(--muted)}.hub-follow{margin:18px 0;border-top:1px solid var(--line);padding-top:16px}.hub-empty{text-align:center;color:var(--muted);padding:36px 12px}.hub-muted{color:var(--muted)}.hub-error{color:var(--danger);padding:10px 0;overflow-wrap:anywhere}.hub-notice{color:var(--accent)}.hub-response{white-space:pre-wrap;overflow-wrap:anywhere}.hub-break{overflow-wrap:anywhere}.hub-gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr));gap:16px}.hub-gallery figure{margin:0;min-width:0}.hub-gallery img,.hub-gallery video{width:100%;height:250px;object-fit:contain;background:var(--bg);border-radius:8px}.hub-gallery figcaption{font-size:12px;overflow-wrap:anywhere;margin-top:8px}.hub-gallery label{display:flex;align-items:center;gap:4px}.hub-queue-item{justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.hub-queue-item span{flex:1;min-width:140px}.hub-observation{padding:14px 0;border-bottom:1px solid var(--line)}.workspace-hub pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;font:12px/1.6 monospace}.hub-confirm{border-color:var(--accent)}.hub-upload input{padding:6px}.hub-qr{width:180px;height:180px}.hub-pair-code{font-size:32px;letter-spacing:5px;margin:0}.hub-pair-code small{display:block;font-size:12px;letter-spacing:0;color:var(--muted)}.workspace-hub details summary{cursor:pointer}.workspace-hub :focus-visible{outline:2px solid var(--accent);outline-offset:3px}
@media(max-width:640px){.workspace-hub{padding:18px 14px}.hub-heading h1{font-size:23px}.hub-panel{padding:16px}.hub-project-bar label{width:100%}.hub-project-bar select{flex:1;min-width:0}.hub-task-title{flex-direction:column;gap:4px}.hub-tabs{gap:2px}.hub-tabs button{padding:8px 10px}.hub-gallery{grid-template-columns:1fr}.hub-actions{gap:8px}.hub-form input,.hub-form select,.hub-form textarea{font-size:16px}.hub-heading{gap:8px}.hub-queue-item{align-items:flex-start}.hub-queue-item small{width:100%}}
.hub-sources .hub-badge{margin-left:10px}.hub-sources details{margin:8px 0}.hub-citation{padding:8px 0;border-top:1px solid var(--line)}.hub-excerpt{white-space:pre-wrap;background:var(--bg);border-left:3px solid var(--accent);padding:10px 12px;margin:6px 0;overflow-wrap:anywhere}.hub-ocr-status{margin-bottom:16px}.hub-ocr-status .hub-queue-item{align-items:flex-start}.hub-ocr-status small{display:block;color:var(--muted)}.hub-events{margin:10px 0 0;padding-left:20px}.hub-events li{padding:8px 0;border-bottom:1px solid var(--line)}.hub-events strong{margin-right:8px}.hub-events small{color:var(--muted)}.hub-events pre{max-height:180px;margin:6px 0 0}
.hub-citation p{overflow-wrap:anywhere}
</style>
