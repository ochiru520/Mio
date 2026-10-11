<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Activity, CheckCircle2, CircleAlert, Clock3, Database, LoaderCircle, RefreshCw, ShieldCheck, Wrench } from '@lucide/vue'
import { apiRequest } from '../services/api.js'
import { executionReceiptStatusLabels, groupExecutionRecords, matchesExecutionReceiptFilter } from '../executionRecordPresentation.js'

const props = defineProps({ initialTab: { type: String, default: 'tools' } })

const tabs = [
  { id: 'catalog', label: '工具目录', icon: Wrench },
  { id: 'autonomy', label: '自主策略', icon: ShieldCheck },
  { id: 'receipts', label: '执行记录', icon: Activity },
]
const permissionGroups = [
  { id: 'read_only', label: '只读', description: '查看、搜索和读取信息，不改变本机数据。' },
  { id: 'low_risk_write', label: '低风险写入', description: '可记录或生成可恢复内容，仍受项目范围约束。' },
  { id: 'high_risk_write', label: '高风险写入', description: '修改代码、运行命令或操作窗口，必须经过确认。' },
]
const autonomyLevels = [
  { id: 'observe', label: '只观察', description: '只记录相关事件，不主动提出行动。' },
  { id: 'suggest', label: '主动建议', description: '提出建议，等待你决定是否执行。' },
  { id: 'auto_low_risk', label: '低风险自动', description: '低风险能力可自动运行，高风险仍需确认。' },
  { id: 'confirm_high_risk', label: '高风险确认', description: '允许主动行动，但所有高风险步骤都要确认。' },
]
const capabilityNames = {
  follow_up_reminder: '到期跟进', daily_state: '今日状态', service_health: '服务健康', application_activity: '应用活动',
  task_result: '任务结果', proactive_checkin: '主动联系', night_close: '夜间收尾', screen_event: '重要屏幕变化',
}
const activeTab = ref('catalog'), tools = ref([]), receipts = ref([]), autonomy = ref(null)
const states = ref(Object.fromEntries(tabs.map(tab => [tab.id, { loading: false, loaded: false, error: '' }])))
const loading = computed(() => states.value[activeTab.value].loading)
const error = computed(() => states.value[activeTab.value].error)
const saving = ref(false), notice = ref(''), filter = ref('')
const receiptFilter = ref('all')
const receiptFilters = [
  { id: 'all', label: '全部状态' }, { id: 'running', label: '执行中' },
  { id: 'needs_confirmation', label: '待确认' }, { id: 'approved', label: '已批准' }, { id: 'completed', label: '已完成' },
  { id: 'failed', label: '失败 / 已取消' },
]
const receiptGroups = computed(() => groupExecutionRecords(receipts.value.filter(receipt => matchesExecutionReceiptFilter(receipt, receiptFilter.value))))
const draft = ref(null)
const dirty = computed(() => draft.value && autonomy.value?.policy && JSON.stringify(draft.value) !== JSON.stringify(normalizePolicy(autonomy.value.policy)))
const controllers = new Map()
let disposed = false

