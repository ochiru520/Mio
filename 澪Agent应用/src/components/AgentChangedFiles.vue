<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { changedFilesFromHistory } from '../runActivityState.js'
import * as api from '../services/executionApi.js'
const props=defineProps({projectId:String,conversationId:String,taskId:String})
const emit=defineEmits(['preview'])
const changes=ref([]),error=ref('')
const files=computed(()=>changedFilesFromHistory(changes.value,props.taskId))
let version=0,disposed=false
async function load(){const n=++version;if(!props.projectId||!props.conversationId||!props.taskId){changes.value=[];return}try{const result=await api.loadProjectHistory(props.projectId,new URLSearchParams({conversation_id:props.conversationId}));if(!disposed&&n===version){changes.value=result.changes||[];error.value=''}}catch(cause){if(n===version)error.value=cause.message}}
watch(()=>[props.projectId,props.conversationId,props.taskId],()=>{changes.value=[];void load()},{immediate:true})
onBeforeUnmount(()=>{disposed=true;++version})
</script>
<template>
  <section v-if="files.length" class="changed-files" aria-label="本次任务的代码改动"><header><strong>已修改 {{ files.length }} 个文件</strong><button type="button" @click="load">刷新</button></header><button v-for="file in files" :key="file.path" type="button" @click="emit('preview',{kind:'diff',id:file.change_id,change_ids:file.change_ids,path:file.path,conversation_id:conversationId,project_id:projectId,task_id:taskId})"><span>{{ file.path }}</span><small>+{{ file.additions }} −{{ file.deletions }}</small></button></section><p v-if="error" class="change-error">改动摘要暂不可用：{{ error }}<button type="button" @click="load">重试</button></p>
</template>
<style scoped>
.changed-files{margin:20px 0;padding:14px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);font-size:calc(13px * var(--fs,1))}.changed-files header,.changed-files>button{display:flex;gap:20px;justify-content:space-between;align-items:center}.changed-files>button{width:100%;padding:9px 0}.changed-files button,.change-error button{font:inherit;color:var(--text);background:transparent;border:0;cursor:pointer;text-align:left}.changed-files span{overflow-wrap:anywhere}.changed-files small{white-space:nowrap;color:var(--text-muted)}.change-error{color:var(--text-muted);font-size:12px}
</style>
