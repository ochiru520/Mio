<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as api from '../services/agentExperienceApi.js'
const props = defineProps({ taskId: String, conversationId: String, target:Object })
const emit = defineEmits(['select','activated'])
const input=ref('')
const pages = ref([]), pageId = ref(''), frame = ref(''), error = ref(''), picking = ref(false), busy = ref(false)
const page = computed(() => pages.value.find(item => item.page_id === pageId.value))
let disposed = false, generation = 0, timer, refreshing = false, controlQueue=Promise.resolve()
const claimedTargets=new Set()
function replaceFrame(url) { if(frame.value) URL.revokeObjectURL(frame.value); frame.value=url }
function setControl(owner, visible = true, target = page.value) {
  const task=props.taskId
  if(!target || !task) return Promise.resolve()
  const operation=controlQueue.catch(()=>{}).then(async()=>{
    const result=await api.browserControl(task,target.page_id,{expected_revision:target.control.revision,visible,active:visible,owner})
    if(result.control)target.control=result.control
    return result
  })
  controlQueue=operation
  return operation
}
async function refresh() {
  if(disposed || refreshing || document.hidden || !props.taskId) return
  refreshing=true
  const current=++generation, task=props.taskId
  try {
    const data=await api.browserPages(task)
    if(disposed || current!==generation) return
    pages.value=data.pages || []
    if(props.target?.task_id===task && props.target.auto_control && !claimedTargets.has(props.target.page_id) && pages.value.some(item=>item.page_id===props.target.page_id)) pageId.value=props.target.page_id
    if(!page.value) pageId.value=pages.value[0]?.page_id || ''
    const selected=page.value
    if(selected) {
      const autoControl=props.target?.task_id===task && props.target.page_id===selected.page_id && props.target.auto_control && !claimedTargets.has(selected.page_id)
      await setControl(autoControl ? 'agent' : selected.control?.owner || 'user',true,selected)
      if(disposed || current!==generation || document.hidden) return
      if(autoControl){claimedTargets.add(selected.page_id);emit('activated')}
      const blob=await api.browserFrame(task,selected.page_id)
      if(!disposed && current===generation && selected.page_id===pageId.value) replaceFrame(URL.createObjectURL(blob))
    } else replaceFrame('')
    error.value=''
  } catch(cause) { if(!disposed && current===generation) { error.value=cause.message; replaceFrame('') } }
  finally { refreshing=false }
}
async function switchPage(id) {
  if(id===pageId.value) return
  ++generation; busy.value=true
  try { await setControl('user',false); pageId.value=id; replaceFrame(''); picking.value=false }
  catch(cause) { error.value=cause.message }
  finally { busy.value=false; void refresh() }
}
async function changeOwner(owner) {
  busy.value=true; picking.value=false
  try { await setControl(owner); await refresh() } catch(cause) { error.value=cause.message } finally { busy.value=false }
}
async function userAction(operation,value='',coordinates={}){if(!page.value||busy.value)return;busy.value=true;try{await api.browserUserAction(props.taskId,page.value.page_id,{expected_revision:page.value.control.revision,operation,value,...coordinates});input.value='';await refresh()}catch(cause){error.value=cause.message}finally{busy.value=false}}
async function select(event) {
  if(!page.value || busy.value) return
  if(!picking.value){const img=event.currentTarget,rect=img.getBoundingClientRect();if(page.value.control?.owner==='user')await userAction('click','',{x:Math.round((event.clientX-rect.left)*img.naturalWidth/rect.width),y:Math.round((event.clientY-rect.top)*img.naturalHeight/rect.height)});return}
  const img=event.currentTarget, rect=img.getBoundingClientRect(), selected=page.value
  busy.value=true
  try {
    const value=await api.selectBrowserElement(props.taskId,selected.page_id,{x:Math.round((event.clientX-rect.left)*img.naturalWidth/rect.width),y:Math.round((event.clientY-rect.top)*img.naturalHeight/rect.height),expected_revision:selected.control.revision})
    emit('select',{...value,task_id:props.taskId,conversation_id:props.conversationId})
    picking.value=false
  } catch(cause) { error.value=cause.message }
  finally { busy.value=false }
}
function hide() { ++generation; picking.value=false; void setControl('user',false).catch(()=>{}); replaceFrame('') }
function visibility() { if(document.hidden) hide(); else void refresh() }
watch(()=>props.taskId,()=>{++generation;pageId.value='';pages.value=[];replaceFrame('');void refresh()})
watch(()=>props.target?.page_id,()=>{void refresh()})
onMounted(()=>{void refresh();timer=window.setInterval(refresh,5000);document.addEventListener('visibilitychange',visibility)})
onBeforeUnmount(()=>{hide();disposed=true;clearInterval(timer);document.removeEventListener('visibilitychange',visibility)})
</script>
<template>
  <section class="browser-panel" aria-label="任务受控浏览器">
    <p v-if="!taskId">开始网页任务后，可在这里查看受控页面。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="pages.length" class="browser-tabs"><button v-for="item in pages" :key="item.page_id" type="button" :class="{selected:item.page_id===pageId}" :disabled="busy" @click="switchPage(item.page_id)">{{ item.title || item.url || '网页' }}</button></div>
    <template v-if="page">
      <p class="browser-url">{{ page.url }}</p>
      <div class="browser-controls"><button type="button" :disabled="busy" @click="changeOwner('user')">我来操作</button><button type="button" :disabled="busy" @click="changeOwner('agent')">交给澪</button><button type="button" :disabled="busy || !frame" :aria-pressed="picking" @click="picking=!picking">{{ picking ? '取消点选' : '指出网页元素' }}</button><button type="button" :disabled="busy" @click="refresh">刷新</button></div>
      <small>{{ page.control?.owner === 'agent' ? '澪正在使用此页面' : '你已接管，可点击画面或输入文字' }} · 面板隐藏或切页后暂停此页面的自动操作</small>
      <p v-if="picking">点击画面中的目标；选择会随下一条消息发送。</p>
      <form v-if="page.control?.owner==='user'" class="browser-controls" @submit.prevent="userAction('type',input)"><input v-model="input" aria-label="输入到网页当前焦点" placeholder="输入到网页当前焦点" /><button type="submit" :disabled="busy || !input">输入</button><button type="button" :disabled="busy" @click="userAction('press','Enter')">回车</button><button type="button" :disabled="busy" @click="userAction('scroll','600')">向下滚动</button><button type="button" :disabled="busy" @click="userAction('scroll','-600')">向上滚动</button></form>
      <img v-if="frame" :src="frame" alt="受控浏览器当前画面" :class="{picking}" @click="select" />
    </template>
    <p v-else-if="taskId && !error">当前任务尚未打开网页。</p>
  </section>
</template>
<style scoped>
.browser-panel{display:grid;gap:14px;min-width:0;color:var(--text);font-size:calc(13px * var(--fs,1))}.browser-tabs,.browser-controls{display:flex;flex-wrap:wrap;gap:8px}.browser-tabs button{max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.browser-panel button{border:1px solid var(--border);background:var(--surface);border-radius:7px;color:var(--text);padding:7px 10px;font:inherit;cursor:pointer}.browser-panel button.selected,.browser-panel button[aria-pressed=true]{background:var(--accent-soft);color:var(--accent)}.browser-url{overflow-wrap:anywhere;margin:0}.browser-panel small{color:var(--text-muted);line-height:1.8}.browser-panel img{display:block;width:100%;height:auto;border:1px solid var(--border)}.browser-panel img.picking{cursor:crosshair}
</style>
