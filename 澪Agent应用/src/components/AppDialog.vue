<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { AlertTriangle, X } from '@lucide/vue'
import { focusModal, restoreModalFocus, trapModalFocus } from '../modalFocus.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  mode: { type: String, default: 'confirm' },
  title: { type: String, default: '请确认' },
  message: { type: String, default: '' },
  modelValue: { type: String, default: '' },
  confirmText: { type: String, default: '确定' },
  cancelText: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },
  multiline: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'confirm', 'cancel'])
const dialogRef = ref(null)
let focusBeforeOpen = null

watch(() => props.open, async (open) => {
  if (open) {
    focusBeforeOpen = document.activeElement
    await nextTick()
    focusModal(dialogRef.value)
    return
  }
  const target = focusBeforeOpen
  focusBeforeOpen = null
  await nextTick()
  restoreModalFocus(target)
})

onBeforeUnmount(() => restoreModalFocus(focusBeforeOpen))

function cancelDialog() {
  emit('cancel')
}

function onDialogKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    cancelDialog()
    return
  }
  trapModalFocus(event, dialogRef.value)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop app-dialog-backdrop" @click.self="cancelDialog">
      <section ref="dialogRef" class="app-dialog" role="dialog" aria-modal="true" :aria-label="title" tabindex="-1" @keydown="onDialogKeydown">
      <header>
        <span :class="['app-dialog-icon', { danger }]">
          <AlertTriangle :size="18" />
        </span>
        <div>
          <h2>{{ title }}</h2>
          <p v-if="message">{{ message }}</p>
        </div>
        <button class="icon-button" type="button" title="关闭" data-modal-initial-focus @click="cancelDialog"><X :size="17" /></button>
      </header>
      <textarea
        v-if="mode === 'prompt' && multiline"
        class="app-dialog-input app-dialog-textarea"
        :value="modelValue"
        autofocus
        rows="5"
        @input="emit('update:modelValue', $event.target.value)"
        @keydown.ctrl.enter="emit('confirm')"
      />
      <input
        v-else-if="mode === 'prompt'"
        class="app-dialog-input"
        :value="modelValue"
        autofocus
        @input="emit('update:modelValue', $event.target.value)"
        @keydown.enter="emit('confirm')"
      />
      <footer>
        <button type="button" @click="cancelDialog">{{ cancelText }}</button>
        <button :class="danger ? 'danger-button' : 'primary-button'" type="button" @click="emit('confirm')">{{ confirmText }}</button>
      </footer>
      </section>
    </div>
  </Teleport>
</template>
