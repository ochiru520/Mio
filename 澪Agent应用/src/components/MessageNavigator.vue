<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as api from '../services/agentExperienceApi.js'
import { pauseReadingFollow,rememberReadingPosition } from '../conversationScroll.js'
const props = defineProps({ scrollElement: Object, turns: { type: Array, default: () => [] }, name: { type: String, default: '澪' },conversationId:String,loadTarget:Function,loadOlder:Function,hasMore:Boolean })
const current = ref(0), preview = ref(null), previewTop = ref(12)
const indexed=ref([]),indexMore=ref(false),indexBefore=ref(0),loadingIndex=ref(false),jumping=ref(false),error=ref('')
let version=0,disposed=false
async function loadIndex(older=false){if(!props.conversationId || loadingIndex.value)return;loadingIndex.value=true;const n=version;try{const data=await api.turnIndex(props.conversationId,older?indexBefore.value:0);if(!disposed && n===version){indexed.value=older?[...(data.turns||[]),...indexed.value]:(data.turns||[]);indexMore.value=Boolean(data.has_more);indexBefore.value=data.next_before_id||0;error.value=''}}catch(cause){if(n===version)error.value=cause.message}finally{loadingIndex.value=false;if(!disposed && n!==version)void loadIndex()}}
const userTurns=computed(()=>{const all=new Map(indexed.value.filter(item=>!item.role || item.role==='user').map(item=>[String(item.id),item]));for(const turn of props.turns.filter(turn=>turn.role==='user'))all.set(String(turn.id),turn);return [...all.values()].sort((a,b)=>Number(a.id)-Number(b.id))})
let resize, highlightTimer
const labels = computed(() => userTurns.value.map(turn => ({
  id:turn.id,
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
  pauseReadingFollow(props.conversationId,props.scrollElement)
  props.scrollElement?.dispatchEvent(new Event('mio-chat-navigate',{bubbles:true}))
  preview.value = { ...labels.value[index], index }
  const pane = event.currentTarget.closest('.message-navigator').getBoundingClientRect()
  const button = event.currentTarget.getBoundingClientRect()
  previewTop.value = Math.max(8, Math.min(pane.height - 160, button.top - pane.top - 45))
}
async function jump(index, event) {
  const target=labels.value[index],conversation=props.conversationId
  if(!target || jumping.value)return
  jumping.value=true;error.value=''
  try{if(!rows()[index] && props.loadTarget)await props.loadTarget(target.id);await nextTick()}catch(cause){error.value=cause.message}finally{jumping.value=false}
  if(conversation!==props.conversationId)return
  const actual=labels.value.findIndex(item=>item.anchor===target.anchor)
  const row = rows()[actual], scroll = props.scrollElement
  if (!row || !scroll) return
  scroll.dispatchEvent(new Event('mio-chat-navigate', { bubbles: true }))
  scroll.scrollTo({ top: row.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop - 16, behavior: 'instant' })
  rows().forEach(item => item?.classList.remove('message-jump-target'))
  row.classList.add('message-jump-target')
  clearTimeout(highlightTimer); highlightTimer = window.setTimeout(() => row.classList.remove('message-jump-target'), 1200)
  current.value = actual; rememberReadingPosition(props.conversationId,scroll);if(event.currentTarget)show(actual, event)
}
watch(()=>props.conversationId,()=>{++version;indexed.value=[];indexMore.value=false;indexBefore.value=0;void loadIndex()},{immediate:true})
watch(() => props.scrollElement, (element, old) => {
  old?.removeEventListener('scroll', update); resize?.disconnect()
  element?.addEventListener('scroll', update, { passive: true })
  if (element) { resize = new ResizeObserver(update); resize.observe(element) }
}, { immediate: true })
watch(() => props.turns, async () => { preview.value = null; await nextTick(); update() }, { deep: true })
onBeforeUnmount(() => { disposed=true;++version; props.scrollElement?.removeEventListener('scroll', update); resize?.disconnect(); clearTimeout(highlightTimer) })
</script>
<template>
  <nav v-if="labels.length" class="message-navigator" aria-label="对话消息导航" @mouseleave="preview = null" @keydown.esc="preview = null">
    <div class="message-ticks" @scroll="preview = null"><button v-if="indexMore || hasMore" type="button" :disabled="loadingIndex || jumping" aria-label="读取更早的用户发言刻度" @click="indexMore ? loadIndex(true) : loadOlder?.()">↑</button>
      <button v-for="(label, index) in labels" :key="label.anchor" type="button" :aria-label="`跳转到第 ${index + 1} 条用户发言：${label.text.slice(0, 60)}`" :aria-current="current === index ? 'true' : undefined" @mouseenter="show(index, $event)" @focus="show(index, $event)" @blur="preview = null" @click="jump(index, $event)"><i /></button>
    </div>
    <span v-if="jumping" class="navigator-status" role="status">正在定位…</span><span v-if="error" class="navigator-status" role="alert" :title="error">定位暂不可用</span>
    <div v-if="preview" role="tooltip" class="message-tick-preview" :style="{ top: `${previewTop}px` }"><small>第 {{ preview.index + 1 }} 条发言 · {{ labels.length }} 条用户发言</small><strong>{{ preview.speaker }}</strong><p>{{ preview.text }}</p></div>
  </nav>
</template>
