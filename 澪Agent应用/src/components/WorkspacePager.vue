<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
const props=defineProps({ label: String, offset: { type: Number, default: 0 }, hasMore: Boolean, busy: Boolean })
const emit = defineEmits(['page'])
const marker=ref(null)
let observer, visible=false, pending=false
async function loadVisible() {
  await nextTick()
  if (!visible || pending || props.busy || !props.hasMore) return
  pending=true
  emit('page',1)
}
watch(() => [props.hasMore,props.busy,props.offset], () => { if (!props.busy) pending=false; void loadVisible() })
onMounted(() => { if (typeof IntersectionObserver==='undefined') return; observer=new IntersectionObserver(entries => { visible=entries.some(item => item.isIntersecting); void loadVisible() },{rootMargin:'80px'}); observer.observe(marker.value) })
onBeforeUnmount(() => observer?.disconnect())
</script>
<template><div ref="marker" class="workspace-pager"><button v-if="hasMore" :disabled="busy" @click="emit('page', 1)">{{ busy ? '正在读取…' : `加载更多${label || ''}` }}</button></div></template>
<style scoped>.workspace-pager{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:12px 0}</style>
