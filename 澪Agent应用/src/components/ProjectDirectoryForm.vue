<script setup>
import { ref } from 'vue'
import { FolderOpen } from '../icons/index.js'
import { selectLocalPath } from '../services/localPicker.js'
defineProps({ busy: Boolean })
const emit = defineEmits(['submit','cancel'])
const name = ref(''), directory = ref(''), create = ref(false), error = ref('')
async function selectFolder() {
  error.value = ''
  try {
    const picked = await selectLocalPath('directory')
    if (!picked) return
    directory.value = picked.path
    if (!name.value && !create.value) name.value = picked.path.split(/[\\/]/).filter(Boolean).at(-1) || ''
  } catch (cause) { error.value = cause.message }
}
function submit() {
  let path = directory.value.trim()
  const title = name.value.trim()
  if (create.value) {
    if (!title || /[<>:"/\\|?*]|[. ]$/.test(title) || ['.', '..'].includes(title)) { error.value = '请输入有效的文件夹名称。'; return }
    path = path.replace(/[\\/]$/, '') + '/' + title
  }
  if (!path) { error.value = '请选择文件夹或填写本机完整路径。'; return }
  emit('submit', { name: title, directory: path, create_directory: create.value })
}
</script>
<template>
  <form class="agent-project-directory-form" @submit.prevent="submit">
    <div class="project-directory-modes" role="group" aria-label="项目目录方式"><button type="button" :aria-pressed="!create" :disabled="busy" @click="create = false">已有文件夹</button><button type="button" :aria-pressed="create" :disabled="busy" @click="create = true">新建文件夹</button></div>
    <label><span>项目名称</span><input v-model="name" placeholder="为项目取个名字" maxlength="160" required :disabled="busy" /></label>
    <label><span>{{ create ? '创建位置' : '项目文件夹' }}</span><div class="project-directory-path"><input v-model="directory" :aria-label="create ? '新文件夹的父目录' : '项目文件夹'" :placeholder="create ? '选择父目录' : '选择电脑上的文件夹'" required :disabled="busy" /><button type="button" :aria-label="create ? '选择创建位置' : '选择项目文件夹'" :disabled="busy" @click="selectFolder"><FolderOpen :size="17" /></button></div></label>
    <small v-if="create">将在所选位置创建与项目同名的文件夹。</small>
    <p v-if="error" role="alert">{{ error }}</p><footer><button type="button" :disabled="busy" @click="emit('cancel')">取消</button><button class="project-create-primary" type="submit" :disabled="busy">{{ busy ? '正在创建…' : '创建项目' }}</button></footer>
  </form>
</template>
<style scoped>
.agent-project-directory-form { display: grid; gap: 18px; }
.project-directory-modes { display: flex; gap: 4px; padding: 4px; border-radius: 9px; background: var(--surface-soft); }
button { border: 1px solid var(--border); border-radius: 7px; padding: 9px 14px; color: var(--text); background: var(--surface-raised); font: inherit; font-size: calc(13px * var(--fs, 1)); cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
.project-directory-modes button { flex: 1; border-color: transparent; background: transparent; }
.project-directory-modes button[aria-pressed=true] { background: var(--surface-raised); border-color: var(--border); font-weight: 600; }
label { display: grid; gap: 8px; color: var(--text); font-size: calc(13px * var(--fs, 1)); }
input { width: 100%; min-width: 0; height: 40px; padding: 9px 11px; font: inherit; }
.project-directory-path { display: flex; gap: 8px; }
.project-directory-path input { flex: 1; }
.project-directory-path button { display: grid; place-items: center; flex: none; padding: 9px 11px; }
small { color: var(--text-muted); font-size: calc(12px * var(--fs, 1)); line-height: 1.6; }
p { margin: 0; color: var(--danger); font-size: calc(12px * var(--fs, 1)); }
footer { display: flex; justify-content: flex-end; gap: 8px; padding-top: 8px; }
.project-create-primary { color: var(--accent-contrast); background: var(--accent-hover); border-color: var(--accent-hover); }
</style>
