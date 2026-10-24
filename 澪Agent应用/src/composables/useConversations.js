/** Conversations operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import { apiRequest } from '../services/api.js'
import * as chatApi from '../services/chatApi.js'
import { contextUsagePresentation } from '../contextUsageState.js'
import { filterConversationsForWorkspace } from '../workspaceState.js'
import { nextTick, ref } from 'vue'
import { captureReadingPosition, restoreReadingPosition } from '../conversationScroll.js'

export function useConversations(deps) {
  let pendingChat = null
  let reconciliationInFlight = false
  function finishPendingChat(pending) {
    if (pendingChat !== pending) return
    pendingChat = null
    deps.chatAbortController = null
    deps.activeChatCancelPayload = null
    deps.sending.value = false
  }
  async function reconcilePendingChat() {
    const pending = pendingChat
    if (!pending || reconciliationInFlight) return
    reconciliationInFlight = true
    try {
      const status = await deps.request(`/api/agent/chat/requests/${encodeURIComponent(pending.clientRequestId)}?conversation_id=${encodeURIComponent(pending.conversationId)}`)
      if (pendingChat !== pending) return
      if (status.status === 'pending') return
      if (deps.selectedConversationId.value === pending.conversationId) {
        deps.errorMessage.value = status.status === 'succeeded' ? '' : (status.error?.message || '后台回复失败，请查看任务状态')
        if (status.response?.context_usage) deps.contextUsage.value = status.response.context_usage
        if (status.response?.task_handoff?.task_id) deps.latestTaskHandoff.value = status.response.task_handoff
      }
      finishPendingChat(pending)
    } catch (error) {
      if (error?.status === 404 && pendingChat === pending) {
        if (deps.selectedConversationId.value === pending.conversationId) deps.errorMessage.value = '后台没有收到这条请求，请重新发送'
        finishPendingChat(pending)
      }
      // Reconnecting must never resubmit a paid request or cancel backend work.
    } finally {
      reconciliationInFlight = false
    }
  }
  const historyLoading=ref(false), historyHasMore=ref(true)
  async function loadOlderMessages() {
    if(historyLoading.value || !historyHasMore.value || deps.loading.value)return
    const id=deps.selectedConversationId.value,first=deps.messages.value[0]?.id
    if(!Number.isSafeInteger(first))return
    historyLoading.value=true
    try {
      const older=await deps.request(`/api/agent/messages?limit=120&before_id=${first}&conversation_id=${encodeURIComponent(id)}`)
      if(id!==deps.selectedConversationId.value)return
      const scroll=deps.chatScroll?.value,position=captureReadingPosition(scroll)
      const ids=new Set(deps.messages.value.map(item=>item.id))
      deps.messages.value=[...older.filter(item=>!ids.has(item.id)),...deps.messages.value]
      historyHasMore.value=older.length===120
      await nextTick();restoreReadingPosition(scroll,position)
    }catch(error){deps.errorMessage.value=error.message}finally{historyLoading.value=false}
  }
  async function refreshMessages() {
    await reconcilePendingChat()
    const conversationId = deps.selectedConversationId.value
    if (!conversationId || deps.messageRefreshInFlight) return
    deps.messageRefreshInFlight = true
    try {
      await refreshConversations()
      const data = await deps.request(`/api/agent/messages?limit=120&conversation_id=${encodeURIComponent(conversationId)}`)
      if (deps.selectedConversationId.value !== conversationId) return
      const previousLastId = deps.messages.value.at(-1)?.id
      const scroll=deps.chatScroll?.value,position=captureReadingPosition(scroll)
      const first=data[0]?.id
      const prefix=Number.isSafeInteger(first) ? deps.messages.value.filter(item=>Number.isSafeInteger(item.id) && item.id<first) : []
      deps.messages.value = [...prefix,...data]
      await nextTick();restoreReadingPosition(scroll,position)
      if (data.at(-1)?.id !== previousLastId || deps.isAgentWorkspace) {
        await deps.refreshContextUsage()
      }
    } catch {
      // Polling failures are reflected by the next bootstrap/status action.
    } finally {
      deps.messageRefreshInFlight = false
    }
  }
  async function requestStartupGreeting() {
    if (!deps.selectedConversationId.value) return
    try {
      const result = await deps.request('/api/agent/startup-greeting', {
        method: 'POST',
        body: JSON.stringify({ conversation_id: deps.selectedConversationId.value }),
      })
      if (result.sent) {
        await Promise.all([refreshMessages(), refreshConversations()])
      }
    } catch {
      // A startup greeting should never prevent the application from opening.
    }
  }
  function createClientRequestId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
    return `desktop-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`
  }
  function openAgentHandoff() {
    const target = deps.latestTaskHandoff.value?.conversation_id
    if (!target) return
    localStorage.setItem('mio_agent_active_view', 'chat')
    const next = new URL(window.location.href)
    next.searchParams.set('workspace', 'agent')
    next.searchParams.set('conversation_id', target)
    window.location.href = next.toString()
  }
  async function sendMessage() {
    const content = deps.draft.value.trim()
    if ((!content && !deps.attachments.value.length) || deps.sending.value) return
    const conversationId = deps.selectedConversationId.value
    const clientRequestId = createClientRequestId()
    const creationWorkflowId = deps.isAgentWorkspace ? deps.creationWorkflowId?.value || '' : ''
    const outgoingAttachments = deps.attachments.value.map((item) => ({
      kind: item.kind,
      name: item.name,
      mime_type: item.mime_type,
      data_url: item.data_url || '',
      text: item.text || '',
      size: item.size,
      ephemeral: Boolean(item.ephemeral),
    }))
    const localAttachments = deps.attachments.value.map((item) => ({
      kind: item.kind,
      name: item.name,
      mime_type: item.mime_type,
      size: item.size,
      url: item.preview_url || '',
    }))
    deps.draft.value = ''
    deps.attachments.value = []
    deps.sending.value = true
    deps.errorMessage.value = ''
    deps.messages.value.push({
      id: `local-${clientRequestId}`,
      request_id: clientRequestId,
      role: 'user',
      content: content || '发送了附件',
      attachments: localAttachments,
      source: 'desktop',
      created_at: new Date().toISOString(),
      request_cost_yuan: 0,
    })
    await deps.scrollToBottom()
    try {
      const isDesktopPetConversation = conversationId === 'desktop_pet'
      const endpoint = isDesktopPetConversation && !outgoingAttachments.length
        ? '/api/companion/chat'
        : '/api/agent/chat'
      const payload = isDesktopPetConversation && !outgoingAttachments.length
        ? {
            message: content,
            reasoning_level: deps.reasoningLevel.value,
            model_id: deps.selectedModel.value,
            client_request_id: clientRequestId,
          }
        : {
            message: content,
            reasoning_level: deps.reasoningLevel.value,
            model_id: deps.selectedModel.value,
            conversation_id: conversationId,
            attachments: outgoingAttachments,
            creation_tools_enabled: deps.isAgentWorkspace,
            creation_workflow_id: creationWorkflowId,
            agent_workspace: deps.isAgentWorkspace,
            mode: deps.isAgentWorkspace ? 'agent' : 'companion',
            surface_mode: deps.agentSurfaceMode?.value || 'work',
            client_request_id: clientRequestId,
          }
      const controller = new AbortController()
      deps.chatAbortController = controller
      deps.activeChatCancelPayload = {
        endpoint,
        body: isDesktopPetConversation
          ? { client_request_id: clientRequestId }
          : { conversation_id: conversationId, client_request_id: clientRequestId },
      }
      const result = await deps.request(endpoint, {
        method: 'POST',
        body: JSON.stringify(payload),
        signal: controller.signal,
        // An Agent request covers multiple model calls and tools. Its execution
        // budget is enforced by the backend, rather than a single HTTP deadline.
        ...(deps.isAgentWorkspace ? { timeoutMs: 0 } : {}),
      })
      if (result.context_usage && deps.selectedConversationId.value === conversationId) {
        deps.contextUsage.value = result.context_usage
      }
      if (result.task_handoff?.task_id && deps.selectedConversationId.value === conversationId) {
        ++deps.handoffLoadVersion
        deps.latestTaskHandoff.value = result.task_handoff
      }
      await refreshMessages()
      if (result.request_id && deps.selectedConversationId.value === conversationId) {
        deps.messages.value = deps.messages.value.map((message) => (
          message.request_id === result.request_id
            ? {
                ...message,
                first_token_latency_ms: result.first_token_latency_ms,
                total_latency_ms: result.total_latency_ms,
                agent_run_id: result.agent_run_id || message.agent_run_id || '',
                agent_run_status: result.agent_run_status || message.agent_run_status || '',
                tool_receipts: result.tool_receipts || message.tool_receipts || [],
              }
            : message
        ))
      }
      await refreshConversations()
    } catch (error) {
      const reconnect = ['request_timeout', 'network_error'].includes(error?.code)
      if (reconnect && deps.activeChatCancelPayload) {
        pendingChat = { conversationId, clientRequestId }
      }
      if (deps.selectedConversationId.value === conversationId) {
        deps.errorMessage.value = reconnect ? '连接暂时中断，正在核对后台回复；任务仍可在后台继续，可点击停止'
          : error?.code === 'request_cancelled' ? '已停止这次回复' : deps.modelRequestError(error)
      }
      await refreshMessages()
      await refreshConversations()
    } finally {
      if (!pendingChat) {
        deps.chatAbortController = null
        deps.activeChatCancelPayload = null
        deps.sending.value = false
      }
    }
  }
  async function requestChatCancellation(cancelPayload) {
    return apiRequest(`${cancelPayload.endpoint}/cancel`, {
      method: 'POST',
      body: JSON.stringify(cancelPayload.body),
      deadlineClass: 'mutation',
    })
  }
  async function cancelActiveChat() {
    if (!deps.sending.value || !deps.chatAbortController || !deps.activeChatCancelPayload) return
    const cancelPayload = deps.activeChatCancelPayload
    deps.chatAbortController.abort('user_cancelled')
    try {
      await requestChatCancellation(cancelPayload)
      if (pendingChat) {
        finishPendingChat(pendingChat)
        deps.errorMessage.value = '已停止这次回复'
        await refreshMessages()
      }
    } catch (error) {
      if (error?.code !== 'request_cancelled') deps.errorMessage.value = `停止回复失败：${error.message}`
    }
  }
  async function refreshConversations() {
    try {
      deps.conversations.value = filterConversationsForWorkspace(
        await chatApi.listConversations(),
        deps.workspaceMode,
      )
    } catch {
      // The message view remains usable if only the sidebar refresh fails.
    }
  }
  async function selectConversation(conversationId) {
    if (!conversationId || conversationId === deps.selectedConversationId.value) {
      deps.activeView.value = 'chat'
      return
    }
    deps.activeView.value = 'chat'
    deps.loading.value = true
    deps.errorMessage.value = ''
    deps.selectedConversationId.value = conversationId
    historyHasMore.value=true
    const loadVersion = ++deps.conversationLoadVersion
    localStorage.setItem(deps.conversationStorageKey, conversationId)
    try {
      const [selectedMessages, selectedUsage] = await Promise.all([
        deps.request(`/api/agent/messages?limit=120&conversation_id=${encodeURIComponent(conversationId)}`),
        deps.request(`/api/agent/context-usage?conversation_id=${encodeURIComponent(conversationId)}`),
      ])
      if (loadVersion !== deps.conversationLoadVersion || deps.selectedConversationId.value !== conversationId) return
      deps.messages.value = selectedMessages
      historyHasMore.value=selectedMessages.length===120
      deps.contextUsage.value = selectedUsage
    } catch (error) {
      if (loadVersion === deps.conversationLoadVersion && deps.selectedConversationId.value === conversationId) {
        deps.errorMessage.value = error.message
      }
    } finally {
      if (loadVersion === deps.conversationLoadVersion) deps.loading.value = false
    }
    if (loadVersion !== deps.conversationLoadVersion || deps.selectedConversationId.value !== conversationId) return
    await deps.settleChatScrollToBottom()
  }
  async function createNewConversation(mode = deps.agentSurfaceMode?.value || 'work') {
    deps.activeView.value = 'chat'
    deps.errorMessage.value = ''
    try {
      const conversation = await chatApi.createConversation(
        deps.isAgentWorkspace ? ({ chat: '新对话', work: '新任务', learn: '新学习对话', image: '新生图对话' }[mode] || '新任务') : '新对话',
        deps.isAgentWorkspace ? 'agent' : 'main',
      )
      deps.conversations.value = [conversation, ...deps.conversations.value]
      deps.selectedConversationId.value = conversation.id
      localStorage.setItem(deps.conversationStorageKey, conversation.id)
      deps.messages.value = []
      deps.contextUsage.value = { used_tokens: 0, max_tokens: contextUsagePresentation(deps.contextUsage.value).limit, percent: 0, has_summary: false }
      await nextTick()
      document.querySelector('.composer-input')?.focus()
    } catch (error) {
      deps.errorMessage.value = error.message
    }
  }
  async function renameConversation(conversation) {
    const title = await deps.showAppPrompt({
      title: '重命名对话',
      message: '输入一个便于识别的对话名称',
      value: conversation.title || '新对话',
    })
    if (title === null) return
    const normalized = title.replace(/\s+/g, ' ').trim()
    if (!normalized || normalized === conversation.title) return
    deps.errorMessage.value = ''
    try {
      const updated = await deps.request(`/api/agent/conversations/${encodeURIComponent(conversation.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ title: normalized }),
      })
      deps.conversations.value = deps.conversations.value.map((item) => (
        item.id === conversation.id ? { ...item, ...updated } : item
      ))
    } catch (error) {
      deps.errorMessage.value = error.message
    }
  }
  async function deleteConversation(conversation) {
    const confirmed = await deps.showAppConfirm({
      title: `删除“${conversation.title}”？`,
      message: '该窗口的聊天记录会被永久删除，已经生成的日记不会受影响。',
      confirmText: '删除对话',
      danger: true,
    })
    if (!confirmed) return
    deps.errorMessage.value = ''
    try {
      await deps.request(`/api/agent/conversations/${encodeURIComponent(conversation.id)}`, {
        method: 'DELETE',
      })
      const remaining = deps.conversations.value.filter((item) => item.id !== conversation.id)
      deps.conversations.value = remaining
      if (deps.selectedConversationId.value === conversation.id) {
        const nextConversation = remaining.find((item) => item.kind === 'qq') || remaining[0]
        deps.selectedConversationId.value = ''
        if (nextConversation) {
          await selectConversation(nextConversation.id)
        } else {
          deps.messages.value = []
          localStorage.removeItem(deps.conversationStorageKey)
          if (deps.isAgentWorkspace) await createNewConversation()
        }
      }
    } catch (error) {
      deps.errorMessage.value = error.message
    }
  }
  async function deleteConversationMessages(conversation, messageIds) {
    const normalizedIds = [...new Set(messageIds.map(Number).filter((id) => Number.isInteger(id) && id > 0))]
    if (conversation?.kind !== 'qq' || !normalizedIds.length) return false
    const confirmed = await deps.showAppConfirm({
      title: `清理选中的 ${normalizedIds.length} 句消息？`,
      message: '选中的消息会从 QQ 共享上下文中删除，旧会话摘要会立即失效，之后只会根据剩余消息重新生成。未选择的消息、人格、日记、今日状态和独立长期记忆不会删除。',
      confirmText: '清理消息',
      danger: true,
    })
    if (!confirmed) return false
    deps.errorMessage.value = ''
    try {
      await chatApi.deleteConversationMessages(conversation.id, normalizedIds)
      const [updatedMessages, updatedUsage] = await Promise.all([
        chatApi.loadMessages(conversation.id),
        chatApi.loadContextUsage(conversation.id),
      ])
      if (deps.selectedConversationId.value === conversation.id) {
        deps.messages.value = updatedMessages
        deps.contextUsage.value = updatedUsage
        await deps.settleChatScrollToBottom()
      }
      await refreshConversations()
      return true
    } catch (error) {
      deps.errorMessage.value = `清理 QQ 消息失败：${error.message}`
      return false
    }
  }
  return { historyLoading, historyHasMore, loadOlderMessages, refreshMessages, requestStartupGreeting, createClientRequestId, openAgentHandoff, sendMessage, requestChatCancellation, cancelActiveChat, refreshConversations, selectConversation, createNewConversation, renameConversation, deleteConversation, deleteConversationMessages }
}
