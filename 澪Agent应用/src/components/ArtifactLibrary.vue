<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Archive, RefreshCw } from '@lucide/vue'
import { apiRequest } from '../services/api.js'
const artifacts = ref([])
const loading = ref(false)
const error = ref('')
let timer
function sizeLabel(value) { const bytes = Number(value || 0); return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(2)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB` }
async function load() {
  if (loading.value) return
  loading.value = true
  try { artifacts.value = (await apiRequest('/api/artifacts?limit=30')).artifacts || []; error.value = '' }
  catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}
onMounted(() => { void load(); timer = window.setInterval(load, 6000) })
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section class="artifact-library">
    <header><div><h3><Archive :size="17" />成果库</h3><small>已验证内容哈希；工作流目录变化不会影响历史成果</small></div><button type="button" title="刷新成果库" :disabled="loading" @click="load"><RefreshCw :class="{ spin: loading }" :size="15" /></button></header>
    <p v-if="error" class="artifact-error">{{ error }}</p><p v-else-if="!artifacts.length && !loading" class="artifact-empty">还没有归档成果</p>
    <div v-else class="artifact-list"><article v-for="item in artifacts" :key="item.id"><div><strong>{{ item.name }}</strong><small>{{ item.producer }} · {{ sizeLabel(item.size) }} · v{{ item.version }} · 内容已校验</small></div><a :href="item.url" :download="item.name">下载</a></article></div>
  </section>
</template>

<style scoped>
.artifact-library{padding:18px 0;border-top:1px solid #d7deda}.artifact-library>header{display:flex;justify-content:space-between;align-items:center}.artifact-library h3{display:flex;align-items:center;gap:7px;margin:0;font-size:15px}.artifact-library header small{display:block;color:#64706c;margin-top:4px}.artifact-library header button{display:flex;border:1px solid #cbd6d0;background:var(--surface,#fff);border-radius:5px;padding:6px;cursor:pointer}.artifact-list{display:grid;gap:6px;margin-top:12px}.artifact-list article{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px;border-bottom:1px solid #e4e9e5}.artifact-list article div{min-width:0}.artifact-list strong,.artifact-list small{display:block;overflow-wrap:anywhere}.artifact-list small{color:#718078;margin-top:3px;font-size:11px}.artifact-list a{color:#356957;font-size:12px;white-space:nowrap}.artifact-error{color:#a24237}.artifact-empty{color:#728078;font-size:12px}
</style>
