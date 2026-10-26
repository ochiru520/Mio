<script setup>
import * as workspaceApi from '../services/workspaceApi.js'

import { ref } from 'vue'
const props = defineProps({ taskId: String, disabled: Boolean })
const emit = defineEmits(['changed'])
const draft = ref(''), busy = ref(false), error = ref(''), notice = ref('')
let pending = null
async function submit() {
  if (busy.value || props.disabled || !draft.value.trim()) return
  const id = props.taskId, content = draft.value.trim()
  if (!pending || pending.id !== id || pending.content !== content) pending = { id, content, key: crypto.randomUUID() }
  const request = pending
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await workspaceApi.submitTaskFollowUp(id, { message: content, request_key: request.key }, {})
    if (id === props.taskId) {
      if (draft.value.trim() === content) draft.value = ''
      notice.value = '补充已保存，下一次规划会读取。'; emit('changed')
    }
    if (pending === request) pending = null
  } catch (cause) { if (id === props.taskId) error.value = cause.message }
  finally { busy.value = false }
}
</script>
<template>
  <form class="task-followup" @submit.prevent="submit">
    <label>补充任务要求<textarea v-model="draft" :disabled="disabled" maxlength="12000" placeholder="补充目标、纠正要求或提供缺少的信息" /></label>
    <small>当前动作结束后调整计划；已执行的动作保留，旧的待确认操作会失效。</small>
    <button type="submit" :disabled="disabled || busy || !draft.trim()">{{ busy ? '正在保存' : '保存补充' }}</button>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  </form>
</template>
<style scoped>
.task-followup{display:grid;gap:8px;margin:12px 0}.task-followup label{display:grid;gap:6px}.task-followup textarea{box-sizing:border-box;width:100%;min-height:80px;resize:vertical;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:inherit;font:inherit}.task-followup small{color:var(--muted)}.task-followup button{justify-self:start;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:inherit;cursor:pointer}.task-followup button:disabled{opacity:.5}.task-followup p{margin:0}
</style>
