<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { apiRequest } from '../services/api.js'
import LearningReviewPanel from './LearningReviewPanel.vue'
const props = defineProps({ projectId: String })
const sessions = ref([]), selected = ref(null), answers = ref({}), busy = ref(false), error = ref(''), tab = ref('sessions')
let version = 0, disposed = false
async function load() {
  const id = props.projectId, n = ++version
  if (!id) { sessions.value = []; return }
  try { const data = await apiRequest(`/api/workspace/projects/${id}/learning`); if (!disposed && n === version && id === props.projectId) { sessions.value = data.sessions; if (selected.value) selected.value = data.sessions.find(s => s.id === selected.value.id) || null; error.value = '' } }
  catch (cause) { if (n === version) error.value = cause.message }
}
async function answer(item) { const id = props.projectId; busy.value = true; try { await apiRequest(`/api/workspace/projects/${id}/learning/items/${item.id}/answer`, { method: 'POST', body: JSON.stringify({ answer: answers.value[item.id] || '' }) }); if (id === props.projectId) await load() } catch (cause) { error.value = cause.message } finally { busy.value = false } }
watch(() => props.projectId, () => { selected.value = null; answers.value = {}; void load() }, { immediate: true })
onBeforeUnmount(() => { disposed = true; ++version })
</script>
<template>
  <section class="agent-learning-panel"><h2>学习记录</h2><p v-if="!projectId">先为学习对话选择项目，笔记、练习和复习记录会保存在其中。</p><p v-if="error" role="alert">{{ error }}</p><template v-if="projectId"><div class="agent-inline-actions"><button type="button" @click="tab = 'sessions'; load()">学习会话</button><button type="button" @click="tab = 'review'">复习</button><button type="button" @click="load">刷新</button></div><LearningReviewPanel v-if="tab === 'review'" :project-id="projectId" /><template v-else><p v-if="!sessions.length">还没有学习记录，可在对话中让澪为资料建立学习会话和练习。</p><button v-for="session in sessions" :key="session.id" type="button" class="agent-learning-session" @click="selected = session">{{ session.title }} · 已作答 {{ session.progress.answered }}/{{ session.progress.total }}</button><section v-if="selected"><h3>{{ selected.title }}</h3><p>{{ selected.goal }}</p><article v-for="item in selected.items" :key="item.id"><strong>{{ item.prompt }}</strong><small v-if="item.source_ref">来源：{{ item.source_ref }}</small><textarea v-model="answers[item.id]" rows="3" :disabled="busy" :aria-label="`${item.prompt}的答案`" /><button type="button" :disabled="busy || !answers[item.id]?.trim()" @click="answer(item)">提交答案</button><p v-if="item.latest_attempt">{{ item.latest_attempt.feedback }} · {{ Math.round(item.latest_attempt.score*100) }}%</p></article></section></template></template></section>
</template>
