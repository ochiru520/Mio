<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
const props = defineProps({ scrollElement: Object, turns: { type: Array, default: () => [] }, name: { type: String, default: '澪' } })
const current = ref(0), preview = ref(null), previewTop = ref(12)
let resize, highlightTimer
const labels = computed(() => props.turns.filter(turn => turn.role === 'user').map(turn => ({
  anchor: `user-${turn.id}`,
  speaker: '你',
  text: (turn.parts?.map(part => part.content || '').join('\n') || turn.content || '发送了附件').replace(/\s+/g, ' ').trim().slice(0, 220),
})))
function rows() {
  const all = [...(props.scrollElement?.querySelectorAll('.message-row.user[data-message-anchor]') || [])]
  return labels.value.map(label => all.find(row => row.dataset.messageAnchor === label.anchor))
}
function update() {
  const scroll = props.scrollElement
  if (!scroll) return
  const top = scroll.getBoundingClientRect().top
  let index = 0
  rows().forEach((row, i) => { if (row && row.getBoundingClientRect().top <= top + 40) index = i })
  if (scroll.scrollTop > 0 && scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 2) index = Math.max(0, labels.value.length - 1)
  current.value = index
}
function show(index, event) {
  preview.value = { ...labels.value[index], index }
  const pane = event.currentTarget.closest('.message-navigator').getBoundingClientRect()
  const button = event.currentTarget.getBoundingClientRect()
  previewTop.value = Math.max(8, Math.min(pane.height - 160, button.top - pane.top - 45))
}
function jump(index, event) {
  const row = rows()[index], scroll = props.scrollElement
  if (!row || !scroll) return
  scroll.dispatchEvent(new Event('mio-chat-navigate', { bubbles: true }))
  scroll.scrollTo({ top: row.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop - 16, behavior: 'instant' })
  rows().forEach(item => item?.classList.remove('message-jump-target'))
  row.classList.add('message-jump-target')
  clearTimeout(highlightTimer); highlightTimer = window.setTimeout(() => row.classList.remove('message-jump-target'), 1200)
  current.value = index; show(index, event)
}
watch(() => props.scrollElement, (element, old) => {
  old?.removeEventListener('scroll', update); resize?.disconnect()
  element?.addEventListener('scroll', update, { passive: true })
  if (element) { resize = new ResizeObserver(update); resize.observe(element) }
}, { immediate: true })
watch(() => props.turns, async () => { preview.value = null; await nextTick(); update() }, { deep: true })
onBeforeUnmount(() => { props.scrollElement?.removeEventListener('scroll', update); resize?.disconnect(); clearTimeout(highlightTimer) })
</script>
<template>
  <nav v-if="labels.length" class="message-navigator" aria-label="对话消息导航" @mouseleave="preview = null" @keydown.esc="preview = null">
    <div class="message-ticks" @scroll="preview = null">
      <button v-for="(label, index) in labels" :key="label.anchor" type="button" :aria-label="`跳转到第 ${index + 1} 条用户发言：${label.text.slice(0, 60)}`" :aria-current="current === index ? 'true' : undefined" @mouseenter="show(index, $event)" @focus="show(index, $event)" @blur="preview = null" @click="jump(index, $event)"><i /></button>
    </div>
    <div v-if="preview" role="tooltip" class="message-tick-preview" :style="{ top: `${previewTop}px` }"><small>第 {{ preview.index + 1 }} 条发言 · {{ labels.length }} 条用户发言</small><strong>{{ preview.speaker }}</strong><p>{{ preview.text }}</p></div>
  </nav>
</template>
