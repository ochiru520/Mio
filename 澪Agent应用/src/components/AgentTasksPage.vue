<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, ChevronDown, ListChecks, Pause, Play, RefreshCw, Settings, X } from '@lucide/vue'
import { apiRequest } from '../services/api.js'
import { canResumeTask, taskStatusLabel } from '../agentTaskState.js'
import RuntimeTimeline from './RuntimeTimeline.vue'

const props = defineProps({ compact: Boolean, conversationId: { type: String, default: '' } })
const emit = defineEmits(['open-settings'])
const tasks = ref([])
const error = ref('')
const busy = ref('')
const filter = ref('active')
const offset = ref(0)
const hasMore = ref(false)
const budgetEnabled = ref(false)
let timer
let disposed = false
const loading = ref(false)
const loaded = ref(false)
const expanded = ref(false)
const compactRoot = ref(null)
function closeOutside(event) {
  if (compactRoot.value && !compactRoot.value.contains(event.target)) expanded.value = false
}
const visible = computed(() => {
  const values = filter.value === 'active' ? tasks.value.filter(t => !['completed', 'cancelled'].includes(t.status)) : tasks.value
  return props.compact ? values.slice(0, 1) : values
})

async function load() {
  if (props.compact && !props.conversationId) { tasks.value = []; loaded.value = true; return }
  if (loading.value) return
  loading.value = true
  const conversationId = props.conversationId
  const requestedFilter = filter.value
  const requestedOffset = offset.value
  try {
    const data = await apiRequest(`/api/agent/work?conversation_id=${encodeURIComponent(props.conversationId)}&status=${requestedFilter}&offset=${requestedOffset}`)
    if (disposed || conversationId !== props.conversationId || requestedFilter !== filter.value || requestedOffset !== offset.value) return
    tasks.value = data.tasks || []
    hasMore.value = !!data.has_more
    budgetEnabled.value = !!data.limits?.enabled
    loaded.value = true
    error.value = ''
  } catch (e) { if (!disposed && conversationId === props.conversationId) error.value = e.message }
  finally { loading.value = false }
}
async function action(task, command) {
  if (busy.value) return
  busy.value = task.id
  try {
    await apiRequest(`/api/agent/work/${encodeURIComponent(task.id)}/${command}`, { method: 'POST', body: '{}' })
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = '' }
}
watch(() => props.conversationId, () => { tasks.value = []; expanded.value = false; load() })
watch(() => visible.value[0]?.id, () => { expanded.value = false })
watch(filter, () => { offset.value = 0; tasks.value = []; void load() })
watch(offset, () => { void load() })
onMounted(() => { void load(); timer = window.setInterval(load, 2500); window.addEventListener('pointerdown', closeOutside) })
onBeforeUnmount(() => { disposed = true; window.clearInterval(timer); window.removeEventListener('pointerdown', closeOutside) })
</script>

