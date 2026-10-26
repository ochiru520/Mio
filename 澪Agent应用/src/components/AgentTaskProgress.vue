<script setup>
import * as experienceApi from '../services/agentExperienceApi.js'
import { activityExpanded, settleResponseStream } from '../runActivityState.js'
import * as tasksApi from '../services/tasksApi.js'

import { mergeLoadedItems } from '../scrollCollections.js'
import WorkspacePager from './WorkspacePager.vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { taskStatusLabel } from '../agentTaskState.js'
import { applyAnalysisEvent, receiptSummary, visibleAnalysis, processOperations } from '../agentProcessState.js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps({ taskId: String, task: Object, runId: String, mobile: Boolean, inlineChat: Boolean, bare:Boolean })
const emit = defineEmits(['expired','browser-target'])
const connected = ref(false), reconnecting = ref(false), liveText = ref(''), textState = ref('')
const action = ref(''), liveStatus = ref(''), streamId = ref(''), events = ref([])
const analyses = ref([]), snapshot = ref({})
const manualExpanded = ref(null), response = ref({ id:'', text:'', state:'' }), editingInput = ref(null), editContent = ref(''), inputError = ref('')
const expanded = computed(() => activityExpanded(liveStatus.value || props.task?.status || (props.runId ? 'completed' : ''), Boolean(props.runId || response.value.state === 'committed'), manualExpanded.value))
async function mutateInput(input, withdraw = false) {
  inputError.value = ''
  try {
    if(withdraw) await experienceApi.withdrawInput(props.taskId,input.id,input.revision)
    else await experienceApi.updateInput(props.taskId,input.id,{content:editContent.value,expected_revision:input.revision})
    editingInput.value=null; await loadInputs()
  } catch (cause) { inputError.value=cause.message; await loadInputs() }
}

