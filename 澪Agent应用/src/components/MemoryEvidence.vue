<script setup>
import { ref, watch } from 'vue'
import { apiRequest } from '../services/api.js'
const props = defineProps({ memory: { type: Object, required: true } })
const evidence = ref(null)
const error = ref('')
const loading = ref(false)
let generation = 0
watch(() => props.memory.id, () => { generation++; evidence.value = null; error.value = ''; loading.value = false })
async function load(event) {
  if (!event.target.open || evidence.value || loading.value) return
  const current = ++generation
  loading.value = true
  error.value = ''
  try {
    const value = await apiRequest(`/api/memory/items/${props.memory.id}/evidence`)
    if (generation === current) evidence.value = value
  } catch (cause) { if (generation === current) error.value = cause.message }
  finally { if (generation === current) loading.value = false }
}
</script>

<template>
  <details @toggle="load">
    <summary>查看依据与修订</summary>
    <p v-if="loading">正在读取依据…</p><p v-if="error" role="alert">{{ error }}</p>
    <template v-if="evidence">
      <p>{{ { user_confirmed: '用户已确认或修正', model_extracted: '模型从消息中提取，可能存在推断', unverified_origin: '旧记录或手动录入，原始依据未确认' }[evidence.kind] }}</p>
      <small>置信度 {{ Math.round(Number(memory.confidence || 0) * 100) }}% · {{ memory.learned_at || memory.created_at }}</small>
      <blockquote v-if="evidence.source" style="white-space: pre-wrap; overflow-wrap: anywhere">{{ evidence.source.content }}<footer>{{ evidence.source.role === 'user' ? '用户原话' : '助手消息' }} · {{ evidence.source.created_at }}</footer></blockquote>
      <p v-else>原始消息不存在或未关联；不补造依据。</p>
      <ul v-if="evidence.events.length"><li v-for="event in evidence.events" :key="event.id">{{ { correct: '修正', archive: '停用', confirm: '确认', restore: '恢复版本' }[event.action] }} · {{ event.created_at }}</li></ul>
    </template>
  </details>
</template>
