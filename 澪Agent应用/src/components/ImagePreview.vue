<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, ref } from 'vue'
import { Download, Minus, Plus, X } from '@lucide/vue'

const props = defineProps({ attachment: { type: Object, required: true } })
const emit = defineEmits(['close'])
const dialog = ref(null)
const stage = ref(null)
const closeButton = ref(null)
const natural = ref({ width: 0, height: 0 })
const viewport = ref({ width: 0, height: 0 })
const zoom = ref(1)
const failed = ref(false)
let observer
let previousFocus
let previousOverflow
const dimensions = computed(() => {
  const { width, height } = natural.value
  if (!width || !height) return {}
  const fit = Math.min(1, viewport.value.width / width, viewport.value.height / height)
  return { width: `${Math.max(1, width * fit * zoom.value)}px`, height: `${Math.max(1, height * fit * zoom.value)}px` }
})
function loaded(event) {
  natural.value = { width: event.target.naturalWidth, height: event.target.naturalHeight }
}
async function setZoom(value) {
  zoom.value = Math.max(1, Math.min(4, value))
  await nextTick()
  stage.value?.scrollTo({ left: (stage.value.scrollWidth - stage.value.clientWidth) / 2, top: (stage.value.scrollHeight - stage.value.clientHeight) / 2 })
}
function keydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); emit('close') }
  if (event.key !== 'Tab') return
  const controls = [...dialog.value.querySelectorAll('button:not(:disabled), a[href]')]
  const first = controls[0], last = controls.at(-1)
  if (event.shiftKey && (document.activeElement === first || !dialog.value.contains(document.activeElement))) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
onMounted(() => {
  previousFocus = document.activeElement
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  closeButton.value?.focus()
  observer = new ResizeObserver(([entry]) => { viewport.value = { width: entry.contentRect.width, height: entry.contentRect.height } })
  observer.observe(stage.value)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  document.body.style.overflow = previousOverflow
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>

<template>
  <Teleport to="body">
    <div class="chat-image-lightbox">
      <section ref="dialog" class="image-preview-dialog" role="dialog" aria-modal="true" :aria-label="props.attachment.name || '图片预览'" @keydown="keydown">
        <div class="image-preview-tools">
          <a :href="props.attachment.url" :download="props.attachment.name || '图片'" title="保存原图" aria-label="保存原图"><Download :size="19" /></a>
          <button ref="closeButton" type="button" title="关闭预览" aria-label="关闭预览" @click="emit('close')"><X :size="20" /></button>
        </div>
        <div ref="stage" class="image-preview-stage" @click.self="emit('close')">
          <div class="image-preview-canvas" @click.self="emit('close')">
            <p v-if="failed" role="alert">图片加载失败，请关闭预览后重试</p>
            <img v-else :src="props.attachment.url" :alt="props.attachment.name || '图片预览'" :style="dimensions" draggable="false" @load="loaded" @error="failed = true" @dblclick="setZoom(zoom === 1 ? 2 : 1)" />
          </div>
        </div>
        <div class="image-preview-zoom" aria-label="图片缩放">
          <button type="button" title="缩小" aria-label="缩小" :disabled="zoom <= 1 || failed" @click="setZoom(zoom - .25)"><Minus :size="18" /></button>
          <button type="button" class="image-preview-reset" title="恢复完整显示" @click="setZoom(1)">{{ Math.round(zoom * 100) }}%</button>
          <button type="button" title="放大" aria-label="放大" :disabled="zoom >= 4 || failed" @click="setZoom(zoom + .25)"><Plus :size="18" /></button>
        </div>
      </section>
    </div>
  </Teleport>
</template>
