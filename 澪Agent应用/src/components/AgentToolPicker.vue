<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, ChevronDown, Image, Sparkles, Video, Wrench } from '../icons/index.js'
import { loadCreationConfiguration, loadCreationWorkflows } from '../services/creationApi.js'

const props = defineProps({ modelValue: { type: String, default: '' }, disabled: Boolean, mode: { type: String, default: '' } })
const emit = defineEmits(['update:modelValue'])
const root = ref(null)
const trigger = ref(null)
const open = ref(false)
const loading = ref(false)
const error = ref('')
const workflows = ref([])
const imageDefault = ref('')
const automaticChosen = ref(false)
const shortLabels = { 'anima-2.9b-image': 'Anima 生图', 'qwen-2.1-image': '千问生图', 'minimax-h3-video': 'H3 生视频' }
const selected = computed(() => workflows.value.find(item => item.id === props.modelValue))
const label = computed(() => !props.modelValue ? '工具' : shortLabels[props.modelValue] || selected.value?.label || '已选工作流')
const options = computed(() => [
  { id: '', label: '自动调用', hint: '由澪按任务选择，可组合使用工具', icon: Sparkles, available: true },
  ...workflows.value.map(item => ({ ...item, label: shortLabels[item.id] || item.label,
    hint: item.available === false ? item.missing_reason || '工作流暂不可用' : item.id === 'anima-2.9b-image' ? '二次元插画 · 无需参考图' : item.id === 'qwen-2.1-image' ? '中文生图 · 支持参考图编辑' : item.media_type === 'video' ? '图片转视频 · 需要一张参考图' : item.description,
    icon: item.media_type === 'video' ? Video : Image,
  })),
])
function preferredImageWorkflow() {
  return workflows.value.find(item => item.available !== false && item.media_type === 'image' && (!imageDefault.value || item.id === imageDefault.value))
}
function ensureModeDefault() {
  if (props.mode !== 'image' || props.modelValue || automaticChosen.value) return
  const preferred = preferredImageWorkflow()
  if (preferred) emit('update:modelValue', preferred.id)
}

async function refresh() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const [data, configuration] = await Promise.all([loadCreationWorkflows(), loadCreationConfiguration()])
    if (!Array.isArray(data.workflows)) throw new Error('工具列表格式不正确')
    imageDefault.value = configuration.defaults?.image_workflow_id || ''
    workflows.value = data.workflows
    ensureModeDefault()
  } catch {
    error.value = '工具列表加载失败，请重试'
  } finally { loading.value = false }
}
watch(() => props.mode, () => { automaticChosen.value = false; ensureModeDefault() })
watch(workflows, ensureModeDefault)
async function toggle() {
  open.value = !open.value
  if (!open.value) return
  void refresh()
  await nextTick()
  const menu = root.value?.querySelector('[role="menu"]')
  if (!menu?.contains(document.activeElement)) root.value?.querySelector('[role="menuitemradio"][aria-checked="true"]')?.focus()
}
function choose(option) {
  if (props.disabled || option.available === false) return
  automaticChosen.value = !option.id
  emit('update:modelValue', option.id)
  open.value = false
  trigger.value?.focus()
}
function closeOutside(event) {
  if (!root.value?.contains(event.target)) open.value = false
}
function navigate(event) {
  if (event.key === 'Escape') {
    open.value = false
    trigger.value?.focus()
    event.preventDefault()
    event.stopPropagation()
  } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    const items = [...root.value.querySelectorAll('[role="menuitemradio"]:not(:disabled)')]
    const current = items.indexOf(document.activeElement)
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (current + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length
    items[index]?.focus()
    event.preventDefault()
  }
}
function leave(event) {
  if (!root.value?.contains(event.relatedTarget)) open.value = false
}
onMounted(() => { document.addEventListener('pointerdown', closeOutside); void refresh() })
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeOutside))
</script>

