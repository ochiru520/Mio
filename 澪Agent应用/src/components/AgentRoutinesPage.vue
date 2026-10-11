<script setup>
import { onMounted, reactive, ref } from 'vue'
import { Plus, Clock3, RefreshCw } from '@lucide/vue'
import { apiRequest } from '../services/api.js'
const props = defineProps({ projects: { type: Array, default: () => [] } })
const items = ref([]), selected = ref(''), editing = ref(false), busy = ref(false), error = ref('')
const draft = reactive({ name: '', goal: '', project_id: '', mode: 'daily', time: '20:00', date: '', weekday: 0, path: '', pattern: '*.pdf', enabled: false })
async function load() { try { items.value = (await apiRequest('/api/workspace')).routines || [] } catch (cause) { error.value = cause.message } }
function begin(item = null) {
  selected.value = item?.id || ''; editing.value = true; error.value = ''
  Object.assign(draft, { name: item?.name || '', goal: item?.goal || '', project_id: item?.project_id || props.projects[0]?.id || '', mode: item?.kind === 'files' ? 'files' : item?.config?.mode || 'daily', time: item?.config?.time || '20:00', date: item?.config?.date || '', weekday: item?.config?.weekday ?? 0, path: item?.config?.path || '', pattern: item?.config?.pattern || '*.pdf', enabled: Boolean(item?.enabled) })
}
async function save() {
  if (busy.value) return
  if (!draft.name.trim() || !draft.goal.trim() || !draft.project_id) { error.value = '请填写任务名称、内容并选择项目。'; return }
  busy.value = true; error.value = ''
  try {
    const config = draft.mode === 'files' ? { path: draft.path, pattern: draft.pattern } : { mode: draft.mode, time: draft.time, ...(draft.mode === 'weekly' ? { weekday: Number(draft.weekday) } : {}), ...(draft.mode === 'once' ? { date: draft.date } : {}) }
    const result = await apiRequest(`/api/workspace/routines${selected.value ? '/' + selected.value : ''}`, { method: selected.value ? 'PUT' : 'POST', body: JSON.stringify({ name: draft.name, goal: draft.goal, project_id: draft.project_id, kind: draft.mode === 'files' ? 'files' : 'schedule', config, enabled: draft.enabled, template_id: '' }) })
    selected.value = result.id; await load(); begin(result)
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function toggle(item) { busy.value = true; try { await apiRequest(`/api/workspace/routines/${item.id}/enabled`, { method: 'POST', body: JSON.stringify({ enabled: !item.enabled }) }); await load(); if (selected.value === item.id) begin(items.value.find(row => row.id === item.id)) } catch (cause) { error.value = cause.message } finally { busy.value = false } }
onMounted(load)
</script>
<template>
  <section class="agent-library-layout"><aside class="agent-content-sidebar"><h2>定时任务</h2><button type="button" :disabled="busy" @click="begin()"><Plus :size="16" />新建任务</button><small>即将执行</small><button v-for="item in items.filter(row => row.enabled)" :key="item.id" :class="{ selected: selected === item.id }" type="button" @click="begin(item)">{{ item.name }}<small>{{ item.next_run_at?.slice(0,16).replace('T',' ') || '等待事件' }}</small></button><small>草稿与已暂停</small><button v-for="item in items.filter(row => !row.enabled)" :key="item.id" :class="{ selected: selected === item.id }" type="button" @click="begin(item)">{{ item.name }}</button></aside>
    <main class="agent-routine-main"><p v-if="error" role="alert">{{ error }}</p><div v-if="!editing" class="agent-empty-state"><Clock3 :size="25" /><h1>定时任务</h1><p>在指定时间或资料发生变化时，让澪继续你的项目。</p><button type="button" @click="begin()">创建任务</button></div>
      <form v-else @submit.prevent="save"><header><h1>{{ selected ? '编辑任务' : '新建任务' }}</h1><button type="button" :disabled="busy" aria-label="刷新定时任务" @click="load"><RefreshCw :size="16" /></button></header><label>任务名称<input v-model="draft.name" maxlength="160" required :disabled="busy" /></label><label>任务内容<textarea v-model="draft.goal" maxlength="12000" rows="4" required :disabled="busy" /></label><label>项目<select v-model="draft.project_id" required :disabled="busy"><option value="">选择项目</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select></label><label>触发条件<select v-model="draft.mode" :disabled="busy"><option value="daily">每天</option><option value="weekly">每周</option><option value="once">指定日期</option><option value="files">文件变化</option></select></label><label v-if="draft.mode === 'weekly'">星期<select v-model="draft.weekday" :disabled="busy"><option v-for="(day,index) in ['星期一','星期二','星期三','星期四','星期五','星期六','星期日']" :key="day" :value="index">{{ day }}</option></select></label><label v-if="draft.mode === 'once'">日期<input v-model="draft.date" type="date" required :disabled="busy" /></label><label v-if="draft.mode !== 'files'">时间<input v-model="draft.time" type="time" required :disabled="busy" /></label><template v-else><label>已授权资料目录<input v-model="draft.path" required :disabled="busy" /></label><label>文件名规则<input v-model="draft.pattern" :disabled="busy" /></label></template><label class="execution-check"><input v-model="draft.enabled" type="checkbox" :disabled="busy" />启用任务</label><p v-if="selected && items.find(row => row.id === selected)?.last_error" role="alert">{{ items.find(row => row.id === selected).last_error }}</p><footer class="agent-inline-actions"><button type="button" :disabled="busy" @click="editing = false">返回</button><button type="submit" :disabled="busy">{{ busy ? '正在保存' : '保存任务' }}</button><button v-if="selected" type="button" :disabled="busy" @click="toggle(items.find(row => row.id === selected))">{{ draft.enabled ? '暂停任务' : '启用任务' }}</button></footer></form>
    </main>
  </section>
</template>
