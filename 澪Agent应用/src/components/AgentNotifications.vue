<script setup>
import { onBeforeUnmount, onMounted, watch } from 'vue'
import * as api from '../services/agentExperienceApi.js'
import { createNotificationState } from '../notificationState.js'
const emit = defineEmits(['open', 'count'])
const state = createNotificationState(api)
const { error, open, visible, pending, completed, load, dismiss } = state
let timer
watch(() => pending.value.length, count => emit('count', count), { immediate: true })
watch(open, async value => { if (value) { await load(); await state.present() } })
function navigate(item) { const target = state.target(item); if (target) { open.value = false; emit('open', target) } }
function foreground() { if (!document.hidden) void load() }
onMounted(() => { void load(); timer = setInterval(load, 6000); document.addEventListener('visibilitychange', foreground) })
onBeforeUnmount(() => { state.dispose(); clearInterval(timer); document.removeEventListener('visibilitychange', foreground) })
</script>
<template>
  <div class="agent-notifications"><button type="button" :aria-expanded="open" aria-label="任务消息" @click="open=!open">消息<span v-if="pending.length"> {{ pending.length }}</span></button><section v-if="open" class="notification-popover" @keydown.esc="open=false"><header><strong>任务消息</strong><button type="button" @click="open=false">关闭</button></header><p v-if="error" role="alert">{{ error }}</p><template v-for="group in [{title:'待处理',items:pending},{title:'已完成',items:completed}]" :key="group.title"><h4 v-if="group.items.length">{{ group.title }}</h4><article v-for="item in group.items" :key="`${item.task_id}:${item.id}`"><button type="button" @click="navigate(item)">{{ item.title }}</button><button type="button" aria-label="隐藏消息" @click="dismiss(item)">×</button></article></template><p v-if="!visible.length">暂时没有新消息。</p><small>隐藏提醒会保留任务；待处理状态由实际任务结果更新。</small></section></div>
</template>
<style scoped>
.agent-notifications{position:relative}.agent-notifications button{background:transparent;border:0;color:var(--text);font:inherit;cursor:pointer;padding:7px}.notification-popover{position:absolute;right:0;top:100%;z-index:80;width:min(340px,80vw);max-height:60vh;overflow:auto;padding:18px;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-lg);font-size:calc(13px * var(--fs,1))}.notification-popover header,.notification-popover article{display:flex;justify-content:space-between;gap:12px;align-items:center}.notification-popover article>button:first-child{text-align:left;overflow-wrap:anywhere}.notification-popover small,.notification-popover p{color:var(--text-muted);line-height:1.8}
</style>
