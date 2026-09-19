<script setup>
import { computed, reactive, ref } from 'vue'
import { apiRequest } from '../services/api.js'
const props = defineProps({ observations: { type: Array, default: () => [] } })
const emit = defineEmits(['changed'])
const drafts = reactive({})
const overrides = reactive({})
const busy = ref(false)
const error = ref('')
const jobs = computed(() => [...new Map(props.observations.map(step => step.result?.job).filter(Boolean).map(job => [job.id, overrides[job.id] || job])).values()].filter(job => ['unknown', 'needs_confirmation', 'failed'].includes(job.status)))
function draft(job) { return drafts[job.id] ||= { outcome: 'still_unknown', receipt: '', note: '' } }
async function perform(job, command, payload = {}) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const value = await apiRequest(`/api/creation/jobs/${encodeURIComponent(job.id)}/${command}`, { method: 'POST', body: JSON.stringify(payload) })
    overrides[job.id] = value.job
    emit('changed')
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
</script>

<template>
  <section v-if="jobs.length" class="job-recovery" aria-label="生成结果核对">
    <p v-if="error" role="alert">{{ error }}</p>
    <details v-for="job in jobs" :key="job.id">
      <summary>{{ job.status === 'unknown' ? '结果待核对' : job.status === 'needs_confirmation' ? '重试待确认' : '生成失败' }} · {{ job.id }}</summary>
      <p>{{ job.error || job.confirmation_reason }}</p>
      <small>请求编号：{{ job.client_id || '尚无回执' }} · 最近状态：{{ job.updated_at }}</small>
      <template v-if="job.status === 'unknown'">
        <label>核对结果<select v-model="draft(job).outcome"><option value="still_unknown">仍无法确认</option><option value="not_completed">供应商确认未完成</option><option value="completed_external">供应商确认已完成，尚未取回成果</option></select></label>
        <label>供应商回执编号<input v-model="draft(job).receipt" maxlength="200" /></label>
        <label>核对说明<textarea v-model="draft(job).note" maxlength="1000" /></label>
        <button type="button" :disabled="busy || !draft(job).receipt.trim() || !draft(job).note.trim()" @click="perform(job, 'reconcile', draft(job))">保存核对结果</button>
        <p>登记核对不会重新生成。远端已完成但尚未取回成果时，仍保留待核对状态。</p>
      </template>
      <ul v-if="job.spec?.reconciliations?.length"><li v-for="(record,index) in job.spec.reconciliations" :key="index">{{ record.checked_at }} · {{ record.receipt }} · {{ record.note }}</li></ul>
      <button v-if="['unknown','failed'].includes(job.status)" type="button" :disabled="busy" @click="perform(job, 'retry')">准备人工重试</button>
      <template v-if="job.status === 'needs_confirmation'"><p>确认会创建新生成请求，可能再次产生费用。</p><button type="button" :disabled="busy" @click="perform(job, 'confirm')">确认重新生成</button><button type="button" :disabled="busy" @click="perform(job, 'cancel')">放弃重试</button></template>
    </details>
  </section>
</template>

<style scoped>
.job-recovery{padding:8px 0;font-size:12px;overflow-wrap:anywhere}.job-recovery label{display:grid;gap:4px;margin:8px 0}.job-recovery input,.job-recovery textarea,.job-recovery select{max-width:100%;box-sizing:border-box;font:inherit;color:inherit;background:var(--surface,#fff);border:1px solid var(--line,#ccc);padding:6px}.job-recovery button{margin:4px;padding:6px;border:1px solid var(--line,#ccc);background:var(--surface,#fff);color:inherit;cursor:pointer}.job-recovery small{display:block}
</style>
