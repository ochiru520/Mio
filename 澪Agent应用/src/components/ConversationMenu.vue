<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
const props = defineProps({ conversation: Object, projects: Array, projectId: String, x: Number, y: Number, busy: Boolean })
const emit = defineEmits(['close','action'])
const menu = ref(null), left = ref(props.x), top = ref(props.y)
const projectOpen = ref(false)
const destinationProjects = computed(() => (props.projects || []).filter(project => project.id !== props.projectId))
const canMoveProject = computed(() => Boolean(props.projectId) || destinationProjects.value.length > 0)
function outside(event) { if (!menu.value?.contains(event.target)) emit('close') }
function escape(event) { if (event.key === 'Escape') emit('close') }
async function positionMenu() {
  await nextTick()
  if (!menu.value) return
  const box = menu.value.getBoundingClientRect()
  left.value = Math.max(8, Math.min(props.x, innerWidth - box.width - 8))
  top.value = Math.max(8, Math.min(props.y, innerHeight - box.height - 8))
}
async function toggleProjects() { projectOpen.value = !projectOpen.value; await positionMenu() }
onMounted(async () => { await positionMenu(); menu.value?.focus(); document.addEventListener('pointerdown',outside); document.addEventListener('keydown',escape) })
onBeforeUnmount(() => { document.removeEventListener('pointerdown',outside); document.removeEventListener('keydown',escape) })
</script>
<template>
  <Teleport to="body"><div ref="menu" class="agent-conversation-menu" role="menu" aria-label="对话菜单" tabindex="-1" :style="{left: `${left}px`,top: `${top}px`}">
    <button type="button" role="menuitem" :disabled="busy" @click="emit('action','rename')">重命名</button>
    <button type="button" role="menuitem" :disabled="busy" @click="emit('action','pin')">{{ conversation.pinned ? '取消置顶' : '置顶' }}</button>
    <template v-if="canMoveProject">
      <button type="button" role="menuitem" :disabled="busy" :aria-expanded="projectOpen" aria-haspopup="menu" @click="toggleProjects">移动到项目<span class="project-chevron" aria-hidden="true">{{ projectOpen ? '⌄' : '›' }}</span></button>
      <div v-if="projectOpen" class="project-destinations" role="menu" aria-label="选择目标项目">
        <button v-if="projectId" type="button" role="menuitem" :disabled="busy" @click="emit('action','project','')">移出当前项目</button>
        <button v-for="project in destinationProjects" :key="project.id" type="button" role="menuitem" :disabled="busy" :title="project.name" @click="emit('action','project',project.id)">{{ project.name }}</button>
      </div>
    </template>
    <hr /><button type="button" role="menuitem" :disabled="busy" @click="emit('action','archive')">{{ conversation.archived ? '取消归档' : '归档' }}</button>
    <button type="button" role="menuitem" class="danger" :disabled="busy" @click="emit('action','delete')">删除对话</button>
  </div></Teleport>
</template>
<style scoped>
.agent-conversation-menu{position:fixed;z-index:2000;box-sizing:border-box;width:220px;max-width:calc(100vw - 16px);max-height:calc(100vh - 16px);overflow-y:auto;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:5px;box-shadow:var(--shadow-lg);font-size:calc(13px * var(--fs,1))}.agent-conversation-menu button{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;min-height:36px;padding:8px 11px;background:none;border:0;border-radius:6px;font:inherit;color:var(--text);text-align:left;cursor:pointer;overflow-wrap:anywhere}.agent-conversation-menu button:hover:not(:disabled){background:var(--accent-soft)}.agent-conversation-menu button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}.project-chevron{flex:none;color:var(--muted);font-size:16px}.project-destinations{margin:2px 7px 4px;padding-left:7px;border-left:1px solid var(--border)}.project-destinations button{font-size:calc(12px * var(--fs,1))}.danger{color:var(--danger)!important}button:disabled{opacity:.45;cursor:default}hr{border:0;border-top:1px solid var(--border);margin:5px 6px}
</style>