function copyPolicy(value) {
  return { ...(value || {}), capability_overrides: { ...((value || {}).capability_overrides || {}) } }
}
function normalizePolicy(value) {
  const source = copyPolicy(value)
  return {
    paused: Boolean(source.paused), autonomy_level: source.autonomy_level || 'suggest', quiet_start_hour: Number(source.quiet_start_hour ?? 0), quiet_end_hour: Number(source.quiet_end_hour ?? 0),
    minimum_interval_minutes: Number(source.minimum_interval_minutes ?? 30), daily_behavior_limit: Number(source.daily_behavior_limit ?? 3), daily_budget_yuan: Number(source.daily_budget_yuan || 0), capability_overrides: source.capability_overrides,
  }
}
const groupedTools = computed(() => permissionGroups.map(group => ({ ...group, items: tools.value.filter(item => item.permission === group.id && `${item.name} ${item.description} ${item.handler}`.toLowerCase().includes(filter.value.trim().toLowerCase())) })))
const overrideOptions = computed(() => [{ id: '', label: '继承全局' }, ...autonomyLevels, { id: 'disabled', label: '关闭' }])
function formatTime(value) { if (!value) return '时间未知'; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false }) }
function permissionLabel(value) { return permissionGroups.find(group => group.id === value)?.label || permissionGroups[Number(value)]?.label || value || '未定义' }
function receiptStatus(value) { return executionReceiptStatusLabels[value] || '未知状态' }
function toolAvailability(item) { return item.available ? '已登记 · 可用' : `已登记 · ${item.unavailable_reason || '依赖未就绪'}` }
function setOverride(id, value) {
  if (value) draft.value.capability_overrides[id] = value
  else delete draft.value.capability_overrides[id]
}
async function refresh(tab = activeTab.value) {
  if (states.value[tab].loading || saving.value) return
  const controller = new AbortController()
  controllers.set(tab, controller)
  const state = states.value[tab]
  state.loading = true; state.error = ''; notice.value = ''
  const urls = { catalog: '/api/agent/tools', autonomy: '/api/agent/autonomy?limit=100', receipts: '/api/agent/tool-receipts?limit=100' }
  try {
    const data = await apiRequest(urls[tab], { signal: controller.signal })
    if (disposed) return
    if (tab === 'catalog') tools.value = Array.isArray(data?.tools) ? data.tools : []
    if (tab === 'receipts') receipts.value = Array.isArray(data?.receipts) ? data.receipts : []
    if (tab === 'autonomy') {
      const preserveDraft = dirty.value
      autonomy.value = data || {}
      if (!preserveDraft) draft.value = normalizePolicy(data?.policy)
      const previous = sessionStorage.getItem('mio_autonomy_draft')
      if (previous && !preserveDraft) {
        try {
          const stored = JSON.parse(previous)
          if (JSON.stringify(normalizePolicy(data?.policy)) === stored.baseline) {
            draft.value = normalizePolicy(stored.draft)
            notice.value = '已恢复未保存的自主策略，请保存后生效。'
          } else sessionStorage.removeItem('mio_autonomy_draft')
        } catch { sessionStorage.removeItem('mio_autonomy_draft') }
      }
    }
    state.loaded = true
  } catch (cause) { if (!disposed) state.error = cause.message || '设置读取失败。' }
  finally { if (!disposed) state.loading = false; controllers.delete(tab) }
}
function validPolicy() {
  const ranges = { quiet_start_hour: [0, 23, true], quiet_end_hour: [0, 23, true], minimum_interval_minutes: [1, 1440, true], daily_behavior_limit: [0, 100, true], daily_budget_yuan: [0, 100, false] }
  return Object.entries(ranges).every(([key, [min, max, integer]]) => typeof draft.value[key] === 'number' && Number.isFinite(draft.value[key]) && draft.value[key] >= min && draft.value[key] <= max && (!integer || Number.isInteger(draft.value[key])))
}
async function savePolicy() {
  if (!draft.value || saving.value) return
  if (!validPolicy()) { states.value.autonomy.error = '请填写有效数值：小时 0–23，间隔 1–1440 分钟，次数和预算 0–100。'; return }
  saving.value = true; states.value.autonomy.error = ''; notice.value = ''
  try {
    const result = await apiRequest('/api/agent/autonomy/policy', { method: 'PATCH', body: JSON.stringify(draft.value) })
    if (disposed) return
    draft.value = normalizePolicy(result); autonomy.value = { ...(autonomy.value || {}), policy: result }; notice.value = '自主策略已保存。'; sessionStorage.removeItem('mio_autonomy_draft')
  } catch (cause) { if (!disposed) states.value.autonomy.error = cause.message || '自主策略保存失败。' } finally { saving.value = false }
}
async function togglePaused() {
  if (!draft.value || saving.value) return
  saving.value = true; states.value.autonomy.error = ''; notice.value = ''
  try {
    const result = await apiRequest(`/api/agent/autonomy/${autonomy.value.policy.paused ? 'resume' : 'pause'}`, { method: 'POST' })
    if (!disposed) { autonomy.value.policy = result; draft.value.paused = Boolean(result.paused); notice.value = result.paused ? '自主行为已暂停。' : '自主行为已恢复。' }
  } catch (cause) { if (!disposed) states.value.autonomy.error = cause.message || '操作失败。' } finally { saving.value = false }
}
function resetPolicy() { if (autonomy.value?.policy) draft.value = normalizePolicy(autonomy.value.policy); notice.value = ''; states.value.autonomy.error = ''; sessionStorage.removeItem('mio_autonomy_draft') }
watch(() => props.initialTab, value => { activeTab.value = ({ tools: 'catalog', autonomy: 'autonomy', receipts: 'receipts' })[value] || 'catalog' }, { immediate: true })
watch(activeTab, value => { if (!states.value[value].loaded) void refresh(value) }, { immediate: true })
onBeforeUnmount(() => {
  disposed = true
  for (const controller of controllers.values()) controller.abort()
  if (dirty.value) sessionStorage.setItem('mio_autonomy_draft', JSON.stringify({ baseline: JSON.stringify(normalizePolicy(autonomy.value.policy)), draft: draft.value }))
})
</script>

