<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { Check, ChevronDown } from '@lucide/vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  label: { type: String, required: true },
  disabled: Boolean,
})
const emit = defineEmits(['update:modelValue'])
const id = useId()
const trigger = ref(null)
const menu = ref(null)
const opened = ref(false)
const active = ref(0)
const position = ref({})
const selected = computed(() => props.options.find(item => item.value === props.modelValue))
let query = ''
let lastKey = 0

function close() { opened.value = false }
function reveal() {
  nextTick(() => menu.value?.children[active.value]?.scrollIntoView({ block: 'nearest' }))
}
function open() {
  if (props.disabled || !props.options.length) return
  const rect = trigger.value.getBoundingClientRect()
  const below = window.innerHeight - rect.bottom - 12
  const above = rect.top - 12
  const up = below < 200 && above > below
  position.value = {
    left: `${Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8))}px`,
    width: `${Math.min(rect.width, window.innerWidth - 16)}px`,
    maxHeight: `${Math.max(60, Math.min(300, up ? above : below))}px`,
    ...(up ? { bottom: `${window.innerHeight - rect.top + 5}px` } : { top: `${rect.bottom + 5}px` }),
  }
  active.value = Math.max(0, props.options.findIndex(item => item.value === props.modelValue))
  opened.value = true
  reveal()
}
function choose(index) {
  const option = props.options[index]
  if (!option) return
  emit('update:modelValue', option.value)
  close()
  trigger.value?.focus()
}
function keydown(event) {
  if (event.key === 'Tab') { close(); return }
  if (event.key === 'Escape') { event.preventDefault(); close(); return }
  if (['Enter', ' '].includes(event.key)) {
    event.preventDefault()
    if (opened.value) choose(active.value)
    else open()
    return
  }
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault()
    if (!opened.value) { open(); return }
    if (event.key === 'Home') active.value = 0
    else if (event.key === 'End') active.value = props.options.length - 1
    else active.value = (active.value + (event.key === 'ArrowDown' ? 1 : -1) + props.options.length) % props.options.length
    reveal()
  } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    if (!opened.value) open()
    query = Date.now() - lastKey > 700 ? event.key : query + event.key
    lastKey = Date.now()
    const found = props.options.findIndex(item => item.label.toLocaleLowerCase().startsWith(query.toLocaleLowerCase()))
    if (found >= 0) { active.value = found; reveal() }
  }
}
function outside(event) {
  if (!trigger.value?.contains(event.target) && !menu.value?.contains(event.target)) close()
}
function scroll(event) {
  if (!menu.value?.contains(event.target)) close()
}
onMounted(() => {
  document.addEventListener('pointerdown', outside)
  window.addEventListener('resize', close)
  document.addEventListener('scroll', scroll, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', outside)
  window.removeEventListener('resize', close)
  document.removeEventListener('scroll', scroll, true)
})
watch(() => [props.options, props.disabled], close)
</script>

<template>
  <button ref="trigger" class="app-select" type="button" role="combobox" aria-haspopup="listbox"
    :aria-label="label" :aria-expanded="opened" :aria-controls="`${id}-list`"
    :aria-activedescendant="opened ? `${id}-${active}` : undefined"
    :disabled="disabled || !options.length" @click="opened ? close() : open()" @keydown="keydown" @blur="close">
    <span>{{ selected?.label || '请选择' }}</span><ChevronDown :size="15" :class="{ opened }" />
  </button>
  <Teleport to="body">
    <ul v-if="opened" :id="`${id}-list`" ref="menu" class="app-select-menu" role="listbox" :aria-label="label" :style="position" @pointerdown.prevent>
      <li v-for="(option, index) in options" :id="`${id}-${index}`" :key="option.value" role="option"
        :aria-selected="option.value === modelValue" :class="{ highlighted: index === active }"
        @pointermove="active = index" @click="choose(index)">
        <span>{{ option.label }}</span><Check v-if="option.value === modelValue" :size="15" />
      </li>
    </ul>
  </Teleport>
</template>

<style scoped>
.app-select { display: flex; width: 100%; min-width: 0; min-height: 40px; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 12px; border: 1px solid var(--line); border-radius: 9px; background: #fff; color: var(--ink); font: inherit; font-size: 12px; text-align: left; cursor: pointer; }
.app-select span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.app-select svg { flex-shrink: 0; color: var(--muted); transition: transform .15s; }
.app-select svg.opened { transform: rotate(180deg); }
.app-select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.app-select:disabled { opacity: .5; cursor: default; }
.app-select-menu { position: fixed; z-index: 10000; margin: 0; padding: 5px; overflow: auto; list-style: none; border: 1px solid var(--line); border-radius: 11px; background: #fff; color: var(--ink); box-shadow: 0 12px 36px #263c4a24; }
.app-select-menu li { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 38px; padding: 8px 10px; border-radius: 7px; font-size: 12px; cursor: pointer; overflow-wrap: anywhere; }
.app-select-menu li.highlighted { background: var(--accent-soft); }
.app-select-menu li[aria-selected=true] { color: var(--accent-dark); font-weight: 650; }
.app-select-menu svg { flex-shrink: 0; }
</style>
