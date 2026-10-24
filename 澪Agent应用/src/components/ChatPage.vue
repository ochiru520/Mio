<script setup>
import AgentTasksPage from './AgentTasksPage.vue'
import AgentTaskProgress from './AgentTaskProgress.vue'
import { receiptSummary } from '../agentProcessState.js'
import { contextUsagePresentation } from '../contextUsageState.js'
import { messageCacheRateLabel, messageTokenUsageTitle } from '../messageTokenUsage.js'
import { artifactPreviewTarget } from '../artifactPreviewTarget.js'
import ExecutionPolicy from './ExecutionPolicy.vue'
import AgentToolPicker from './AgentToolPicker.vue'
import MessageNavigator from './MessageNavigator.vue'
import ImagePreview from './ImagePreview.vue'
import { computed, inject, nextTick, ref, watch, onBeforeUnmount } from 'vue'
import { rememberReadingPosition, savedReadingPosition, restoreReadingPosition } from '../conversationScroll.js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  FileText,
  ListChecks,
  MessageSquareText,
  Paperclip,
  Pin,
  Plus,
  RefreshCw,
  Send,
  ArrowUp,
  ArrowUpRight,
  Settings,
  Square,
  SquarePen,
  Trash2,
  Upload,
  UserRound,
  Volume2,
  X,
} from '@lucide/vue'

const props = defineProps({ artifacts: { type: Array, default: () => [] } })
const emit = defineEmits(['preview-artifact'])
const context = inject('mio-chat-page')
const surface = inject('mio-agent-surface', null)
function attachmentArtifact(attachment) { return surface ? artifactPreviewTarget(attachment, props.artifacts) : null }
const contextWindow = computed(() => contextUsagePresentation(context.contextUsage))
if (!context) throw new Error('对话页上下文未初始化')
const conversationDrawerOpen = ref(false)
const conversationDrawerPinned = ref(localStorage.getItem('mio_conversation_drawer_pinned') === 'true')
const qqCleanupMode = ref(false)
const selectedQqMessageIds = ref([])
const activeImageAttachment = ref(null)
const agentProcessExpanded = ref(localStorage.getItem('mio_agent_process_expanded') === 'true')
const openedProcesses = ref(new Set())
function openProcess(event, id) { if (event.target.open) openedProcesses.value=new Set([...openedProcesses.value,id]) }
const activeConversation = computed(() => context.conversations.find((item) => item.id === context.selectedConversationId) || null)
const composerInput=ref(null)
let mountedScroll=null,scrollConversation='',restoring=false,scrollObserver
async function observeMessages() {
  await nextTick()
  const list=mountedScroll?.querySelector('.message-list')
  if(list && scrollObserver)scrollObserver.observe(list)
}
function rememberScroll() {
  if(!restoring && !context.loading && mountedScroll?.querySelector('[data-message-anchor]'))rememberReadingPosition(scrollConversation,mountedScroll)
}
async function restoreScroll() {
  restoring=true;await nextTick()
  if(mountedScroll && !context.loading){
    const position=savedReadingPosition(context.selectedConversationId)
    if(position)restoreReadingPosition(mountedScroll,position)
    else mountedScroll.scrollTop=mountedScroll.scrollHeight
  }
  await new Promise(resolve=>requestAnimationFrame(resolve));restoring=false
}
function bindChatScroll(element) {
  if(element===mountedScroll)return
  mountedScroll?.removeEventListener('scroll',rememberScroll)
  scrollObserver?.disconnect();mountedScroll=element;context.chatScroll=element
  scrollConversation=context.selectedConversationId
  if(element){
    element.addEventListener('scroll',rememberScroll,{passive:true})
    if(typeof ResizeObserver!=='undefined'){
      scrollObserver=new ResizeObserver(()=>{
        const position=savedReadingPosition(scrollConversation)
        if(!context.loading && !restoring && element.querySelector('[data-message-anchor]')){
          if(position)restoreReadingPosition(element,position)
          else element.scrollTop=element.scrollHeight
        }
      });scrollObserver.observe(element)
    }
    void restoreScroll()
  }
}
watch(()=>context.loading,(loading)=>{if(!loading){void restoreScroll();void observeMessages()}})
watch(()=>context.messageTurns,()=>{void observeMessages()},{flush:'post'})
watch(()=>context.selectedConversationId,(id,old)=>{
  if(mountedScroll && old)rememberReadingPosition(old,mountedScroll)
  scrollConversation=id
})
watch(()=>context.draft,async()=>{await nextTick();const input=composerInput.value;if(input){input.style.height='auto';input.style.height=`${input.scrollHeight}px`}},{immediate:true})
onBeforeUnmount(()=>{rememberScroll();mountedScroll?.removeEventListener('scroll',rememberScroll);scrollObserver?.disconnect()})
function renderAgentMarkdown(content) {
  const renderer=new marked.Renderer()
  renderer.html=({text})=>text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')
  return DOMPurify.sanitize(marked.parse(content || '',{renderer}))
}

