<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { apiRequest } from '../services/api.js'

const props = defineProps({ projectId: { type: String, required: true }, mobile: Boolean })
const emit = defineEmits(['expired'])
const items = ref([]), busy = ref(false), error = ref(''), loaded = ref(false)
let version = 0, disposed = false
async function load() {
  if (!props.projectId) return
  const requestVersion = ++version, project = props.projectId
  const current = () => !disposed && version === requestVersion && props.projectId === project
  busy.value = true; error.value = ''
  try { const value = await apiRequest(`/api/workspace/projects/${project}/learning/review?limit=50`, { headers: props.mobile ? { 'X-Mio-Mobile': '1' } : {} }); if (current()) { items.value = value.items || []; loaded.value = true } }
  catch (cause) { if (current()) { if (cause.status === 401 && props.mobile) emit('expired'); error.value = cause.message } }
  finally { if (current()) busy.value = false }
}
watch(() => props.projectId, () => { ++version; busy.value = false; loaded.value = false; items.value = []; error.value = '' })
onBeforeUnmount(() => { disposed = true; ++version })
</script>

<template>
  <section class="hub-panel hub-form learning-review-panel">
    <div class="hub-actions"><div><h2>今日复习</h2><p class="hub-muted">未作答、低分或已到复习时间的题目会集中在这里。</p></div><button :disabled="busy" @click="load">{{ loaded ? '重新检查' : '加载复习题' }}</button></div>
    <p v-if="error" class="hub-error" role="alert">{{ error }}</p>
    <p v-else-if="loaded && !items.length" class="hub-muted">当前没有需要复习的题目。</p>
    <article v-for="item in items" :key="item.id" class="hub-observation"><strong>{{ item.session_title }} · {{ item.prompt }}</strong><small v-if="item.latest_score !== null && item.latest_score !== undefined">上次得分 {{ Math.round(item.latest_score * 100) }}% · {{ item.latest_feedback }}</small><small v-else>尚未作答</small><small v-if="item.review_at">下次复习：{{ item.review_at }}</small></article>
  </section>
</template>