const displayAnalyses = computed(() => analyses.value.map(block => ({ ...block, ...visibleAnalysis(block.content) })))
const currentAnalysis = computed(() => !props.runId && displayAnalyses.value.findLast(block => block.status === 'running'))
const previousAnalyses = computed(() => displayAnalyses.value.filter(block => (props.runId || block.status !== 'running') && block.text))
const languageNotice = computed(() => displayAnalyses.value.some(block => block.suppressed || block.languageNotice))
const operations = computed(() => processOperations(events.value))
const completedOperations = computed(() => operations.value.filter(step => step.status === 'completed').length)
const processState = computed(() => ({ ...(props.task?.snapshot || {}), ...snapshot.value }))
const statusLabels = { completed:'已完成', failed:'失败', needs_confirmation:'等待确认', cancelled:'已停止', running:'执行中', deferred:'未执行', unknown:'结果待核实' }
function analysisHtml(text) { return DOMPurify.sanitize(marked.parse(text || '')) }
function settleAnalyses(status) {
  if (['completed','cancelled','paused','failed','budget_exhausted','waiting_user','waiting_confirmation'].includes(status)) {
    analyses.value=analyses.value.map(block => block.status==='running' ? {...block,status:'interrupted'} : block)
  }
}
const inputPage = ref({ inputs: [], has_more: false, next_version: 0 }), inputCursors = ref([0]), inputLoading = ref(false)
let source, cursor = 0, generation = 0, disposed = false, inputVersion = 0
const labels = { planner_started: '正在规划下一步', planner_completed: '已读取规划结果', tool_batch_completed: '本轮工具执行结束', user_input_replan: '收到新要求，正在调整计划', final_response_completed: '本轮回复已保存', final_response_failed: '回复未完成', final_response_cancelled: '回复已停止' }
function close() { source?.close(); source = null; connected.value = false }
async function loadInputs() {
  const version = ++inputVersion, id = props.taskId, after = inputCursors.value.at(-1)
  inputLoading.value = true
  try {
    const pages=await Promise.all(inputCursors.value.map(cursor=>tasksApi.loadWorkInputsByAfterVersion(id,cursor,{})))
    const data=pages.at(-1),updated=new Map()
    for(const page of pages)for(const item of page.inputs || [])updated.set(item.id,item)
    if (!disposed && version === inputVersion && id === props.taskId) inputPage.value = { ...data, inputs:[...updated.values()] }
  } catch (cause) { if (!disposed && id === props.taskId && cause.status === 401 && props.mobile) emit('expired') }
  finally { if (version === inputVersion) inputLoading.value = false }
}
function inputPageChange(direction) {
  if (inputLoading.value) return
  if (direction > 0) inputCursors.value.push(inputPage.value.next_version)
  else if (inputCursors.value.length > 1) inputCursors.value.pop()
  void loadInputs()
}
function connect() {
  if (disposed || source || !props.taskId || typeof EventSource === 'undefined') return
  const version = generation, id = props.taskId
  source = tasksApi.openWorkStream(id, cursor, EventSource)
  const current = () => !disposed && generation === version && id === props.taskId
  source.onopen = () => { if (current()) { connected.value = true; reconnecting.value = false } }
  source.onerror = () => { if (current()) { connected.value = false; reconnecting.value = true } }
  source.addEventListener('progress', event => {
    if (!current()) return
    let value
    try { value = JSON.parse(event.data) } catch { return }
    if (!Number.isSafeInteger(value.id) || value.id <= cursor) return
    cursor = value.id
    if (props.runId && value.run_id !== props.runId) return
    const kind = value.event_type, payload = value.payload || {}
    if (!props.runId && kind === 'tool_batch_completed' && ['running','responding','ready','waiting_jobs'].includes(props.task?.status)) {
      for (const tool of payload.tools || []) if (tool.tool_name === 'browser_open' && tool.status === 'completed' && tool.result?.page_id) emit('browser-target',{task_id:id,page_id:tool.result.page_id})
    }
    if (!props.runId && kind === 'browser_page_handoff') for (const page of payload.pages || []) emit('browser-target',{task_id:id,page_id:page.page_id,auto_control:false})
    response.value = settleResponseStream(response.value,kind,payload)
    liveText.value = props.inlineChat && response.value.state === 'committed' ? '' : response.value.text
    if (kind.startsWith('analysis_')) analyses.value=applyAnalysisEvent(analyses.value,value)
    if (kind !== 'response_text_delta' && !kind.startsWith('analysis_')) events.value = [...events.value, value].slice(-200)
    if (labels[kind]) action.value = labels[kind]
    if (kind === 'response_stream_started') { streamId.value = payload.stream_id; liveText.value = ''; textState.value = '生成中的正文，尚未保存为最终回复' }
    if (kind === 'response_text_delta') textState.value = response.value.state === 'committed' ? '本轮回复已保存' : '生成中的正文，尚未保存为最终回复'
    if (kind === 'response_stream_finished' && payload.stream_id === streamId.value) textState.value = payload.status === 'generated' ? '正文已生成，等待保存最终回复' : '生成已中断，以下是未完成正文'
    if (kind === 'final_response_completed') { if (typeof payload.reply === 'string') liveText.value = props.inlineChat ? '' : payload.reply; textState.value = '本轮回复已保存' }
    if (['planner_completed','user_input_replan','input_updated','input_withdrawn','user_input_received'].includes(kind)) void loadInputs()
  })
  source.addEventListener('task_state', event => {
    if (!current()) return
    try { const value = JSON.parse(event.data); liveStatus.value = value.status; snapshot.value=value.snapshot || {}; settleAnalyses(value.status); void loadInputs() } catch {}
  })
  source.addEventListener('idle', () => { if (current()) { close(); reconnecting.value = false } })
  source.addEventListener('task_removed', () => { if (current()) { close(); reconnecting.value = false } })
  source.addEventListener('access_expired', () => { if (current()) { close(); reconnecting.value = false; emit('expired') } })
}
watch(() => props.taskId, () => { ++generation; ++inputVersion; close(); manualExpanded.value=null;response.value={id:'',text:'',state:''}; cursor = 0; liveText.value = ''; textState.value = ''; streamId.value = ''; events.value = []; analyses.value = []; snapshot.value = {}; inputPage.value = { inputs: [], has_more: false, next_version: 0 }; inputCursors.value = [0]; liveStatus.value = ''; action.value = ''; connect(); void loadInputs() })
watch(() => [props.task?.status, props.task?.snapshot?.input_version], () => { liveStatus.value = props.task?.status || ''; if (!source && ['running','responding','ready','waiting_jobs'].includes(props.task?.status)) connect(); void loadInputs() })
onMounted(() => { connect(); void loadInputs() })
onUnmounted(() => { disposed = true; ++generation; ++inputVersion; close() })
</script>