<template>
  <div ref="root" class="agent-tool-picker" @keydown="navigate" @focusout="leave">
    <button ref="trigger" type="button" class="agent-tool-mode" :class="{ selected: modelValue }" :disabled="disabled" aria-haspopup="menu" :aria-expanded="open" :aria-label="`切换工具，当前${label}`" @click="toggle">
      <Wrench :size="14" /><span>{{ label }}</span><ChevronDown :size="12" />
    </button>
    <div v-if="open" class="agent-tool-popover" role="menu" aria-label="选择任务工具">
      <header>选择工具<small>用于接下来发送的任务</small></header>
      <button v-for="option in options" :key="option.id" type="button" role="menuitemradio" :aria-checked="modelValue === option.id" :disabled="disabled || option.available === false" @click="choose(option)">
        <component :is="option.icon" :size="17" />
        <span><strong>{{ option.label }}</strong><small>{{ option.hint }}</small></span>
        <Check v-if="modelValue === option.id" :size="16" />
      </button>
      <p v-if="loading" role="status">正在更新工具…</p>
      <p v-if="error" role="alert">{{ error }} <button type="button" @click="refresh">重试</button></p>
      <p v-if="modelValue && !loading && !error && !selected" role="alert">所选工作流已移除，请重新选择。</p>
      <div class="agent-tool-extras" @click="open = false"><slot name="extras" /></div><footer>文件读取等辅助工具仍由澪按需调用</footer>
    </div>
  </div>
</template>

<style scoped>
.agent-tool-picker { position: relative; flex: 0 1 auto; min-width: 0; }
.agent-tool-mode { display: inline-flex; align-items: center; gap: 5px; max-width: 150px; height: 30px; padding: 0 8px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--text-muted); font: inherit; font-size: calc(12px * var(--fs, 1)); cursor: pointer; }
.agent-tool-mode span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-tool-mode svg { flex-shrink: 0; }
.agent-tool-mode:hover, .agent-tool-mode.selected, .agent-tool-mode[aria-expanded="true"] { color: var(--view-accent-dark); background: var(--view-accent-soft); }
.agent-tool-mode:disabled { opacity: .55; cursor: default; }
.agent-tool-mode:focus-visible, .agent-tool-popover button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.agent-tool-popover { position: absolute; z-index: 40; bottom: calc(100% + 10px); left: 0; width: min(302px, calc(100vw - 90px)); max-height: min(440px, 65vh); overflow: auto; padding: 7px; border: 1px solid var(--border-strong); border-radius: 10px; background: var(--surface); box-shadow: 0 8px 30px var(--shadow-tint); }
.agent-tool-popover header { display: flex; justify-content: space-between; align-items: baseline; padding: 7px 8px 10px; font-size: calc(12px * var(--fs, 1)); color: var(--text); }
.agent-tool-popover header small, .agent-tool-popover footer, .agent-tool-popover p { color: var(--text-faint); font-size: calc(12px * var(--fs, 1)); }
.agent-tool-popover > button { display: flex; align-items: center; gap: 10px; width: 100%; padding: 10px 9px; border: 0; border-radius: 6px; background: transparent; color: var(--text-muted); text-align: left; cursor: pointer; }
.agent-tool-popover > button:hover, .agent-tool-popover > button[aria-checked="true"] { color: var(--view-accent-dark); background: var(--view-accent-soft); }
.agent-tool-popover > button:disabled { opacity: .45; cursor: not-allowed; }
.agent-tool-popover > button > svg { flex-shrink: 0; }
.agent-tool-popover > button > span { display: grid; gap: 4px; flex: 1; min-width: 0; }
.agent-tool-popover strong { font-size: calc(12px * var(--fs, 1)); font-weight: 500; }
.agent-tool-popover small { color: var(--text-faint); font-size: calc(12px * var(--fs, 1)); line-height: 1.5; }
.agent-tool-popover footer { margin: 6px 7px 2px; padding-top: 9px; border-top: 1px solid var(--border); line-height: 1.5; }
.agent-tool-popover p { margin: 8px; line-height: 1.5; }
.agent-tool-popover p button { background: transparent; border: 0; color: var(--view-accent-dark); cursor: pointer; }
</style>