<template>
  <section v-if="compact && (visible.length || error)" ref="compactRoot" class="task-goal-strip" @keydown.esc="expanded = false">
    <p v-if="error" role="alert" class="goal-error">{{ error }}<button type="button" title="重新读取任务" @click="load"><RefreshCw :size="14" /></button></p>
    <template v-for="task in visible" :key="task.id">
      <div class="goal-row">
        <button type="button" class="goal-summary" :aria-expanded="expanded" aria-label="展开或收起任务详情" @click="expanded = !expanded">
          <ListChecks :size="14" /><span class="goal-title" :title="task.snapshot?.goal || task.original_goal">{{ task.snapshot?.goal || task.original_goal }}</span><span class="goal-status">{{ taskStatusLabel(task.status) }}</span><ChevronDown :size="13" :class="{ rotated: expanded }" />
        </button>
        <button v-if="canResumeTask(task)" type="button" title="继续任务" :disabled="!!busy" @click="action(task, 'resume')"><Play :size="14" /></button>
        <button v-if="['running','responding','ready','waiting_jobs','waiting_confirmation'].includes(task.status)" type="button" title="暂停任务推进，保留已提交后台工作" :disabled="!!busy" @click="action(task, 'pause')"><Pause :size="14" /></button>
        <button type="button" title="取消任务及关联后台工作，已完成结果保留" :disabled="!!busy" @click="action(task, 'cancel')"><X :size="14" /></button>
      </div>
      <section v-if="expanded" class="goal-details" aria-label="任务详情">
        <header><strong>任务详情</strong><button type="button" title="收起任务详情" @click="expanded = false"><X :size="14" /></button></header>
        <p>{{ task.snapshot?.goal || task.original_goal }}</p>
        <p v-if="task.snapshot?.blocker" class="task-blocker">{{ task.snapshot.blocker }}</p>
        <p v-if="task.snapshot?.next_step">下一步：{{ task.snapshot.next_step }}</p>
        <ol v-if="task.snapshot?.plan?.length"><li v-for="(step,index) in task.snapshot.plan" :key="index">{{ step }}</li></ol>
        <div v-for="step in (task.observations || []).filter(s => task.status === 'waiting_confirmation' && s.status === 'needs_confirmation')" :key="step.step_id" class="goal-confirm"><span>待确认：{{ step.tool_name }}</span><button type="button" :disabled="!!busy" @click="action(task, `actions/${step.action_id}/approve`)">确认执行</button></div>
        <details v-if="task.observations?.length"><summary>执行记录 · {{ task.observations.length }}</summary><ul><li v-for="step in task.observations" :key="step.step_id">{{ step.tool_name }} · {{ step.status }}<p v-if="step.error">{{ step.error }}</p></li></ul></details>
        <small>模型费用 ¥{{ Number(task.spent_yuan || 0).toFixed(4) }}<template v-if="task.snapshot?.cost_unknown">（部分费用未知）</template></small>
      </section>
    </template>
  </section>
  <section v-else-if="!compact" class="agent-tasks">
    <header v-if="!compact" class="task-heading">
      <h2><ListChecks :size="20" />任务</h2>
      <select v-model="filter" aria-label="任务状态"><option value="active">当前任务</option><option value="all">全部任务</option></select>
      <button title="刷新任务" :disabled="loading" @click="load"><RefreshCw :class="{ spin: loading }" :size="17" /></button>
      <button title="任务设置" @click="emit('open-settings')"><Settings :size="17" /></button>
    </header>
    <RuntimeTimeline v-if="!compact" />
    <p v-if="error" role="alert" class="task-error">{{ error }}</p>
    <p v-if="!loaded && !error && !compact" class="empty-tasks">正在读取任务</p>
    <p v-else-if="loaded && !error && !visible.length && !compact" class="empty-tasks">暂无{{ filter === 'active' ? '进行中的' : '' }}任务</p>
    <article v-for="task in visible" :key="task.id" class="task-item">
      <header><strong>{{ task.snapshot.goal || task.original_goal }}</strong><span>{{ taskStatusLabel(task.status) }}</span></header>
      <p v-if="task.snapshot.blocker" class="task-blocker">{{ task.snapshot.blocker }}</p>
      <p v-if="task.snapshot.next_step">下一步：{{ task.snapshot.next_step }}</p>
      <ol v-if="task.snapshot.plan?.length"><li v-for="(step,index) in task.snapshot.plan" :key="index">{{ step }}</li></ol>
      <details v-if="!compact && task.observations.length"><summary>执行记录 · {{ task.observations.length }}</summary>
        <ul><li v-for="step in task.observations" :key="step.step_id"><code>{{ step.tool_name }}</code><span>{{ step.status }}</span><p v-if="step.error">{{ step.error }}</p><a v-if="step.result?.verified && step.result?.url" :href="step.result.url" download>{{ step.result.name }}</a></li></ul>
      </details>
      <div v-for="step in task.observations.filter(s => task.status === 'waiting_confirmation' && s.status === 'needs_confirmation')" :key="`approve-${step.step_id}`" class="task-confirm">
        <span>待确认：{{ step.tool_name }}</span><button :disabled="!!busy" @click="action(task, `actions/${step.action_id}/approve`)"><Check :size="15" />确认执行</button>
      </div>
      <footer>
        <small>模型费用 ¥{{ Number(task.spent_yuan || 0).toFixed(4) }}<template v-if="task.snapshot.cost_unknown">（部分费用未知）</template><template v-if="budgetEnabled"> / ¥{{ Number(task.budget_yuan).toFixed(2) }}</template></small>
        <button v-if="canResumeTask(task)" title="继续任务" :disabled="!!busy" @click="action(task, 'resume')"><Play :size="16" />继续</button>
        <button v-if="['running','responding','ready','waiting_jobs','waiting_confirmation'].includes(task.status)" title="暂停任务推进，保留已提交后台工作" :disabled="!!busy" @click="action(task, 'pause')"><Pause :size="16" /></button>
        <button v-if="!['completed','cancelled'].includes(task.status)" title="取消任务及关联后台工作，已完成结果保留" :disabled="!!busy" @click="action(task, 'cancel')"><X :size="16" />取消任务</button>
      </footer>
    </article>
    <footer v-if="!compact && (offset || hasMore)" class="task-heading"><button :disabled="!offset || loading" @click="offset = Math.max(0, offset - 50)">上一页</button><span>第 {{ offset / 50 + 1 }} 页</span><button :disabled="!hasMore || loading" @click="offset += 50">下一页</button></footer>
  </section>
