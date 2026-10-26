<script setup>
import * as diaryApi from '../services/diaryApi.js'

import { onBeforeUnmount, ref, watch } from 'vue'
const props = defineProps({ diary: { type: Object, required: true }, confirm: { type: Function, required: true } })
const emit = defineEmits(['restored'])
const expanded = ref(false), versions = ref([]), comparison = ref(null), busy = ref(false), error = ref('')
let revision = 0, disposed = false
async function load() {
  const date = props.diary.date, n = ++revision
  busy.value = true; error.value = ''
  try {
    const result = await diaryApi.loadDiaryVersions(date, {})
    if (!disposed && n === revision && date === props.diary.date) versions.value = result.versions || []
  } catch (cause) { if (!disposed && n === revision) error.value = cause.message }
  finally { if (!disposed && n === revision) busy.value = false }
}
function toggle(event) {
  expanded.value = event.target.open
  if (expanded.value) void load()
}
async function compare(version) {
  if (busy.value) return
  const date = props.diary.date, n = ++revision
  busy.value = true; error.value = ''; comparison.value = null
  try {
    const result = await diaryApi.loadDiaryVersionDiff(date, version, {})
    if (!disposed && n === revision && date === props.diary.date) comparison.value = result
  } catch (cause) { if (!disposed && n === revision) error.value = cause.message }
  finally { if (!disposed && n === revision) busy.value = false }
}
async function restore(version) {
  if (busy.value) return
  const date = props.diary.date, current = props.diary.revision
  const confirmed = await props.confirm({ title: '恢复日记版本', message: `恢复 ${date} 的版本 ${version}。当前正文会保留为历史版本。`, confirmText: '恢复版本' })
  if (!confirmed || disposed || date !== props.diary.date || current !== props.diary.revision || busy.value) return
  const n = ++revision
  busy.value = true; error.value = ''
  try {
    await diaryApi.submitDiaryVersionRestore(date, version, { expected_revision: current }, {})
    if (!disposed && n === revision && date === props.diary.date) emit('restored', date)
  } catch (cause) { if (!disposed && n === revision) error.value = cause.message }
  finally { if (!disposed && n === revision) busy.value = false }
}
watch(() => [props.diary.date, props.diary.revision], () => {
  ++revision; versions.value = []; comparison.value = null; error.value = ''; busy.value = false
  if (expanded.value) void load()
})
onBeforeUnmount(() => { disposed = true; ++revision })
</script>
<template>
  <details class="diary-version-history" @toggle="toggle">
    <summary>日记历史版本</summary>
    <p v-if="busy" role="status">正在读取…</p><p v-if="error" role="alert">{{ error }} <button type="button" :disabled="busy" @click="load">重试</button></p>
    <article v-for="item in versions" :key="item.revision" :aria-label="`日记版本 ${item.revision}`">
      <div><strong>版本 {{ item.revision }}{{ item.revision === diary.revision ? ' · 当前' : '' }}</strong><small>{{ item.created_at }} · {{ item.change_reason || '保存记录' }}</small></div>
      <div class="version-actions"><button type="button" :disabled="busy" @click="compare(item.revision)">与当前比较</button><button type="button" :disabled="busy || item.revision === diary.revision" @click="restore(item.revision)">恢复此版本</button></div>
    </article>
    <section v-if="comparison" aria-label="日记版本差异"><strong>版本 {{ comparison.revision }} → 当前版本 {{ comparison.current_revision }}</strong><pre>{{ comparison.diff || '正文没有差异。' }}</pre></section>
  </details>
</template>
<style scoped>
.diary-version-history{margin-top:20px;border-top:1px solid var(--line);padding-top:14px;min-width:0}.diary-version-history summary{cursor:pointer;color:var(--muted)}article{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--line)}small{display:block;color:var(--muted);margin-top:4px;overflow-wrap:anywhere}.version-actions{display:flex;flex-wrap:wrap;gap:6px;flex-shrink:0}button{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:7px 10px;cursor:pointer}button:disabled{opacity:.5;cursor:default}pre{max-height:320px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid var(--line);padding:12px;border-radius:6px;font-size:13px}section{margin-top:14px}p[role=alert]{color:var(--danger)}@media(max-width:640px){article{align-items:flex-start;flex-direction:column}button{min-height:40px}}
</style>
