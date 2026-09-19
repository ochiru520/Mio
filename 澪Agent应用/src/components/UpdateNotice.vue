<script setup>
import { ref } from 'vue'
import { useUpdates } from '../composables/useUpdates.js'
const { state } = useUpdates()
const dismissed = ref('')
defineEmits(['open'])
</script>
<template>
  <aside v-if="['available', 'ready'].includes(state.state) && state.release?.version !== dismissed" class="mio-update-notice" aria-label="新版本通知">
    <span>{{ state.state === 'ready' ? '更新已下载' : '发现新版本' }} {{ state.release?.version }}</span>
    <button type="button" @click="$emit('open')">查看更新</button>
    <button type="button" aria-label="稍后提醒" @click="dismissed = state.release?.version">×</button>
  </aside>
</template>
<style scoped>
.mio-update-notice { position: fixed; right: 22px; bottom: 22px; z-index: 100; max-width: calc(100vw - 44px); display: flex; align-items: center; gap: 12px; padding: 12px 16px; background: var(--surface, #fff); color: var(--text, #24343a); border: 1px solid var(--line, #dbe4e5); border-radius: 12px; box-shadow: 0 4px 18px #0002; font-size: 13px; }
.mio-update-notice button { white-space: nowrap; }
</style>
