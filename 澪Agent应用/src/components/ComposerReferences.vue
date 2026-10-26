<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import * as api from '../services/agentExperienceApi.js'
const props=defineProps({draft:String,projectId:String,conversationId:String,enabled:{type:Boolean,default:false},items:{type:Array,default:()=>[]}})
const emit=defineEmits(['add','remove','preview','replace-mention'])
const open=ref(false),kind=ref('file'),path=ref(''),query=ref(''),items=ref([]),error=ref('')
const loading=ref(false),resolving=ref(false),hasMore=ref(false),nextOffset=ref(0)
const busy=computed(()=>loading.value || resolving.value)
const mention=computed(()=>String(props.draft || '').match(/(?:^|\s)@([^@\n]*)$/)?.[1] ?? null)
let timer,version=0,selectionVersion=0,disposed=false
function invalidateSelection(){++selectionVersion;resolving.value=false}
async function load(more=false){
  if(!props.enabled)return
  clearTimeout(timer)
  if(more && !hasMore.value)return
  const n=++version,offset=more ? nextOffset.value : 0
  loading.value=true
  if(!more){items.value=[];hasMore.value=false;nextOffset.value=0}
  try{
    const data=await api.referenceCandidates({kind:kind.value,query:query.value,path:path.value,project_id:props.projectId||'',offset:String(offset),limit:'40'})
    if(disposed || n!==version)return
    const values=more ? [...items.value,...(data.candidates||[])] : data.candidates||[]
    items.value=[...new Map(values.map(item=>[`${item.kind}:${item.target}`,item])).values()]
    hasMore.value=Boolean(data.has_more)
    nextOffset.value=Number.isSafeInteger(data.next_offset) ? data.next_offset : offset+(data.candidates||[]).length
    error.value=''
  }catch(cause){if(!disposed && n===version)error.value=cause.message}
  finally{if(n===version)loading.value=false}
}
function browse(item){invalidateSelection();path.value=item.target;query.value='';void load()}
function changeKind(value){invalidateSelection();kind.value=value;path.value='';void load()}
function close(){open.value=false;invalidateSelection();++version;loading.value=false;clearTimeout(timer)}
function toggle(){if(!props.enabled)return;if(open.value)close();else{open.value=true;void load()}}
async function choose(item){
  if(busy.value)return
  const n=++selectionVersion,project=props.projectId,conversation=props.conversationId,draft=props.draft
  resolving.value=true;error.value=''
  const current=()=>!disposed && n===selectionVersion && project===props.projectId && conversation===props.conversationId && draft===props.draft
  try{
    const data=await api.resolveReferences({references:[item],project_id:project||''})
    if(!current())return
    if(data.blocked?.length)throw new Error(data.blocked[0].reason)
    const resolved=data.resolved?.[0]
    if(resolved){emit('add',{kind:resolved.kind,target:resolved.target,version:resolved.version,name:resolved.name});emit('replace-mention');open.value=false}
  }catch(cause){if(current())error.value=cause.message}
  finally{if(n===selectionVersion)resolving.value=false}
}
watch(()=>props.draft,()=>{if(resolving.value)error.value='输入内容已变化，请重新选择引用。';invalidateSelection()},{flush:'sync'})
watch(mention,value=>{if(props.enabled && value!==null){open.value=true;query.value=value;clearTimeout(timer);timer=setTimeout(()=>void load(),200)}})
watch(()=>props.enabled,value=>{if(!value)close()})
watch(()=>[props.projectId,props.conversationId],()=>{++version;invalidateSelection();loading.value=false;clearTimeout(timer);path.value='';query.value=mention.value ?? '';items.value=[];hasMore.value=false;nextOffset.value=0;error.value='';if(open.value)void load()},{flush:'sync'})
onBeforeUnmount(()=>{disposed=true;++version;invalidateSelection();clearTimeout(timer)})
</script>
<template>
  <div v-if="enabled || props.items.length" class="composer-references" @keydown.esc="close"><div v-if="props.items.length" class="reference-chips"><span v-for="(item,index) in props.items" :key="`${item.kind}:${item.target}`"><button type="button" :title="item.target" @click="emit('preview',item)">@{{ item.name || item.target }}</button><button type="button" :aria-label="`移除引用${item.name || item.target}`" @click="emit('remove',index)">×</button></span></div><button type="button" v-if="enabled" class="reference-trigger" :aria-expanded="open" @click="toggle">@ 引用文件、目录或会话</button>
    <section v-if="enabled && open" class="reference-popover"><header><strong>引用资料</strong><button type="button" @click="close">关闭</button></header><nav><button v-for="(label,value) in {file:'文件',directory:'目录',conversation:'会话'}" :key="value" type="button" :aria-pressed="kind===value" @click="changeKind(value)">{{ label }}</button></nav><input v-model="query" type="search" aria-label="搜索引用" placeholder="搜索名称" @input="load()" /><p v-if="path" class="reference-path">{{ path }}<button type="button" @click="browse({target:''})">返回授权目录</button></p><p v-if="error" role="alert">{{ error }}</p><p v-if="busy">正在读取…</p><div class="reference-candidates"><div v-for="item in items" :key="`${item.kind}:${item.target}`"><button type="button" :disabled="busy" :title="item.target" @click="choose(item)">{{ item.kind==='directory' ? '目录 · ' : '' }}{{ item.name }}</button><button v-if="item.kind==='directory'" type="button" :disabled="busy" aria-label="浏览子目录" @click="browse(item)">浏览 →</button></div><button v-if="hasMore" type="button" :disabled="busy" @click="load(true)">加载更多引用</button><p v-if="!items.length && !busy">没有找到可引用的资料。文件范围沿用已授权目录。</p></div></section>
  </div>
</template>
<style scoped>
.composer-references{position:relative;font-size:calc(12px * var(--fs,1));color:var(--text-muted);padding:2px 0 6px}.composer-references button{background:transparent;border:0;color:inherit;cursor:pointer;font:inherit;padding:5px;text-align:left}.reference-chips{display:flex;gap:8px;flex-wrap:wrap}.reference-chips>span{border-bottom:1px solid var(--border);max-width:100%;display:flex}.reference-chips button:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.reference-popover{position:absolute;bottom:100%;left:0;width:min(480px,75vw);padding:16px;z-index:70;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-lg);color:var(--text)}.reference-popover header,.reference-candidates>div{display:flex;justify-content:space-between;gap:10px;align-items:center}.reference-popover nav{display:flex;gap:14px;margin:12px 0}.reference-popover nav button[aria-pressed=true]{color:var(--accent)}.reference-popover input{width:100%;font:inherit;padding:8px;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:6px}.reference-candidates{max-height:260px;overflow:auto;margin-top:8px}.reference-candidates>div>button:first-child{flex:1;min-width:0;overflow-wrap:anywhere}.reference-path{overflow-wrap:anywhere}
</style>
