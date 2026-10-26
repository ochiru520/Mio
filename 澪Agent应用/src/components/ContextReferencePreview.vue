<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import * as api from '../services/agentExperienceApi.js'
const props=defineProps({conversationId:String,taskId:String,reference:Object})
const emit=defineEmits(['close'])
const value=ref(null),error=ref(''),busy=ref(false)
let version=0,disposed=false
async function load(more=false){const n=++version;busy.value=true;try{const result=await api.inputReference(props.conversationId,props.taskId,props.reference.id,more?value.value.next_offset:0);if(!disposed&&n===version){value.value=more?{...result,text:value.value.text+result.text}:result;error.value=''}}catch(cause){if(n===version)error.value=cause.message}finally{if(n===version)busy.value=false}}
watch(()=>props.reference,()=>{value.value=null;void load()},{immediate:true})
onBeforeUnmount(()=>{disposed=true;++version})
</script>
<template><section class="input-reference-preview"><header><strong>{{ reference.kind==='image'?'本轮省略的原图':'已裁剪的原始工具结果' }}</strong><button type="button" @click="emit('close')">关闭</button></header><div><p v-if="error" role="alert">{{ error }}<button type="button" @click="load()">重试</button></p><p v-if="busy">正在读取原件…</p><template v-if="value"><p>{{ value.notice }}</p><img v-if="value.kind==='image' && value.preview_url" :src="value.preview_url" alt="上下文中省略的原图" /><pre v-else>{{ value.text }}</pre><button v-if="value.has_more" type="button" :disabled="busy" @click="load(true)">继续读取</button></template></div></section></template>
<style scoped>.input-reference-preview{height:100%;display:flex;flex-direction:column;color:var(--text)}header{display:flex;justify-content:space-between;gap:12px;padding:20px;border-bottom:1px solid var(--border)}.input-reference-preview>div{padding:20px;overflow:auto;min-height:0;flex:1;font-size:calc(13px * var(--fs,1));line-height:1.8}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}img{width:100%;height:auto}button{font:inherit;color:var(--text);background:transparent;border:0;cursor:pointer}p{color:var(--text-muted)}</style>
