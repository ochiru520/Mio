<script setup>
import * as workspaceApi from '../services/workspaceApi.js'
import { onBeforeUnmount, ref, watch } from 'vue'
import LearningExercise from './LearningExercise.vue'
import LearningReviewPanel from './LearningReviewPanel.vue'
const props = defineProps({ projectId: String })
const sessions = ref([]), selected = ref(null), busy = ref(false), error = ref(''), tab = ref('sessions')
const exercise = ref(null), exerciseVersion = ref(0)
let version = 0, disposed = false
const current = (n, project) => !disposed && n === version && project === props.projectId
async function load({ saved = false } = {}) {
  if (busy.value) return false
  const project = props.projectId, n = ++version
  if (!project) { sessions.value = []; selected.value = null; return true }
  busy.value = true
  try {
    if (!saved && exercise.value && !await exercise.value.flush()) return false
    if (!current(n, project)) return false
    const data = await workspaceApi.loadProjectLearning(project, {})
    if (!current(n, project)) return false
    sessions.value = data.sessions || []
    if (selected.value) { selected.value = sessions.value.find(session => session.id === selected.value.id) || null; exerciseVersion.value++ }
    error.value = ''; return true
  } catch (cause) { if (current(n, project)) error.value = cause.message; return false }
  finally { if (current(n, project)) busy.value = false }
}
async function choose(session) {
  if (busy.value) return
  const project = props.projectId, n = ++version; busy.value = true
  try {
    if (exercise.value && !await exercise.value.flush()) return
    if (!current(n, project)) return
    const fresh = await workspaceApi.loadLearningSession(project, session.id)
    if (current(n, project)) { selected.value = fresh; exerciseVersion.value++; error.value = '' }
  } catch (cause) { if (current(n, project)) error.value = cause.message }
  finally { if (current(n, project)) busy.value = false }
}
async function switchTab(value) {
  if (busy.value || tab.value === value) return
  const project = props.projectId, n = ++version; busy.value = true
  try {
    if (exercise.value && !await exercise.value.flush()) return
    if (value === 'sessions' && selected.value) {
      const fresh = await workspaceApi.loadLearningSession(project, selected.value.id)
      if (current(n, project)) { selected.value = fresh; exerciseVersion.value++ }
    }
    if (current(n, project)) tab.value = value
  } catch (cause) { if (current(n, project)) error.value = cause.message }
  finally { if (current(n, project)) busy.value = false }
}
async function reloadExercise() { await load({ saved: true }) }
watch(() => props.projectId, () => { ++version; busy.value = false; sessions.value = []; selected.value = null; error.value = ''; tab.value = 'sessions'; void load({ saved: true }) }, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { disposed = true; ++version })
</script>
<template>
  <section class="agent-learning-panel"><h2>学习记录</h2><p v-if="!projectId">先为学习对话选择项目，笔记、练习和复习记录会保存在其中。</p><p v-if="error" role="alert">{{ error }}</p><template v-if="projectId"><div class="agent-inline-actions"><button type="button" :disabled="busy" @click="switchTab('sessions')">学习会话</button><button type="button" :disabled="busy" @click="switchTab('review')">复习</button><button type="button" :disabled="busy" @click="load">刷新</button></div><LearningReviewPanel v-if="tab === 'review'" :project-id="projectId" /><template v-else><p v-if="!sessions.length">还没有学习记录，可在对话中让澪为资料建立学习会话和练习。</p><button v-for="session in sessions" :key="session.id" type="button" class="agent-learning-session" :disabled="busy" @click="choose(session)">{{ session.title }} · 已作答 {{ session.progress.answered }}/{{ session.progress.total }}</button><section v-if="selected"><h3>{{ selected.title }}</h3><p>{{ selected.goal }}</p><LearningExercise ref="exercise" :key="`${projectId}:${selected.id}:${exerciseVersion}`" :project-id="projectId" :session="selected" :disabled="busy" @changed="reloadExercise" /></section></template></template></section>
</template>
