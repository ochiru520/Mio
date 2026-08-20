<script setup>
import { inject, ref } from 'vue'
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  FileText,
  MessageSquareText,
  Paperclip,
  Pin,
  Plus,
  RefreshCw,
  Send,
  Settings,
  Square,
  SquarePen,
  Trash2,
  Upload,
  UserRound,
  Volume2,
  X,
} from '@lucide/vue'

const context = inject('mio-chat-page')
if (!context) throw new Error('对话页上下文未初始化')
const conversationDrawerOpen = ref(false)
const conversationDrawerPinned = ref(localStorage.getItem('mio_conversation_drawer_pinned') === 'true')

async function openConversation(conversationId) {
  await context.selectConversation(conversationId)
  if (!conversationDrawerPinned.value) conversationDrawerOpen.value = false
}

function toggleConversationDrawerPin() {
  conversationDrawerPinned.value = !conversationDrawerPinned.value
  localStorage.setItem('mio_conversation_drawer_pinned', String(conversationDrawerPinned.value))
}
</script>

<template>
  <div :class="['chat-layout', 'integrated-chat-layout', `chat-display-${context.displayMode}`, { 'conversation-drawer-pinned': conversationDrawerPinned, 'has-custom-background': Boolean(context.chatBackgroundStyle?.['--chat-background-image']) }]" :style="context.chatBackgroundStyle || {}">
    <button
      v-if="conversationDrawerOpen && !conversationDrawerPinned"
      class="conversation-drawer-backdrop"
      type="button"
      aria-label="关闭对话窗口"
      @click="conversationDrawerOpen = false"
    />
    <aside v-if="conversationDrawerOpen || conversationDrawerPinned" class="conversation-drawer">
      <header>
        <div><MessageSquareText :size="17" /><strong>对话窗口</strong><span>{{ context.conversations.length }}</span></div>
        <button type="button" :class="{ active: conversationDrawerPinned }" :title="conversationDrawerPinned ? '取消固定' : '固定抽屉'" @click="toggleConversationDrawerPin"><Pin :size="14" /></button>
      </header>
      <button class="conversation-new-button" type="button" @click="context.createNewConversation"><Plus :size="15" />新对话</button>
      <div class="conversation-drawer-list">
        <article v-for="conversation in context.conversations" :key="conversation.id" :class="{ active: context.selectedConversationId === conversation.id }">
          <button type="button" @click="openConversation(conversation.id)">
            <span><strong>{{ conversation.title }}</strong><small>{{ conversation.preview || (conversation.kind === 'qq' ? '与 QQ 共享上下文' : conversation.kind === 'pet' ? '桌宠独立对话' : '还没有消息') }}</small></span>
            <time>{{ context.formatSidebarTime(conversation.updated_at) }}</time>
          </button>
          <div v-if="conversation.kind === 'desktop'" class="conversation-drawer-actions">
            <button type="button" title="重命名" @click="context.renameConversation(conversation)"><SquarePen :size="13" /></button>
            <button type="button" title="删除" @click="context.deleteConversation(conversation)"><Trash2 :size="13" /></button>
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
      <div v-if="context.isFileDragging" class="file-drop-overlay"><Upload :size="25" /><strong>松开即可添加到对话</strong><span>图片、PDF、Word 和其他文件都可以</span></div>

      <div :ref="(element) => { context.chatScroll = element }" class="message-scroll">
        <div v-if="context.loading" class="center-state"><RefreshCw class="spin" :size="22" /><span>正在读取对话</span></div>
        <div v-else-if="!context.visibleMessages.length" class="empty-chat"><h2>我在这里</h2><p>现在想说什么？</p></div>
        <div v-else class="message-list">
          <article v-for="turn in context.messageTurns" :key="`${turn.role}-${turn.id}`" :class="['message-row', turn.role]">
            <img v-if="turn.role === 'assistant'" class="message-avatar assistant-avatar" :src="context.avatarUrl" :alt="context.mioDisplayName" />
            <div class="message-body">
              <div class="message-heading"><strong>{{ turn.role === 'assistant' ? context.mioDisplayName : '你' }}</strong></div>
              <div v-for="part in turn.parts" v-show="part.content" :key="part.id" class="message-part"><div class="message-bubble">{{ part.content }}</div></div>
              <div v-if="context.turnAttachments(turn).length" class="message-attachments">
                <a v-for="attachment in context.turnAttachments(turn)" :key="`${turn.id}-${attachment.url || attachment.name}`" :href="attachment.url || undefined" :target="attachment.url && attachment.kind === 'image' ? '_blank' : undefined" :download="attachment.url && attachment.kind !== 'image' ? attachment.name : undefined" :class="['message-attachment', attachment.kind]">
                  <img v-if="attachment.kind === 'image' && attachment.url" :src="attachment.url" :alt="attachment.name" />
                  <FileText v-else :size="18" /><span><strong>{{ attachment.name }}</strong><small>{{ context.formatFileSize(attachment.size || 0) }}</small></span>
                </a>
              </div>
              <div class="message-hover-meta">
                <span v-if="context.displayVisibility.message_time">{{ context.formatRealTime(turn.created_at) }}</span>
                <template v-if="turn.role === 'assistant'">
                  <span v-if="context.displayVisibility.message_model">{{ context.messageModelLabel(turn) }}</span>
                  <span v-if="context.displayVisibility.message_model">{{ context.reasoningLabel(turn.reasoning_level || 'auto') }}</span>
                  <span v-if="context.displayVisibility.message_tokens">{{ Number(turn.prompt_tokens || 0) + Number(turn.completion_tokens || 0) }} token</span>
                  <span v-if="context.displayVisibility.response_latency">{{ turn.total_latency_ms ? `${(Number(turn.total_latency_ms) / 1000).toFixed(1)} 秒` : '耗时未记录' }}</span>
                  <span v-if="context.displayVisibility.message_cost">{{ context.formatCost(turn) }}</span>
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
            <img v-if="turn.role === 'user' && context.userAvatarCustom" class="message-avatar user-avatar" :src="context.userAvatarUrl" alt="你" />
            <span v-else-if="turn.role === 'user'" class="message-avatar user-avatar" aria-label="你"><UserRound :size="18" /></span>
          </article>
          <article v-if="context.sending" class="message-row assistant pending-message"><img class="message-avatar assistant-avatar" :src="context.avatarUrl" :alt="context.mioDisplayName" /><div class="message-body"><div class="message-heading"><strong>{{ context.mioDisplayName }}</strong><span>正在思考</span></div><div class="thinking-indicator"><i /><i /><i /></div></div></article>
        </div>
      </div>

      <div class="composer-wrap">
        <div class="composer">
          <div v-if="context.attachments.length" class="attachment-preview-list">
            <div v-for="attachment in context.attachments" :key="attachment.id" class="attachment-preview">
              <img v-if="attachment.kind === 'image'" :src="attachment.preview_url" :alt="attachment.name" /><FileText v-else :size="19" />
              <span><strong>{{ attachment.name }}</strong><small>{{ context.formatFileSize(attachment.size) }}</small></span><button type="button" title="移除附件" @click="context.removeAttachment(attachment.id)"><X :size="14" /></button>
            </div>
          </div>
          <p v-if="context.attachments.some((item) => item.kind === 'image') && !context.activeModelSupportsVision" class="vision-warning">当前模型不支持识图，图片会保存在本地，但{{ context.mioDisplayName }}无法读取内容</p>
          <textarea v-model="context.draft" class="composer-input" rows="1" :placeholder="`和${context.mioDisplayName}聊点什么...`" @keydown="context.handleComposerKeydown" @paste="context.handleComposerPaste" />
          <div class="composer-actions">
            <input :ref="(element) => { context.fileInput = element }" class="file-input" type="file" multiple @change="context.handleFileSelection" />
            <button type="button" class="icon-button" title="添加图片或文件" @click="context.fileInput?.click()"><Paperclip :size="18" /></button>
            <span class="composer-flex-spacer" />
            <div class="context-usage-control" :aria-label="context.contextUsageLabel" tabindex="0"><span class="context-usage-ring" :style="context.contextRingStyle"><i /></span><div class="context-usage-tooltip" role="tooltip"><strong>上下文窗口</strong><span>{{ Number(context.contextUsage.used_chars || 0).toLocaleString('zh-CN') }} / {{ Number(context.contextUsage.max_chars || 18000).toLocaleString('zh-CN') }} 字</span><small>已使用 {{ context.contextPercent.toFixed(1) }}%</small></div></div>
            <div :ref="(element) => { context.modelPicker = element }" class="model-picker">
              <button class="model-picker-trigger" type="button" :aria-expanded="context.showModelMenu" title="切换模型与思考程度" @click="context.toggleModelMenu"><span>{{ context.compactActiveModelLabel }}</span><small v-if="!context.isAutoRouting">{{ context.compactCurrentReasoningLabel }}</small><ChevronDown :size="13" /></button>
              <div v-if="context.showModelMenu" class="model-picker-popover">
                <template v-if="context.modelMenuSection === 'root'"><button type="button" @click="context.modelMenuSection = 'models'"><span>模型</span><small>{{ context.compactActiveModelLabel }}</small><ChevronRight :size="15" /></button><button type="button" @click="context.modelMenuSection = 'reasoning'"><span>推理强度</span><small>{{ context.compactCurrentReasoningLabel }}</small><ChevronRight :size="15" /></button></template>
                <template v-else-if="context.modelMenuSection === 'models'"><header><button type="button" title="返回" @click="context.modelMenuSection = 'root'"><ChevronLeft :size="16" /></button><strong>选择模型</strong></header><div class="model-picker-list"><button type="button" :class="{ selected: context.selectedModel === 'auto' }" @click="context.chooseModel('auto')"><span><strong>自动</strong><small>按话题难度选择</small></span><Check v-if="context.selectedModel === 'auto'" :size="15" /></button><template v-for="group in context.modelGroups" :key="group.provider_id"><p>{{ group.provider }}</p><button v-for="model in group.models" :key="model.id" type="button" :class="{ selected: context.selectedModel === model.id }" @click="context.chooseModel(model.id)"><span><strong>{{ model.display_name }}</strong><small>{{ model.model }}</small></span><Check v-if="context.selectedModel === model.id" :size="15" /></button></template></div></template>
                <template v-else><header><button type="button" title="返回" @click="context.modelMenuSection = 'root'"><ChevronLeft :size="16" /></button><strong>推理强度</strong></header><div class="model-picker-list reasoning-list"><button v-for="option in context.activeReasoningOptions" :key="option.id" type="button" :class="{ selected: context.reasoningLevel === option.id }" @click="context.chooseReasoning(option.id)"><span><strong>{{ option.label }}</strong><small>{{ option.description }}</small></span><Check v-if="context.reasoningLevel === option.id" :size="15" /></button></div></template>
              </div>
            </div>
            <button class="composer-settings" type="button" title="模型与API设置" @click="context.openSettingsSection('models')"><Settings :size="16" /></button>
            <button type="button" class="send-button" :title="context.sending ? '停止回复' : '发送'" :disabled="!context.sending && !context.draft.trim() && !context.attachments.length" @click="context.sending ? context.cancelActiveChat() : context.sendMessage()"><Square v-if="context.sending" :size="13" /><Send v-else :size="17" /></button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