<template>
  <section class="agent-tool-settings" :aria-busy="loading || saving">
    <header class="agent-setting-title agent-tool-settings-header"><div><h1>{{ activeTab === 'catalog' ? '工具与权限' : tabs.find(tab => tab.id === activeTab).label }}</h1><p>{{ ({ catalog: '工具可用状态、访问范围与操作确认', autonomy: '主动行为的权限、频率与预算', receipts: '最近的工具调用状态与时间' })[activeTab] }}</p></div><button type="button" class="agent-settings-refresh" :disabled="loading || saving" @click="refresh()"><RefreshCw :size="15" :class="{ spin: loading }" />刷新</button></header>
    <p v-if="error" class="agent-settings-message error" role="alert">{{ error }} <button type="button" @click="refresh()">重新读取</button></p><p v-if="notice" class="agent-settings-message" role="status">{{ notice }}</p>
    <div v-if="loading && !states[activeTab].loaded" class="agent-tool-loading"><LoaderCircle class="spin" :size="22" />正在读取设置…</div>
    <template v-else-if="activeTab === 'catalog' && states.catalog.loaded">
      <label class="agent-tool-filter">搜索工具<input v-model="filter" type="search" placeholder="名称或功能说明" /></label>
      <section v-for="group in groupedTools" :key="group.id" class="agent-tool-group"><header><div><h2>{{ group.label }}</h2><p>{{ group.description }}</p></div><span>{{ group.items.length }}</span></header><div v-if="!group.items.length" class="agent-tool-empty">没有匹配的工具。</div><article v-for="item in group.items" :key="item.name" class="agent-tool-card"><div class="agent-tool-card-icon" :class="{ unavailable: !item.available }"><CircleAlert v-if="!item.available" :size="17" /><CheckCircle2 v-else :size="17" /></div><div class="agent-tool-card-main"><div class="agent-tool-card-title"><strong>{{ item.name }}</strong><span :class="['agent-tool-status', { unavailable: !item.available }]">{{ toolAvailability(item) }}</span></div><p>{{ item.description }}</p><div class="agent-tool-meta"><span>超时 {{ item.timeout_seconds }} 秒</span><span>{{ item.supports_cancellation ? '支持停止' : '不可取消' }}</span><span v-if="item.requires_explicit_intent">需要明确意图</span></div><details class="agent-tool-details"><summary>能力详情</summary><small>{{ item.idempotent ? '重复请求可安全复用' : '重复操作可能产生新结果' }}</small><small v-if="item.dependencies?.length">依赖：{{ item.dependencies.join('、') }}</small><small v-if="item.compensation">恢复：{{ item.compensation }}</small></details></div></article></section>
      <p class="agent-setting-note">可用状态不代表已经授权。文件访问范围在“文件权限”中设置；代码、终端和窗口授权在“代码与电脑”中设置。</p>
    </template>
    <template v-else-if="activeTab === 'autonomy' && draft">
      <section class="agent-tool-policy"><header><div><h2>自主行为</h2><p>{{ draft.paused ? '自主行为已暂停，手动对话和已授权任务不受影响。' : '策略只控制 Mio 是否主动评估和发起行为。' }}</p></div><button type="button" :class="['agent-policy-pause', { paused: draft.paused }]" :disabled="saving" @click="togglePaused">{{ draft.paused ? '恢复自主行为' : '暂停自主行为' }}</button></header><div class="agent-policy-levels" role="radiogroup" aria-label="自主权限档位"><button v-for="level in autonomyLevels" :key="level.id" type="button" :class="{ active: draft.autonomy_level === level.id }" role="radio" :disabled="saving" :aria-checked="draft.autonomy_level === level.id" @click="draft.autonomy_level = level.id"><strong>{{ level.label }}</strong><small>{{ level.description }}</small></button></div><div class="agent-policy-grid"><label>安静时段<div><input :disabled="saving" v-model.number="draft.quiet_start_hour" type="number" min="0" max="23" aria-label="安静时段开始" /><span>至</span><input :disabled="saving" v-model.number="draft.quiet_end_hour" type="number" min="0" max="23" aria-label="安静时段结束" /><span>时</span></div></label><label>最小主动间隔<div><input :disabled="saving" v-model.number="draft.minimum_interval_minutes" type="number" min="1" max="1440" /><span>分钟</span></div></label><label>每日主动次数<div><input :disabled="saving" v-model.number="draft.daily_behavior_limit" type="number" min="0" max="100" /><span>次</span></div></label><label>每日预算<div><input :disabled="saving" v-model.number="draft.daily_budget_yuan" type="number" min="0" max="100" step="0.01" /><span>元</span></div></label></div></section>
      <section class="agent-tool-policy"><header><div><h2>能力覆盖</h2><p>只覆盖选中的能力，其余继承上面的全局档位。</p></div></header><div class="agent-override-list"><label v-for="(label, id) in capabilityNames" :key="id"><span>{{ label }}</span><select :disabled="saving" :value="draft.capability_overrides[id] || ''" @change="setOverride(id, $event.target.value)"><option v-for="option in overrideOptions" :key="option.id" :value="option.id">{{ option.label }}</option></select></label></div><footer><span>{{ dirty ? '有未保存修改' : '当前策略已保存' }}</span><div><button type="button" :disabled="saving || !dirty" @click="resetPolicy">撤销修改</button><button type="button" class="primary" :disabled="saving || !dirty" @click="savePolicy">{{ saving ? '正在保存' : '保存自主策略' }}</button></div></footer></section>
    </template>
    <template v-else-if="activeTab === 'receipts' && states.receipts.loaded">
      <section class="agent-receipts">
        <header><div><h2>最近执行记录</h2><p>查看实际动作与结果；系统任务进度更新收在技术记录中。</p></div><Database :size="20" /></header>
        <label class="agent-receipt-filter">状态<select v-model="receiptFilter"><option v-for="option in receiptFilters" :key="option.id" :value="option.id">{{ option.label }}</option></select></label>
        <div v-if="!receiptGroups.length" class="agent-tool-empty">{{ receipts.length ? '没有符合此状态的执行记录。' : '还没有工具执行记录。' }}</div>
        <component v-for="group in receiptGroups" :key="String(group.technical)" :is="group.technical ? 'details' : 'div'" :class="{ 'agent-technical-records': group.technical }">
          <summary v-if="group.technical">技术记录 · {{ group.items.length }} 次任务进度更新</summary>
          <article v-for="{ record: receipt, presentation } in group.items" :key="receipt.id" class="agent-receipt-row">
            <div class="agent-receipt-icon"><CircleAlert v-if="receipt.status === 'failed'" :size="15" /><Clock3 v-else :size="15" /></div>
            <div class="agent-receipt-main">
              <div class="agent-receipt-heading"><strong>{{ presentation.label }}</strong><span>{{ receiptStatus(receipt.status) }}</span></div>
              <p :class="{ 'agent-receipt-error': receipt.status === 'failed' }">{{ presentation.summary }}</p>
              <details v-if="presentation.details.length" class="agent-tool-details"><summary>技术详情</summary><dl><template v-for="detail in presentation.details" :key="detail.label"><dt>{{ detail.label }}</dt><dd>{{ detail.value }}</dd></template><template v-if="receipt.permission !== undefined && receipt.permission !== null && receipt.permission !== ''"><dt>权限级别</dt><dd>{{ permissionLabel(receipt.permission) }}</dd></template></dl></details>
            </div>
            <time v-if="receipt.finished_at || receipt.created_at">{{ formatTime(receipt.finished_at || receipt.created_at) }}</time>
          </article>
        </component>
      </section><p class="agent-setting-note">工具执行完成不代表整个任务已经完成；最终成果可回到对话或任务详情查看。</p>
    </template>
  </section>
