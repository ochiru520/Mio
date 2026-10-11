<script setup>
import { computed } from 'vue'
const props = defineProps({ thread: { type: Object, required: true } })
const emit = defineEmits(['open-diary'])
const sources = computed(() => props.thread.sources || [])
const stale = computed(() => props.thread.needs_recheck || sources.value.some(item => item.state === 'stale'))
const labels = { diary: '日记', memory: '记忆', message: '消息', artifact: '成果' }
function canOpen(source) { return source.source_type === 'diary' && /^\d{4}-\d{2}-\d{2}$/.test(source.source_id || '') }
</script>
<template>
  <div v-if="sources.length" class="pending-thread-sources">
    <p v-if="stale" role="alert">来源已更新，请先重新核对这件事。</p>
    <details><summary>来源与核对</summary><ul><li v-for="source in sources" :key="source.id">
      <span>{{ labels[source.source_type] || source.source_type }} · {{ source.source_id }}<template v-if="source.source_revision"> · v{{ source.source_revision }}</template> · {{ source.state === 'stale' ? '需要重新核对' : '已关联' }}</span>
      <button v-if="canOpen(source)" type="button" @click="emit('open-diary', source.source_id)">查看来源日记</button>
    </li></ul></details>
  </div>
</template>
<style scoped>
.pending-thread-sources { margin: 8px 0; font-size: calc(12px * var(--fs, 1)); overflow-wrap: anywhere; }
.pending-thread-sources p { color: var(--danger); margin: 6px 0; }
.pending-thread-sources summary { cursor: pointer; color: var(--text-muted); }
.pending-thread-sources ul { margin: 8px 0; padding-left: 18px; }
.pending-thread-sources li { margin: 6px 0; }
.pending-thread-sources button { margin: 4px 0 4px 8px; padding: 4px 8px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); cursor: pointer; }
</style>
