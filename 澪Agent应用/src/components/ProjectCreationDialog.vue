<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { X } from '../icons/index.js'
import ProjectDirectoryForm from './ProjectDirectoryForm.vue'
const props = defineProps({ busy: Boolean })
const emit = defineEmits(['submit', 'close'])
const dialog = ref(null)
let returnFocus
function cancel(event) { event.preventDefault(); if (!props.busy) emit('close') }
onMounted(async () => { returnFocus = document.activeElement; await nextTick(); dialog.value.showModal() })
onBeforeUnmount(() => { dialog.value?.close(); if (returnFocus?.isConnected) returnFocus.focus() })
</script>
<template>
  <Teleport to="body">
    <dialog ref="dialog" class="project-creation-dialog" aria-labelledby="project-creation-title" @cancel="cancel" @click="event => { if (event.target === dialog && !busy) emit('close') }">
      <header><h2 id="project-creation-title">新建项目</h2><button type="button" aria-label="关闭新建项目" :disabled="busy" @click="emit('close')"><X :size="18" /></button></header>
      <ProjectDirectoryForm :busy="busy" @submit="emit('submit', $event)" @cancel="emit('close')" />
    </dialog>
  </Teleport>
</template>
<style scoped>
.project-creation-dialog { width: min(470px, calc(100vw - 32px)); max-height: calc(100vh - 40px); overflow: auto; margin: auto; padding: 24px; border: 1px solid var(--border-strong); border-radius: 16px; background: var(--surface); color: var(--text); box-shadow: var(--shadow-pop); font-size: calc(13px * var(--fs, 1)); }
.project-creation-dialog::backdrop { background: var(--overlay); }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 22px; }
h2 { margin: 0; font-size: calc(18px * var(--fs, 1)); font-weight: 600; }
header button { display: grid; place-items: center; padding: 6px; border: 0; border-radius: 6px; background: transparent; color: var(--text-muted); cursor: pointer; }
header button:hover { background: var(--surface-soft); }
</style>
