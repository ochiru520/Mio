<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import * as api from '../services/agentExperienceApi.js'
const props=defineProps({reference:Object,projectId:String})
const emit=defineEmits(['close','conversation'])
const value=ref(null),error=ref(''),loading=ref(false)
let version=0,disposed=false
async function load(){const n=++version;if(!props.reference)return;loading.value=true;try{const data=await api.resolveReferences({references:[props.reference],project_id:props.projectId||''});if(n!==version||disposed)return;if(data.blocked?.length)throw new Error(data.blocked[0].reason);value.value=data.resolved?.[0];error.value=''}catch(cause){if(n===version)error.value=cause.message}finally{if(n===version)loading.value=false}}
watch(()=>props.reference,()=>{value.value=null;void load()},{immediate:true})
onBeforeUnmount(()=>{disposed=true;++version})
</script>
<template><section class="reference-preview"><header><strong>{{ reference?.name || '引用预览' }}</strong><button type="button" @click="emit('close')">关闭</button></header><div><p v-if="loading">正在核对来源…</p><p v-if="error" role="alert">{{ error }}<button type="button" @click="load">重试</button></p><template v-if="value"><small>{{ value.target }}</small><button v-if="value.kind==='conversation'" type="button" @click="emit('conversation',value.target)">打开来源会话</button><pre>{{ value.excerpt }}</pre><p v-if="value.truncated || value.has_more">这里只展示已解析片段，完整来源仍保留。</p></template></div></section></template>
<style scoped>.reference-preview{height:100%;display:flex;flex-direction:column;min-height:0;color:var(--text)}header{display:flex;justify-content:space-between;padding:20px;border-bottom:1px solid var(--border)}.reference-preview>div{overflow:auto;flex:1;padding:20px;font-size:calc(13px * var(--fs,1));line-height:1.8}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}small{overflow-wrap:anywhere;color:var(--text-muted)}button{font:inherit;background:transparent;color:var(--text);border:0;cursor:pointer}</style>
