<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as api from '../services/agentExperienceApi.js'
import * as workspaceApi from '../services/workspaceApi.js'
import { createLearningDraftWriter, learningDraftKey } from '../learningDraftWriter.js'
const props = defineProps({ projectId: String, session: Object, disabled: Boolean })
const emit = defineEmits(['changed'])
const answers = ref({}), flagged = ref([]), active = ref('')
const error = ref(''), status = ref('草稿已保存'), busy = ref(false), conflicted = ref(false), restored = ref(false)
const current = computed(() => props.session.items?.find(item => item.id === active.value))
const locked = computed(() => busy.value || props.disabled)
let timer, disposed = false, generation = 0, writer, previousProject, previousSession
function apply(value) { answers.value = { ...value.answers }; flagged.value = [...(value.flagged_item_ids || [])]; active.value = value.active_item_id || props.session.items?.[0]?.id || '' }
function leaveWriter() { clearTimeout(timer); if (writer) { writer.checkpoint(); void writer.flush().catch(() => {}) } }
watch(() => [props.projectId, props.session], () => {
  const project = props.projectId, session = props.session.id
  if (previousProject === project && previousSession?.id === session && writer?.dirty) { previousSession = props.session; return }
  const waitingForProjectSession = previousProject !== undefined && previousProject !== project && previousSession === props.session
  leaveWriter(); writer = undefined; ++generation
  previousProject = project; previousSession = props.session
  if (waitingForProjectSession) { answers.value = {}; flagged.value = []; active.value = ''; busy.value = false; error.value = ''; status.value = '正在载入此项目的学习会话'; return }
  const initial = { answers: { ...props.session.draft?.answers }, flagged_item_ids: [...(props.session.draft?.flagged_item_ids || [])], active_item_id: props.session.draft?.active_item_id || props.session.items?.[0]?.id || '' }
  writer = createLearningDraftWriter(payload => api.saveLearningDraft(project, session, payload), props.session.draft?.revision || 0, { initialPayload: initial, storageKey: learningDraftKey(project, session) })
  apply(writer.payload); busy.value = false; conflicted.value = false; error.value = writer.storageError
  restored.value = writer.recovered
  status.value = writer.recovered ? '已恢复此窗口未同步的草稿；保存时会核对版本' : '草稿已保存'
}, { immediate: true, flush: 'sync' })
function dirty() { if (!writer) return; writer.change({ answers: answers.value, flagged_item_ids: flagged.value, active_item_id: active.value }); status.value = '尚未保存'; clearTimeout(timer); timer = setTimeout(() => void flush(), 450) }
async function flush() {
  clearTimeout(timer); const target = writer, n = generation
  if (!target) return false
  try { if (!disposed) status.value = '保存草稿…'; await target.flush(); if (!disposed && n === generation) { status.value = '草稿已保存'; error.value = target.storageError; conflicted.value = false; restored.value = false } return true }
  catch (cause) { if (!disposed && n === generation) { status.value = '保存失败，草稿已暂存，可切页后恢复'; error.value = cause.message; conflicted.value = cause.status === 409 } return false }
}
async function navigate(id) { if (locked.value) return; active.value = id; dirty(); await flush() }
function toggleFlag(id) { if (locked.value) return; flagged.value = flagged.value.includes(id) ? flagged.value.filter(value => value !== id) : [...flagged.value, id]; dirty() }
async function mergeLatest() {
  if (locked.value) return
  const n = generation, target = writer, project = props.projectId, session = props.session.id; busy.value = true
  try { const latest = await workspaceApi.loadLearningSession(project, session); if (disposed || n !== generation) return; apply(target.rebase(latest.draft)); await flush() }
  catch (cause) { if (!disposed && n === generation) error.value = cause.message }
  finally { if (!disposed && n === generation) busy.value = false }
}
async function answer() {
  if (!current.value || locked.value) return
  const n = generation, project = props.projectId, id = current.value.id; busy.value = true
  try { if (!await flush() || disposed || n !== generation) return; await workspaceApi.submitProjectLearningItemAnswer(project, id, { answer: answers.value[id] || '' }); if (!disposed && n === generation) emit('changed') }
  catch (cause) { if (!disposed && n === generation) error.value = cause.message }
  finally { if (!disposed && n === generation) busy.value = false }
}
function beforeUnload(event) { writer?.checkpoint(); if (writer?.dirty) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => { disposed = true; ++generation; leaveWriter(); window.removeEventListener('beforeunload', beforeUnload) })
defineExpose({ flush })
</script>
<template><section class="learning-exercise"><div class="exercise-navigator" aria-label="按题导航"><button v-for="(item,index) in session.items" :key="item.id" type="button" :aria-current="active===item.id?'step':undefined" :disabled="locked" @click="navigate(item.id)">{{ index+1 }}{{ flagged.includes(item.id)?' · 待检查':'' }}{{ item.latest_attempt?' ✓':'' }}</button></div><p role="status">{{ status }}<button v-if="error" type="button" :disabled="locked" @click="flush">重试保存</button></p><button v-if="restored&&!error" type="button" :disabled="locked" @click="flush">保存恢复的草稿</button><p v-if="error" role="alert">{{ error }}</p><template v-if="conflicted"><p>另一窗口已修改草稿。合并会保留你改过的答案和标记，采用远端其他改动；同题冲突以当前本地内容为准。</p><button type="button" :disabled="locked" @click="mergeLatest">读取最新版本并保留本地修改后保存</button></template><article v-if="current"><header><strong>{{ current.prompt }}</strong><button type="button" :disabled="locked" :aria-pressed="flagged.includes(current.id)" @click="toggleFlag(current.id)">{{ flagged.includes(current.id)?'取消待检查':'标记待检查' }}</button></header><small v-if="current.source_ref">来源：{{ current.source_ref }}</small><textarea v-model="answers[current.id]" rows="4" maxlength="10000" :disabled="locked" aria-label="本题草稿答案" @input="dirty" /><button type="button" :disabled="locked||!answers[current.id]?.trim()" @click="answer">提交本题答案</button><p v-if="current.latest_attempt">{{ current.latest_attempt.feedback }} · {{ Math.round(current.latest_attempt.score*100) }}%</p></article><p v-else>此会话还没有练习。</p><small>未同步草稿临时保存在此浏览器窗口；成功同步后清理。提交答案后才计入作答进度。</small></section></template>
<style scoped>.learning-exercise{display:grid;gap:14px;color:var(--text);font-size:calc(13px * var(--fs,1));line-height:1.8}.exercise-navigator{display:flex;flex-wrap:wrap;gap:8px}.learning-exercise button{padding:7px 10px;background:transparent;border:1px solid var(--border);border-radius:7px;font:inherit;color:var(--text);cursor:pointer}.learning-exercise button[aria-current=step]{background:var(--accent-soft);color:var(--accent)}.learning-exercise header{display:flex;justify-content:space-between;gap:20px}.learning-exercise textarea{width:100%;margin:12px 0;resize:vertical;max-height:40vh;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);font:inherit}.learning-exercise small,.learning-exercise>p{color:var(--text-muted)}</style>