async function openConversation(conversationId) {
  if (conversationId !== context.selectedConversationId) cancelQqCleanup()
  await context.selectConversation(conversationId)
  if (!conversationDrawerPinned.value) conversationDrawerOpen.value = false
}

async function startNewConversation() {
  const previous = context.selectedConversationId
  await context.createNewConversation()
  if (context.selectedConversationId === previous) return
  cancelQqCleanup()
  if (!conversationDrawerPinned.value) conversationDrawerOpen.value = false
  await nextTick()
  document.querySelector('.composer-input')?.focus()
}

async function beginQqCleanup(conversation) {
  if (context.sending) return
  await openConversation(conversation.id)
  selectedQqMessageIds.value = []
  qqCleanupMode.value = true
}

function cancelQqCleanup() {
  qqCleanupMode.value = false
  selectedQqMessageIds.value = []
}

function toggleQqMessage(messageId) {
  const id = Number(messageId)
  if (!Number.isInteger(id) || id <= 0) return
  selectedQqMessageIds.value = selectedQqMessageIds.value.includes(id)
    ? selectedQqMessageIds.value.filter((item) => item !== id)
    : [...selectedQqMessageIds.value, id]
}

async function deleteSelectedQqMessages() {
  if (!activeConversation.value || !selectedQqMessageIds.value.length) return
  const deleted = await context.deleteConversationMessages(activeConversation.value, selectedQqMessageIds.value)
  if (deleted) cancelQqCleanup()
}

function toggleConversationDrawerPin() {
  conversationDrawerPinned.value = !conversationDrawerPinned.value
  localStorage.setItem('mio_conversation_drawer_pinned', String(conversationDrawerPinned.value))
}

async function closeConversationDrawer() {
  conversationDrawerOpen.value = false
  if (conversationDrawerPinned.value) toggleConversationDrawerPin()
  await nextTick()
  document.querySelector('.conversation-drawer-trigger')?.focus()
}

function openImagePreview(attachment) {
  if (!attachment?.url) return
  activeImageAttachment.value = attachment
}

function closeImagePreview() {
  activeImageAttachment.value = null
}
</script>