<template>
  <details class="task-progress-group" :open="bare || expanded"><summary v-show="!bare" @click.prevent="manualExpanded = !expanded">{{ expanded ? '收起过程' : '分析与执行过程' }}<small> · {{ taskStatusLabel(liveStatus || task?.status || (runId ? 'completed' : 'running')) }}</small></summary>
  <section class="task-progress" aria-label="任务实时进展">
    <p v-if="!runId" class="status"><strong>{{ runId ? '已保存的过程' : taskStatusLabel(liveStatus || task?.status) }}</strong><span v-if="action && !runId"> · {{ action }}</span><small v-if="reconnecting">正在重连，已有进度保留</small></p>
    <p v-if="!runId && processState.summary" class="process-summary">{{ processState.summary }}</p>
    <details v-if="currentAnalysis?.text" class="analysis-block current-analysis" open><summary>当前分析</summary><div class="analysis-text" v-html="analysisHtml(currentAnalysis.text)" /></details>
    <p v-else-if="currentAnalysis && !currentAnalysis.languageNotice && !currentAnalysis.suppressed" class="analysis-waiting">正在分析，等待模型返回中文说明…</p>
    <p v-if="languageNotice" class="analysis-language-note">模型返回的部分分析不是中文，以下显示中文执行进度。</p>
    <div class="process-records">
    <details v-if="previousAnalyses.length" class="analysis-history"><summary>分析记录 <small>{{ previousAnalyses.length }} 段</small></summary><article v-for="block in previousAnalyses" :key="block.id" class="analysis-entry"><div class="analysis-text" v-html="analysisHtml(block.text)" /></article></details>
    <details v-if="operations.length" class="process-actions"><summary>操作记录 <small>{{ completedOperations }}/{{ operations.length }} 已完成</small></summary><article v-for="(step,index) in operations" :key="step.step_id || index"><header><strong>{{ step.display_name || '执行操作' }}</strong><small>{{ statusLabels[step.status] || '状态待核实' }}</small></header><p v-if="receiptSummary(step)">{{ receiptSummary(step) }}</p></article></details>
    <details v-if="!runId && (processState.plan?.length || processState.next_step || processState.blocker)" class="process-plan"><summary>任务计划</summary><ol v-if="processState.plan?.length"><li v-for="item in processState.plan" :key="item">{{ item }}</li></ol><p v-if="processState.next_step">接下来：{{ processState.next_step }}</p><p v-if="processState.blocker">暂时受阻：{{ processState.blocker }}</p></details>
    </div>
    <div v-if="liveText && !runId" class="text"><small>{{ textState }}</small><pre>{{ liveText }}</pre></div>
    <details v-if="!runId && (inputPage.inputs.length || task?.snapshot?.input_version || events.length)" class="process-diagnostics"><summary>更多执行详情</summary><p v-if="inputError" role="alert">{{ inputError }}</p>
    <details v-if="inputPage.inputs.length || task?.snapshot?.input_version"><summary>补充要求与读取位置</summary><p class="hint">当前动作结束后读取新要求；已读取表示提供给规划，不代表任务已完成。</p><article v-for="input in inputPage.inputs" :key="input.id"><strong>补充 v{{ input.version }}</strong><small>{{ input.state === 'withdrawn' ? '已撤回' : input.consumed_run_id ? `已提供给 ${input.consumed_run_id.slice(-8)} 的第 ${input.consumed_round + 1} 次规划` : '已保存，等待下一次规划读取' }}</small><details><summary>查看补充正文</summary><pre>{{ input.content }}</pre></details><template v-if="!input.consumed_run_id && input.state !== 'withdrawn'"><div v-if="editingInput === input.id"><textarea v-model="editContent" aria-label="修改未采用的补充" /><button type="button" :disabled="!editContent.trim()" @click="mutateInput(input)">保存修改</button><button type="button" @click="editingInput=null">取消</button></div><div v-else><button type="button" @click="editingInput=input.id;editContent=input.content">编辑</button><button type="button" @click="mutateInput(input,true)">撤回</button></div></template></article><div class="pages"><WorkspacePager label="补充要求" :has-more="inputPage.has_more" :busy="inputLoading" @page="inputPageChange(1)" /><button :disabled="inputLoading" @click="loadInputs">刷新读取位置</button></div></details>
    <details v-if="events.length && !runId"><summary>最近执行事件（{{ events.length }}）</summary><ol><li v-for="event in events" :key="event.id"><strong>{{ labels[event.event_type] || '任务进度更新' }}</strong><small> · {{ event.created_at }}</small><details><summary>详细记录</summary><pre>{{ JSON.stringify(event.payload, null, 2) }}</pre></details></li></ol><small>这里只保留最近 200 条事件；完整工具与来源记录仍在任务中。</small></details>
    </details>
  </section>
  </details>