</template>

<style scoped>
.agent-tasks{padding:24px;max-width:1000px;width:100%;margin:0 auto;box-sizing:border-box;color:var(--text-primary,#26332f);overflow:auto}
.task-goal-strip{position:relative;width:100%;min-width:0;margin:0 0 6px;color:var(--muted);font-size:12px}
.goal-row{display:flex;align-items:center;gap:3px;min-height:30px;padding:0 4px}
.task-goal-strip button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-width:28px;height:28px;padding:0 5px;border:0;border-radius:5px;color:inherit;background:transparent;cursor:pointer}
.task-goal-strip button:hover{background:var(--surface-soft,#f4f2f5);color:var(--ink)}
.task-goal-strip button:focus-visible{outline:2px solid var(--muted);outline-offset:1px}
.goal-row .goal-summary{flex:1;min-width:0;justify-content:flex-start;text-align:left;font-size:12px}
.goal-title{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.goal-status{flex-shrink:0;font-size:11px}.goal-row svg{flex-shrink:0}.rotated{transform:rotate(180deg)}
.goal-details{position:absolute;bottom:calc(100% + 6px);left:0;right:0;z-index:30;max-height:min(280px,40vh);overflow:auto;padding:14px 16px;border:1px solid var(--line,#e4dfe7);border-radius:12px;background:var(--surface,#fff);color:var(--ink);box-shadow:0 8px 28px #32243d12;overflow-wrap:anywhere}
.goal-details header{display:flex;align-items:center;justify-content:space-between}.goal-details p,.goal-details li{font-size:12px;line-height:1.6}.goal-details small{display:block;margin-top:10px;color:var(--muted)}.goal-confirm{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}.goal-confirm button{border:1px solid var(--line)}.goal-error{display:flex;align-items:center;gap:6px;margin:0;color:var(--muted);font-size:11px;max-height:48px;overflow:auto}
.task-heading,.task-heading h2,.task-item header,.task-item footer{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.task-heading h2{font-size:20px;margin-right:auto}.task-item{border-bottom:1px solid #d7deda;padding:18px 0}.task-item header strong{flex:1;min-width:120px;overflow-wrap:anywhere}.task-item header span{font-size:12px;color:#356957}.task-item p,.task-item li{font-size:13px;overflow-wrap:anywhere;line-height:1.6}.task-item footer{margin-top:12px}.task-item footer small{margin-right:auto;color:#64706c}.task-item button,.task-heading button,.task-limits button{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:32px;padding:5px 9px;border:1px solid #cbd6d0;border-radius:5px;background:var(--surface,#fff);color:inherit;cursor:pointer}.task-error,.task-blocker{color:#a24237}.task-confirm{display:flex;gap:12px;align-items:center;margin:12px 0}.task-limits{margin-top:24px}.task-limits>div{display:flex;gap:16px;flex-wrap:wrap;padding-top:16px}.task-limits label{display:grid;gap:6px;font-size:12px}.task-limits input{width:100px;padding:6px;border:1px solid #cbd6d0;border-radius:4px}.empty-tasks{padding:32px 0;color:#65716b}details summary{cursor:pointer;font-size:13px}code{margin-right:12px}button:disabled{opacity:.5;cursor:wait}@media(max-width:600px){.agent-tasks{padding:14px}.task-item header{align-items:flex-start}.task-item footer small{width:100%}}
</style>
