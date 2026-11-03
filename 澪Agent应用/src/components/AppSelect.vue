<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { Check } from '../icons/index.js'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  modelValue: { type: [String, Number, Boolean], default: '' },
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
const enabledIndices = computed(() => props.options.flatMap((item, index) => item.disabled ? [] : [index]))
const unavailable = computed(() => props.disabled || !enabledIndices.value.length)
let query = ''
let lastKey = 0

function close() { opened.value = false; query = ''; lastKey = 0 }
function reveal() {
  nextTick(() => {
    const list = menu.value, item = list?.children[active.value]
    if (!list || !item) return
    if (item.offsetTop < list.scrollTop) list.scrollTop = item.offsetTop
    else if (item.offsetTop + item.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = item.offsetTop + item.offsetHeight - list.clientHeight
    }
  })
}
function place() {
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
}
function open() {
  if (unavailable.value) return
  place()
  const selectedIndex = props.options.findIndex(item => item.value === props.modelValue && !item.disabled)
  active.value = selectedIndex >= 0 ? selectedIndex : enabledIndices.value[0]
  opened.value = true
  reveal()
}
function choose(index) {
  const option = props.options[index]
  if (!option || option.disabled) return
  emit('update:modelValue', option.value)
  close()
  trigger.value?.focus()
}
function keydown(event) {
  if (unavailable.value) return
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
    const wasOpen = opened.value
    if (!wasOpen) open()
    const indices = enabledIndices.value
    if (event.key === 'Home') active.value = indices[0]
    else if (event.key === 'End') active.value = indices[indices.length - 1]
    else if (wasOpen) active.value = indices[(indices.indexOf(active.value) + (event.key === 'ArrowDown' ? 1 : -1) + indices.length) % indices.length]
    reveal()
  } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    if (!opened.value) open()
    query = Date.now() - lastKey > 700 ? event.key : query + event.key
    lastKey = Date.now()
    const found = props.options.findIndex(item => !item.disabled && item.label.toLocaleLowerCase().startsWith(query.toLocaleLowerCase()))
    if (found >= 0) { active.value = found; reveal() }
  }
}
function outside(event) {
  if (!trigger.value?.contains(event.target) && !menu.value?.contains(event.target)) close()
}
function scroll(event) {
  if (opened.value && !menu.value?.contains(event.target)) {
    const rect = trigger.value?.getBoundingClientRect()
    if (!rect || rect.bottom < 0 || rect.top > window.innerHeight) close()
    else place()
  }
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
  <button v-bind="$attrs" ref="trigger" class="app-select" type="button" role="combobox" aria-haspopup="listbox"
    :aria-label="label" :aria-expanded="opened" :aria-controls="`${id}-list`"
    :aria-activedescendant="opened ? `${id}-${active}` : undefined"
    :disabled="unavailable" @click="opened ? close() : open()" @keydown="keydown" @blur="close">
    <span>{{ selected?.label || '请选择' }}</span>
  </button>
  <Teleport to="body">
    <ul v-if="opened" :id="`${id}-list`" ref="menu" class="app-select-menu" role="listbox" :aria-label="label" :style="position" @pointerdown.prevent>
      <li v-for="(option, index) in options" :id="`${id}-${index}`" :key="option.value" role="option"
        :aria-selected="option.value === modelValue" :aria-disabled="Boolean(option.disabled)" :class="{ highlighted: index === active, 'disabled-option': option.disabled }"
        @pointermove="!option.disabled && (active = index)" @click="choose(index)">
        <span>{{ option.label }}</span><Check v-if="option.value === modelValue" :size="15" />
      </li>
    </ul>
  </Teleport>
</template>

<style scoped>
.app-select { display: flex; width: 100%; min-width: 0; min-height: 40px; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 12px; border: 1px solid var(--line); border-radius: var(--radius-sm, 6px); background: var(--surface-raised); color: var(--ink); font: inherit; font-size: var(--text-base); text-align: left; cursor: pointer; }
.app-select span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.app-select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.app-select:disabled { opacity: .5; cursor: default; }
.app-select-menu { position: fixed; z-index: 10000; margin: 0; padding: 5px; overflow: auto; list-style: none; border: 1px solid var(--line); border-radius: var(--radius-md, 10px); background: var(--surface-raised); color: var(--ink); font-family: inherit; box-shadow: var(--shadow-md); }
.app-select-menu li { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 38px; padding: 8px 10px; border-radius: max(0px, calc(var(--radius-md, 10px) - 5px)); font-size: var(--text-base); cursor: pointer; overflow-wrap: anywhere; }
.app-select-menu li.highlighted { background: var(--accent-soft); }
.app-select-menu li[aria-selected=true] { color: var(--accent-dark); font-weight: 650; }
.app-select-menu svg { flex-shrink: 0; }
.app-select-menu li.disabled-option { opacity: .5; cursor: default; }
</style>