</template>

<style scoped>
.agent-receipt-filter{display:flex;align-items:center;gap:8px;color:var(--text-muted);font-size:12px}.agent-receipt-filter select{border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text);padding:6px 9px;font:inherit}.agent-receipt-main{min-width:0}.agent-receipt-heading{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.agent-receipt-row .agent-receipt-heading span{margin:0}.agent-receipt-main p{margin:6px 0 0;font-size:12px;line-height:1.6;overflow-wrap:anywhere}.agent-receipt-error{color:var(--danger)}.agent-technical-records>summary{padding:10px 0;cursor:pointer;font-size:12px;color:var(--text-muted)}.agent-tool-details dl{display:grid;grid-template-columns:70px minmax(0,1fr);gap:7px;margin:8px 0}.agent-tool-details dt{color:var(--text-muted)}.agent-tool-details dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto}@media(max-width:600px){.agent-receipts .agent-receipt-row{grid-template-columns:20px minmax(0,1fr)}.agent-receipt-row time{grid-column:2;white-space:normal}}
.agent-tool-settings{display:grid;gap:18px;min-width:0;max-width:960px;margin:0 auto;padding:30px 34px;box-sizing:border-box}.agent-tool-settings-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.agent-settings-refresh,.agent-tool-tabs button,.agent-policy-pause,.agent-tool-policy footer button{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text-muted);padding:8px 11px;font:inherit;font-size:12px;cursor:pointer}.agent-settings-refresh:disabled,.agent-tool-settings button:disabled{opacity:.5;cursor:default}.agent-tool-tabs{display:flex;gap:7px;flex-wrap:wrap;border-bottom:1px solid var(--border);padding-bottom:10px}.agent-tool-tabs button.active,.agent-tool-policy footer button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-contrast)}.agent-tool-tabs span{min-width:18px;padding:1px 5px;border-radius:99px;background:var(--surface-soft);text-align:center;font-size:11px}.agent-tool-filter{display:grid;gap:7px;font-size:12px;color:var(--text-muted)}.agent-tool-filter input,.agent-policy-grid input,.agent-override-list select{width:100%;box-sizing:border-box;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);padding:9px 10px;font:inherit}.agent-tool-group,.agent-tool-policy,.agent-receipts{border:1px solid var(--border);border-radius:12px;padding:16px;background:color-mix(in srgb,var(--surface) 92%,transparent)}.agent-tool-group>header,.agent-tool-policy>header,.agent-receipts>header,.agent-tool-policy footer{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.agent-tool-group h2,.agent-tool-policy h2,.agent-receipts h2{margin:0 0 5px;font-size:15px;font-weight:600}.agent-tool-group header p,.agent-tool-policy header p,.agent-receipts header p{margin:0;color:var(--text-muted);font-size:12px;line-height:1.6}.agent-tool-group>header>span{color:var(--text-muted);font-size:12px}.agent-tool-card{display:flex;gap:11px;padding:14px 0;border-top:1px solid var(--border);margin-top:14px}.agent-tool-card-icon{flex:none;color:var(--success)}.agent-tool-card-icon.unavailable{color:var(--warning)}.agent-tool-card-main{min-width:0}.agent-tool-card-title{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.agent-tool-card-title strong{font-size:13px;overflow-wrap:anywhere}.agent-tool-card-main p{margin:6px 0;color:var(--text-muted);font-size:12px;line-height:1.65;overflow-wrap:anywhere}.agent-tool-status{font-size:11px;color:var(--success)}.agent-tool-status.unavailable{color:var(--warning)}.agent-tool-meta{display:flex;gap:8px;flex-wrap:wrap;color:var(--text-muted);font-size:11px}.agent-tool-card-main small{display:block;margin-top:5px;color:var(--text-muted);font-size:11px;overflow-wrap:anywhere}.agent-tool-empty,.agent-tool-loading{display:flex;align-items:center;justify-content:center;gap:9px;padding:25px;color:var(--text-muted);font-size:12px}.agent-tool-policy{display:grid;gap:16px}.agent-policy-pause.paused{color:var(--success);border-color:var(--success)}.agent-policy-levels{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.agent-policy-levels button{min-height:74px;padding:10px;text-align:left;border:1px solid var(--border);border-radius:9px;background:var(--surface);color:var(--text);cursor:pointer}.agent-policy-levels button.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset;background:var(--surface-soft)}.agent-policy-levels strong,.agent-policy-levels small{display:block}.agent-policy-levels strong{font-size:12px}.agent-policy-levels small{margin-top:5px;color:var(--text-muted);font-size:11px;line-height:1.45}.agent-policy-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.agent-policy-grid label{display:grid;gap:7px;font-size:12px;color:var(--text-muted)}.agent-policy-grid label>div{display:flex;align-items:center;gap:5px}.agent-policy-grid input{min-width:0}.agent-policy-grid span{white-space:nowrap}.agent-override-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.agent-override-list label{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px;border:1px solid var(--border);border-radius:8px;font-size:12px}.agent-override-list select{width:130px;padding:6px 8px}.agent-tool-policy footer{align-items:center;color:var(--text-muted);font-size:12px}.agent-tool-policy footer div{display:flex;gap:8px}.agent-receipts{display:grid;gap:12px}.agent-receipt-row{display:grid;grid-template-columns:28px minmax(0,1fr) auto;align-items:center;gap:10px;padding:11px 0;border-top:1px solid var(--border)}.agent-receipt-icon{color:var(--text-muted)}.agent-receipt-row strong,.agent-receipt-row span{display:block;font-size:12px}.agent-receipt-row span,.agent-receipt-row time{color:var(--text-muted);font-size:11px;margin-top:4px}.agent-receipt-row time{white-space:nowrap}.agent-tool-loading{min-height:180px}.agent-setting-note{font-size:12px;color:var(--text-muted);line-height:1.7}.agent-setting-note code{font-size:11px}.agent-settings-message{padding:12px 14px;margin:0;border-radius:8px;background:var(--surface-soft);font-size:13px;line-height:1.6}.agent-settings-message.error{color:var(--danger);border:1px solid var(--danger)}.agent-tool-details{margin-top:8px;font-size:11px;color:var(--text-muted)}.agent-tool-details summary{cursor:pointer}.agent-tool-details[open] summary{margin-bottom:8px}@media(max-width:700px){.agent-policy-levels,.agent-policy-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.agent-override-list{grid-template-columns:1fr}}@media(max-width:480px){.agent-tool-settings{padding:22px 16px}.agent-policy-levels,.agent-policy-grid{grid-template-columns:1fr}.agent-tool-settings-header{display:block}.agent-settings-refresh{margin-top:10px}}
</style>
