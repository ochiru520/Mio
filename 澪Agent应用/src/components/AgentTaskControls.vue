<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import * as tasksApi from '../services/tasksApi.js'
import { canResumeTask, taskStatusLabel } from '../agentTaskState.js'
import AgentTaskFollowUp from './AgentTaskFollowUp.vue'
const props=defineProps({task:Object})
const emit=defineEmits(['changed','details'])
const detail=ref(null), busy=ref(false),error=ref(''),followup=ref(false)
let generation=0,disposed=false
const current=computed(()=>detail.value?.id===props.task?.id ? detail.value : props.task)
const approvals=computed(()=>(current.value?.observations || []).filter(item=>item.status==='needs_confirmation'))
async function load(){const n=++generation,id=props.task?.id;if(!id)return;try{const data=await tasksApi.loadWork(id);if(!disposed&&n===generation)detail.value=data.task || data}catch(cause){if(n===generation)error.value=cause.message}}
async function action(command,id){busy.value=true;error.value='';try{if(command==='approve')await tasksApi.approveWorkAction(current.value.id,id);else await tasksApi.submitWork(current.value.id,command);await load();emit('changed')}catch(cause){error.value=cause.message}finally{busy.value=false}}
watch(()=>[props.task?.id,props.task?.status],()=>{detail.value=null;void load()},{immediate:true})
onBeforeUnmount(()=>{disposed=true;++generation})
</script>
<template>
  <section v-if="current && current.status!=='completed'" class="task-inline-controls">
    <div class="task-control-row"><span>{{ taskStatusLabel(current.status) }}</span><button type="button" @click="followup=!followup">补充要求</button><button type="button" @click="emit('details')">详情</button><button v-if="canResumeTask(current)" type="button" :disabled="busy" @click="action('resume')">继续</button></div>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="current.status==='waiting_confirmation'" class="inline-approvals"><p>以下操作等待授权，等待时间不计入执行预算。</p><div v-for="item in approvals" :key="item.step_id"><span>{{ item.display_name || item.tool_name }}<small>{{ item.summary || item.error }}</small></span><button type="button" :disabled="busy" @click="action('approve',item.action_id)">允许此操作</button></div></div>
    <p v-if="current.snapshot?.recovery_state==='unknown'">上次操作已开始，结果尚未确认。继续前会先核对结果，避免重复执行。</p>
    <AgentTaskFollowUp v-if="followup" :key="current.id" :task-id="current.id" :disabled="current.status==='cancelled'" @changed="load();emit('changed')" />
  </section>
</template>
<style scoped>
.task-inline-controls{padding:8px 4px;color:var(--text-muted);font-size:calc(12px * var(--fs,1))}.task-control-row,.inline-approvals>div{display:flex;align-items:center;gap:12px}.task-control-row>span{margin-right:auto}.task-inline-controls button{background:transparent;border:0;color:var(--text);cursor:pointer;font:inherit;padding:6px}.inline-approvals{padding:10px 0}.inline-approvals small{display:block}.inline-approvals>div{justify-content:space-between}
</style>