<template>
  <div :class="['chat-layout', 'integrated-chat-layout', `chat-display-${context.displayMode}`, { 'conversation-drawer-pinned': conversationDrawerPinned, 'has-custom-background': Boolean(context.chatBackgroundStyle?.['--chat-background-image']) }]" :style="context.chatBackgroundStyle || {}">
    <button
      v-if="conversationDrawerOpen && !conversationDrawerPinned"
      class="conversation-drawer-backdrop"
      type="button"
      aria-label="关闭对话窗口"
      @click="closeConversationDrawer"
    />
    <aside v-if="conversationDrawerOpen || conversationDrawerPinned" class="conversation-drawer" @keydown.esc.stop.prevent="closeConversationDrawer">
      <header>
        <div><MessageSquareText :size="17" /><strong>{{ context.isAgentWorkspace ? 'Agent 对话' : '对话窗口' }}</strong><span>{{ context.conversations.length }}</span></div>
        <div class="conversation-drawer-controls"><button type="button" :class="{ active: conversationDrawerPinned }" :title="conversationDrawerPinned ? '取消固定' : '固定抽屉'" @click="toggleConversationDrawerPin"><Pin :size="14" /></button><button type="button" title="收起对话列表" aria-label="收起对话列表" @click="closeConversationDrawer"><X :size="15" /></button></div>
      </header>
      <button class="conversation-new-button" type="button" @click="startNewConversation"><Plus :size="15" />新对话</button>
      <div class="conversation-drawer-list">
        <article v-for="conversation in context.conversations" :key="conversation.id" :class="{ active: context.selectedConversationId === conversation.id }">
          <button type="button" @click="openConversation(conversation.id)">
            <span><strong>{{ conversation.title }}</strong><small>{{ conversation.preview || (conversation.kind === 'wechat' ? '与微信共享聊天记录' : conversation.kind === 'qq' ? '与 QQ 共享上下文' : conversation.kind === 'pet' ? '桌宠独立对话' : '还没有消息') }}</small></span>
            <time>{{ context.formatSidebarTime(conversation.updated_at) }}</time>
          </button>
          <div v-if="['desktop', 'agent', 'qq'].includes(conversation.kind)" class="conversation-drawer-actions">
            <template v-if="conversation.kind === 'desktop' || conversation.kind === 'agent'">
              <button type="button" title="重命名" @click="context.renameConversation(conversation)"><SquarePen :size="13" /></button>
              <button type="button" title="删除" @click="context.deleteConversation(conversation)"><Trash2 :size="13" /></button>
            </template>
            <button v-else type="button" title="选择要清理的 QQ 消息" :disabled="context.sending" @click="beginQqCleanup(conversation)"><ListChecks :size="13" /></button>
          </div>
        </article>
      </div>
    </aside>

    <section
      :class="['chat-column', { 'file-drag-active': context.isFileDragging }]"
      @dragenter="context.handleFileDragEnter"
      @dragover="context.handleFileDragOver"
      @dragleave="context.handleFileDragLeave"
      @drop="context.handleFileDrop"
    >
      <button class="conversation-drawer-trigger" type="button" title="对话窗口" @click="conversationDrawerOpen = !conversationDrawerOpen">
        <MessageSquareText :size="16" /><span>{{ context.conversations.find((item) => item.id === context.selectedConversationId)?.title || '对话窗口' }}</span>
      </button>
      <div v-if="!context.isAgentWorkspace && context.latestTaskHandoff?.task_id" class="chat-handoff-notice" role="status">
        <span><ListChecks :size="15" /><strong>已交给 Agent 继续处理</strong><small>详细执行过程在 Agent 页面查看</small></span>
        <button type="button" @click="context.openAgentHandoff">查看任务</button>
      </div>
      <div v-if="context.isFileDragging" class="file-drop-overlay"><Upload :size="25" /><strong>松开即可添加到对话</strong><span>图片、PDF、Word 和其他文件都可以</span></div>

      <div class="chat-reading-pane">
      <div :ref="bindChatScroll" class="message-scroll">
        <div v-if="context.loading" class="center-state"><RefreshCw class="spin" :size="22" /><span>正在读取对话</span></div>
        <div v-else-if="!context.visibleMessages.length" class="empty-chat"><slot name="welcome"><h2>{{ context.isAgentWorkspace ? '新任务' : '我在这里' }}</h2><p v-if="!context.isAgentWorkspace">现在想说什么？</p></slot></div>
        <div v-else class="message-list">
          <button v-if="context.visibleMessages.length >= 120 && context.historyHasMore" type="button" class="chat-load-history" :disabled="context.historyLoading" @click="context.loadOlderMessages">{{ context.historyLoading ? '正在读取更早对话…' : '加载更早对话' }}</button>
          <slot name="execution-summary" />
          <article v-for="turn in context.messageTurns" :key="`${turn.role}-${turn.id}`" :class="['message-row', turn.role]" :data-message-anchor="`${turn.role}-${turn.id}`">
            <img v-if="turn.role === 'assistant'" class="message-avatar assistant-avatar" :src="context.avatarUrl" :alt="context.mioDisplayName" />
            <div class="message-body">
              <div class="message-heading"><strong>{{ turn.role === 'assistant' ? context.mioDisplayName : '你' }}</strong><small v-if="activeConversation?.kind === 'wechat'">{{ turn.source === 'wechat' ? '微信' : '电脑' }}</small></div>
              <details v-if="surface && turn.role === 'assistant' && turn.agent_task_id" class="agent-saved-process" @toggle="openProcess($event,turn.id)"><summary>查看分析与执行过程</summary><AgentTaskProgress inline-chat v-if="openedProcesses.has(turn.id)" :task-id="turn.agent_task_id" :run-id="turn.agent_run_id" /></details>
              <details v-if="context.isAgentWorkspace && turn.role === 'assistant' && context.turnToolReceipts(turn).length" class="agent-execution-panel" :open="agentProcessExpanded">
                <summary>
                  <ListChecks :size="16" />
                  <strong>执行结果</strong>
                  <small>{{ context.agentExecutionStatus(turn) }}</small>
                  <ChevronDown :size="15" />
                </summary>
                <div class="agent-execution-body">
                  <ol class="agent-execution-steps">
                    <li v-for="receipt in context.turnToolReceipts(turn)" :key="receipt.receipt_id || `${turn.id}-${receipt.tool_name}`" :class="`status-${receipt.status || 'unknown'}`">
                      <i /><span><strong>{{ context.toolReceiptLabel(receipt) }}</strong><small>{{ context.toolReceiptStatus(receipt) }}</small><small v-if="receiptSummary(receipt)">{{ receiptSummary(receipt) }}</small></span>
                    </li>
                  </ol>
                  <section v-if="context.turnCreationJob(turn)" class="agent-prompt-sheet">
                    <div>
                      <span>正面提示词</span>
                      <p>{{ context.turnCreationPrompt(turn).positive }}</p>
                    </div>
                    <div v-if="context.turnCreationPrompt(turn).negative">
                      <span>负面提示词</span>
                      <p>{{ context.turnCreationPrompt(turn).negative }}</p>
                    </div>
                    <small v-else-if="context.turnCreationJob(turn).media_type === 'video'">当前视频工作流使用正面动作提示词，没有独立负面提示词输入。</small>
                    <div v-if="context.turnCreationLoras(turn).length">
                      <span>本次 LoRA</span>
                      <p>{{ context.turnCreationLoras(turn).join(' · ') }}</p>
                    </div>
                    <div v-if="context.turnCreationWorkflow(turn)" class="agent-workflow-snapshot">
                      <span>实际工作流</span>
                      <p>{{ context.turnCreationWorkflow(turn).source_file }} · {{ context.turnCreationWorkflow(turn).source_sha256 ? context.turnCreationWorkflow(turn).source_sha256.slice(0, 12) : '未读取哈希' }}</p>
                      <a :href="`/api/creation/jobs/${context.turnCreationJob(turn).id}/workflow`">下载本次使用的工作流</a>
                      <small v-if="context.turnCreationWorkflow(turn).models?.length">模型：{{ context.turnCreationWorkflow(turn).models.map((item) => item.name).join(' · ') }}</small>
                      <small v-if="context.turnCreationWorkflow(turn).loras?.length">LoRA：{{ context.turnCreationWorkflow(turn).loras.map((item) => `${item.name}${item.on ? '（开）' : '（关）'}`).join(' · ') }}</small>
                    </div>
                    <div v-if="context.turnCreationSampler(turn)">
                      <span>采样配置</span>
                      <p>{{ context.turnCreationSampler(turn) }}</p>
                    </div>
                  </section>
                  <div v-if="context.turnCreationJob(turn) && context.agentExecutionProgress(turn) < 100" class="agent-generation-progress" role="progressbar" :aria-valuenow="context.agentExecutionProgress(turn)" aria-valuemin="0" aria-valuemax="100">
                    <i :style="{ width: `${context.agentExecutionProgress(turn)}%` }" />
                  </div>
                  <footer class="agent-execution-meta">
                    <span>本轮累计 {{ context.turnTokenCount(turn).toLocaleString('zh-CN') }} token</span>
                    <span>{{ context.formatCost(turn) }}</span>
                    <span v-if="context.turnCreationJob(turn)">{{ context.agentExecutionElapsed(turn) }}</span>
                    <span v-if="context.turnCreationJob(turn)">{{ context.agentCreationCost(turn) }}</span>
                  </footer>
                </div>
              </details>
              <div v-for="part in turn.parts" v-show="part.content" :key="part.id" :class="['message-part', { 'cleanup-selected': selectedQqMessageIds.includes(Number(part.id)) }]">
                <button
                  v-if="qqCleanupMode"
                  class="message-cleanup-toggle"
                  type="button"
                  role="checkbox"
                  :aria-checked="selectedQqMessageIds.includes(Number(part.id))"
                  :title="selectedQqMessageIds.includes(Number(part.id)) ? '取消选择' : '选择这句消息'"
                  @click="toggleQqMessage(part.id)"
                >
                  <Check v-if="selectedQqMessageIds.includes(Number(part.id))" :size="14" />
                  <Square v-else :size="13" />
                </button>
                <div v-if="surface && turn.role === 'assistant'" class="message-bubble markdown-body" v-html="renderAgentMarkdown(part.content)" /><div v-else class="message-bubble">{{ part.content }}</div>
                <small v-if="part.delivery_status" class="message-delivery-state" :title="part.delivery_error || ''">{{ ({ pending: '等待发送到微信', sending: '正在发送到微信', sent: '微信已接收', failed: '微信发送失败', unknown: '微信送达结果待核对', superseded: '已被新任务结果替代' })[part.delivery_status] || part.delivery_status }}</small>
              </div>
              <div v-if="context.turnAttachments(turn).length" class="message-attachments">
                <template v-for="attachment in context.turnAttachments(turn)" :key="`${turn.id}-${attachment.url || attachment.name}`">
                  <button v-if="attachmentArtifact(attachment)" :class="['message-attachment', attachment.kind]" type="button" :title="`在右侧浏览 ${attachment.name}`" @click="emit('preview-artifact', attachmentArtifact(attachment))"><img v-if="attachment.kind === 'image' && attachment.url" :src="attachment.url" :alt="attachment.name" /><FileText v-else :size="18" /><span><strong>{{ attachment.name }}</strong><small>{{ context.formatFileSize(attachment.size || 0) }} · 点击在右侧浏览</small></span></button>
                  <button v-else-if="attachment.kind === 'image' && attachment.url" class="message-attachment image message-image-preview" type="button" :title="`在应用内查看 ${attachment.name}`" @click="openImagePreview(attachment)">
                    <img :src="attachment.url" :alt="attachment.name" />
                    <span><strong>{{ attachment.name }}</strong><small>{{ context.formatFileSize(attachment.size || 0) }} · 点击查看原图</small></span>
                  </button>
                  <div v-else-if="attachment.kind === 'video' && attachment.url" class="message-attachment video"><video :src="attachment.url" controls preload="metadata" /></div>
                  <a v-else :href="attachment.url || undefined" :download="attachment.url ? attachment.name : undefined" :class="['message-attachment', attachment.kind]"><FileText :size="18" /><span><strong>{{ attachment.name }}</strong><small>{{ context.formatFileSize(attachment.size || 0) }}</small></span></a>
                </template>
              </div>
              <div :class="['message-hover-meta', { 'always-visible': context.isAgentWorkspace && !surface }]">
                <span v-if="context.displayVisibility.message_time">{{ context.formatRealTime(turn.created_at) }}</span>
                <template v-if="turn.role === 'assistant'">
                  <span v-if="context.displayVisibility.message_model">{{ context.messageModelLabel(turn) }}</span>
                  <span v-if="context.displayVisibility.message_model">{{ context.reasoningLabel(turn.reasoning_level || 'auto') }}</span>
                  <span v-if="context.isAgentWorkspace || context.displayVisibility.message_tokens" class="message-token-usage" :title="messageTokenUsageTitle(turn, context.isAgentWorkspace)">{{ context.isAgentWorkspace ? '本轮累计 ' : '' }}{{ Number(turn.prompt_tokens || 0) + Number(turn.completion_tokens || 0) }} token · {{ messageCacheRateLabel(turn) }}</span>
                  <span v-if="context.isAgentWorkspace || context.displayVisibility.response_latency">{{ turn.total_latency_ms ? `${(Number(turn.total_latency_ms) / 1000).toFixed(1)} 秒` : '耗时未记录' }}</span>
                  <span v-if="context.isAgentWorkspace || context.displayVisibility.message_cost">{{ context.formatCost(turn) }}</span>
                  <span v-if="context.displayVisibility.message_play">{{ context.turnVoiceLanguageLabel(turn) }}</span>
                  <button v-if="context.displayVisibility.message_play" type="button" :title="context.speakingPartId === context.turnId(turn) ? '停止播放' : `播放整轮语音（${context.turnVoiceLanguageLabel(turn)}）`" @click="context.playMessageVoice(turn)">
                    <RefreshCw v-if="context.voiceLoadingPartId === context.turnId(turn)" class="spin" :size="12" />
                    <Square v-else-if="context.speakingPartId === context.turnId(turn)" :size="10" />
                    <Volume2 v-else :size="13" />
                  </button>
                </template>
                <button v-if="context.displayVisibility.message_copy" type="button" :title="context.copiedTurnId === context.turnId(turn) ? '已复制' : '复制'" @click="context.copyTurn(turn)"><Check v-if="context.copiedTurnId === context.turnId(turn)" :size="12" /><Copy v-else :size="12" /></button>
              </div>
            </div>
            <img v-if="!surface && turn.role === 'user' && context.userAvatarCustom" class="message-avatar user-avatar" :src="context.userAvatarUrl" alt="你" />
            <span v-else-if="!surface && turn.role === 'user'" class="message-avatar user-avatar" aria-label="你"><UserRound :size="18" /></span>
          </article>
          <section v-if="surface && props.artifacts.length" class="agent-message-artifacts" aria-label="本对话成果"><small>本对话成果</small><button v-for="artifact in props.artifacts" :key="artifact.id" type="button" @click="emit('preview-artifact',artifact)"><FileText :size="18" /><span><strong>{{ artifact.name }}</strong><small>v{{ artifact.version }} · 点击查看内容与来源</small></span><ArrowUpRight :size="16" /></button></section>
          <article v-if="context.sending" class="message-row assistant pending-message"><img class="message-avatar assistant-avatar" :src="context.avatarUrl" :alt="context.mioDisplayName" /><div class="message-body"><div class="message-heading"><strong>{{ context.mioDisplayName }}</strong><span>{{ surface?.currentTask ? '正在处理' : '正在连接模型' }}</span></div><AgentTaskProgress inline-chat v-if="surface?.currentTask" :key="surface.currentTask.id" :task-id="surface.currentTask.id" :task="surface.currentTask" /><div v-else class="thinking-indicator"><i /><i /><i /></div></div></article>
        </div>
      </div>
      <MessageNavigator v-if="surface && context.agentSurfaceMode !== 'image'" :scroll-element="context.chatScroll" :turns="context.messageTurns" :name="context.mioDisplayName" />
      </div>

      <div v-if="qqCleanupMode" class="qq-message-cleanup-bar">
        <span>{{ selectedQqMessageIds.length ? `已选择 ${selectedQqMessageIds.length} 句` : '选择要清理的消息' }}</span>
        <button type="button" @click="cancelQqCleanup">取消</button>
        <button type="button" class="danger" :disabled="!selectedQqMessageIds.length" @click="deleteSelectedQqMessages"><Trash2 :size="14" />清理</button>
      </div>
      <div v-else class="composer-wrap">
        <AgentTasksPage v-if="context.isAgentWorkspace && !surface" :key="context.selectedConversationId" compact :conversation-id="context.selectedConversationId" />
        <slot name="task-controls" />
        <slot v-if="!surface" name="composer-context" />
        <div class="composer">
          <div v-if="context.attachments.length" class="attachment-preview-list">
            <div v-for="attachment in context.attachments" :key="attachment.id" class="attachment-preview">
              <img v-if="attachment.kind === 'image'" :src="attachment.preview_url" :alt="attachment.name" /><FileText v-else :size="19" />
              <span><strong>{{ attachment.name }}</strong><small>{{ context.formatFileSize(attachment.size) }}</small></span><button type="button" title="移除附件" @click="context.removeAttachment(attachment.id)"><X :size="14" /></button>
            </div>
          </div>
          <p v-if="context.attachments.some((item) => item.kind === 'image') && !context.activeModelSupportsVision" class="vision-warning">当前模型不支持识图，图片会保存在本地，但{{ context.mioDisplayName }}无法读取内容</p>
          <textarea ref="composerInput" v-model="context.draft" class="composer-input" rows="1" :placeholder="surface ? ({chat: `和${context.mioDisplayName}聊点什么…`, learn: '今天想学些什么，可附上资料…', image: '描述你想画的画面，可附上参考图…', work: '描述你想完成的任务，可附上文件或图片…'}[surface.mode]) : context.isAgentWorkspace ? '描述你想完成的任务，可附上文件或图片…' : `和${context.mioDisplayName}聊点什么...`" @keydown="context.handleComposerKeydown" @paste="context.handleComposerPaste" />
          <div class="composer-actions">
            <input :ref="(element) => { context.fileInput = element }" class="file-input" type="file" multiple @change="context.handleFileSelection" />
            <button type="button" class="icon-button" title="添加图片或文件" @click="context.fileInput?.click()"><Plus v-if="surface" :size="18" /><Paperclip v-else :size="18" /></button>
            <slot v-if="surface" name="composer-context" />
            <AgentToolPicker v-if="context.isAgentWorkspace && !surface" v-model="context.creationWorkflowId" :mode="surface?.mode || (context.isAgentWorkspace ? 'work' : '')" :disabled="context.sending" />
            <ExecutionPolicy v-if="context.isAgentWorkspace" compact :disabled="context.sending" />
            <span class="composer-flex-spacer" />
            <div class="context-usage-control" :aria-label="contextWindow.label" tabindex="0"><span class="context-usage-ring" :style="{ '--context-percent': `${Math.min(100, contextWindow.percent)}%` }"><i /></span><div class="context-usage-tooltip" role="tooltip"><strong>上下文预算</strong><span>{{ contextWindow.amount }}</span><small>已使用 {{ contextWindow.percent.toFixed(1) }}%</small><small>{{ contextWindow.compression }}</small><small v-if="context.isAgentWorkspace">当前规划占用与本轮累计消耗分别统计</small></div></div>
            <div :ref="(element) => { context.modelPicker = element }" class="model-picker">
              <button class="model-picker-trigger" type="button" :aria-expanded="context.showModelMenu" title="切换模型与思考程度" @click="context.toggleModelMenu"><span>{{ context.compactActiveModelLabel }}</span><small v-if="!context.isAutoRouting">{{ context.compactCurrentReasoningLabel }}</small><ChevronDown :size="13" /></button>
              <div v-if="context.showModelMenu" class="model-picker-popover">
                <template v-if="context.modelMenuSection === 'root'"><button type="button" @click="context.modelMenuSection = 'models'"><span>模型</span><small>{{ context.compactActiveModelLabel }}</small><ChevronRight :size="15" /></button><button type="button" @click="context.modelMenuSection = 'reasoning'"><span>推理强度</span><small>{{ context.compactCurrentReasoningLabel }}</small><ChevronRight :size="15" /></button></template>
                <template v-else-if="context.modelMenuSection === 'models'"><header><button type="button" title="返回" @click="context.modelMenuSection = 'root'"><ChevronLeft :size="16" /></button><strong>选择模型</strong></header><div class="model-picker-list"><button type="button" :class="{ selected: context.selectedModel === 'auto' }" @click="context.chooseModel('auto')"><span><strong>自动</strong><small>按话题难度选择</small></span><Check v-if="context.selectedModel === 'auto'" :size="15" /></button><template v-for="group in context.modelGroups" :key="group.provider_id"><p>{{ group.provider }}</p><button v-for="model in group.models" :key="model.id" type="button" :class="{ selected: context.selectedModel === model.id }" @click="context.chooseModel(model.id)"><span><strong>{{ model.display_name }}</strong><small>{{ model.supports_vision ? '识图 · ' : '' }}{{ model.supports_reasoning ? '推理 · ' : '' }}{{ model.model }}</small></span><Check v-if="context.selectedModel === model.id" :size="15" /></button></template></div></template>
                <template v-else><header><button type="button" title="返回" @click="context.modelMenuSection = 'root'"><ChevronLeft :size="16" /></button><strong>推理强度</strong></header><div class="model-picker-list reasoning-list"><button v-for="option in context.activeReasoningOptions" :key="option.id" type="button" :class="{ selected: context.reasoningLevel === option.id }" @click="context.chooseReasoning(option.id)"><span><strong>{{ option.label }}</strong><small>{{ option.description }}</small></span><Check v-if="context.reasoningLevel === option.id" :size="15" /></button></div></template>
              </div>
            </div>
            <button v-if="!context.isAgentWorkspace" class="composer-settings" type="button" title="模型与API设置" @click="context.openSettingsSection('models')"><Settings :size="16" /></button>
            <button type="button" class="send-button" :title="context.sending ? '停止回复' : '发送'" :disabled="!context.sending && !context.draft.trim() && !context.attachments.length" @click="context.sending ? context.cancelActiveChat() : context.sendMessage()"><Square v-if="context.sending" :size="13" /><ArrowUp v-else-if="surface" :size="17" /><Send v-else :size="17" /></button>
          </div>
        </div>
      </div>
    </section>
  </div>

  <ImagePreview v-if="activeImageAttachment" :key="activeImageAttachment.url" :attachment="activeImageAttachment" @close="closeImagePreview" />
</template>
