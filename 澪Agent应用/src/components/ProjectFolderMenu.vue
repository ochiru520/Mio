<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Trash2 } from '@lucide/vue'
const props = defineProps({ project: Object, x: Number, y: Number, busy: Boolean, directoryCreated: Boolean })
const emit = defineEmits(['close', 'delete', 'delete-directory'])
const menu = ref(null), left = ref(props.x), top = ref(props.y)
function outside(event) { if (!menu.value?.contains(event.target)) emit('close') }
function escape(event) { if (event.key === 'Escape') emit('close') }
onMounted(async () => {
  await nextTick(); const box = menu.value.getBoundingClientRect()
  left.value = Math.max(8, Math.min(props.x, innerWidth - box.width - 8)); top.value = Math.max(8, Math.min(props.y, innerHeight - box.height - 8))
  menu.value.querySelector('button').focus(); document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape)
})
onBeforeUnmount(() => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) })
</script>
<template>
  <Teleport to="body"><div ref="menu" class="project-folder-menu" role="menu" :aria-label="`${project.name}的项目菜单`" :style="{ left: `${left}px`, top: `${top}px` }">
    <button v-if="directoryCreated" type="button" role="menuitem" class="danger" :disabled="busy" @click="emit('delete-directory', project)"><Trash2 :size="15" />删除项目文件夹</button>
    <button type="button" role="menuitem" :disabled="busy" @click="emit('delete', project)"><Trash2 :size="15" />删除项目</button>
  </div></Teleport>
</template>
<style scoped>
.project-folder-menu { position: fixed; z-index: 2000; width: 180px; max-width: calc(100vw - 16px); padding: 6px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow-lg); }
button { display: flex; align-items: center; gap: 9px; width: 100%; padding: 10px; border: 0; border-radius: 6px; background: transparent; color: var(--danger); font: inherit; font-size: calc(13px * var(--fs, 1)); cursor: pointer; }
.danger { color: var(--danger); }
button:hover { background: var(--danger-soft); }
button:disabled { opacity: .5; }
</style>