</template>

<style scoped>
.task-progress-group{margin:16px 0;color:var(--text-muted)}.task-progress-group>summary{cursor:pointer;padding:10px 0;font-size:calc(13px * var(--fs,1))}.task-progress-group>summary small{font-weight:400}.task-progress-group textarea{width:100%;min-height:70px;background:var(--surface);color:var(--text);border:1px solid var(--border);padding:10px}

.task-progress{display:grid;gap:16px;margin:20px 0;min-width:0;font-size:calc(13px * var(--fs,1));line-height:1.85;color:var(--text-muted)}.status{margin:0}.status strong{font-weight:500;color:var(--text)}.status small{display:block}.text{padding:14px 0}small,.hint{color:var(--text-muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;font:inherit;margin:8px 0}summary{cursor:pointer;padding:8px 0;min-height:34px}summary>small{margin-left:8px;font-size:calc(12px * var(--fs,1));font-weight:400}.pages{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}button{font:inherit;background:var(--surface);color:inherit;border:1px solid var(--border);border-radius:6px;padding:8px;cursor:pointer}button:disabled{opacity:.5;cursor:default}
.analysis-text{overflow-wrap:anywhere;max-height:320px;overflow-y:auto;line-height:1.9;padding:10px 16px;font-size:calc(13px * var(--fs,1));color:var(--text-muted)}.analysis-text :deep(p){margin:0 0 14px}.analysis-text :deep(li){margin:8px 0}.analysis-text :deep(pre){white-space:pre-wrap}.analysis-block{border-left:2px solid var(--border);padding-left:14px}.analysis-block>summary{color:var(--text)}.analysis-waiting,.analysis-language-note{margin:0;font-size:calc(12px * var(--fs,1))}.process-summary{white-space:pre-wrap;margin:0;color:var(--text)}.process-records{display:flex;flex-wrap:wrap;gap:0 24px}.process-records>details{min-width:0}.process-records>details[open]{flex-basis:100%;border-top:1px solid var(--border);padding-top:6px}.process-records article{padding:14px 0;border-bottom:1px solid var(--border)}.process-actions header{display:flex;align-items:baseline;gap:16px;justify-content:space-between}.process-actions strong{font-weight:500;color:var(--text)}.process-actions p{margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}.process-plan ol{padding-left:22px}.process-plan li{margin:10px 0}.process-diagnostics{font-size:calc(12px * var(--fs,1))}.process-diagnostics article{padding:12px 0}.process-diagnostics article>small{display:block}.analysis-entry:last-child{border-bottom:0}
</style>
