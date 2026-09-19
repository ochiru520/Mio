/** Memory operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import * as diaryApi from '../services/diaryApi.js'
import * as memoryApi from '../services/memoryApi.js'

export function useMemory(deps) {
  async function loadMemoryHub({ quiet = false } = {}) {
    if (!quiet) deps.memoryLoading.value = true
    try {
      const [memory, reviews, weekly, monthly] = await Promise.all([
        memoryApi.loadMemory(),
        diaryApi.listDailyReviews(),
        diaryApi.listWeeklyReviews(),
        diaryApi.listMonthlyReviews(),
      ])
      deps.memoryData.value = memory
      deps.runtimeSummaryDraft.value = memory.runtime_summary?.content || ''
      deps.dailyReviews.value = reviews
      deps.weeklyReviews.value = weekly
      deps.monthlyReviews.value = monthly
      if (!deps.selectedDailyDate.value) deps.selectedDailyDate.value = deps.dailyReviewItems.value[0]?.date || deps.logicalDate.value
      if (!deps.selectedWeeklyStart.value) deps.selectedWeeklyStart.value = deps.weeklyReviewItems.value[0]?.week_start || deps.weekStartFor(deps.logicalDate.value)
      if (!deps.selectedMonthlyMonth.value) deps.selectedMonthlyMonth.value = deps.monthlyReviewItems.value[0]?.month || deps.logicalDate.value.slice(0, 7)
      deps.memoryLoaded.value = true
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryLoading.value = false
    }
  }
  async function resolveThread(threadId) {
    try {
      await deps.request(`/api/threads/${threadId}/resolve`, { method: 'POST', body: '{}' })
      deps.memoryData.value.threads = deps.memoryData.value.threads.filter((item) => item.id !== threadId)
    } catch (error) {
      deps.errorMessage.value = error.message
    }
  }
  async function recordFollowUpResult(thread, payload) {
    if (!thread?.id || deps.memoryBusy.value) return false
    deps.memoryBusy.value = `thread-result-${thread.id}`
    try {
      await deps.request(`/api/threads/${thread.id}/result`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      await loadMemoryHub({ quiet: true })
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      return false
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function saveRuntimeSummary() {
    if (!deps.runtimeSummaryDraft.value.trim() || deps.memoryBusy.value) return
    deps.memoryBusy.value = 'runtime'
    try {
      await deps.request('/api/memory/runtime-summary', {
        method: 'PUT',
        body: JSON.stringify({ content: deps.runtimeSummaryDraft.value }),
      })
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  function memoryLayerLabel(layer) {
    return {
      L0: '核心记忆',
      L1: '近期状态',
      L2: '长期经历',
    }[layer] || layer
  }
  function memoryCategoryLabel(category) {
    return {
      identity: '身份事实',
      preference: '稳定偏好',
      relationship: '相处边界',
      current_state: '当前状态',
      plan: '计划',
      project: '项目',
      experience: '经历',
      person: '人物',
      other: '其他',
    }[category] || category
  }
  async function addStructuredMemory() {
    if (!deps.newStructuredMemory.value.content.trim() || deps.memoryBusy.value) return
    deps.memoryBusy.value = 'structured-new'
    try {
      await deps.request('/api/memory/items', {
        method: 'POST',
        body: JSON.stringify({
          ...deps.newStructuredMemory.value,
          confidence: 1,
          conversation_id: deps.selectedConversationId.value || 'default',
        }),
      })
      deps.newStructuredMemory.value = { layer: 'L0', category: 'preference', memory_key: '', content: '' }
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function archiveStructuredMemory(memory) {
    if (deps.memoryBusy.value) return
    const confirmed = await deps.showAppConfirm({ title: '停用这条记忆？', message: '历史证据仍会保留。', confirmText: '停用记忆' })
    if (!confirmed) return
    deps.memoryBusy.value = `structured-${memory.id}`
    try {
      await deps.request(`/api/memory/items/${memory.id}`, { method: 'DELETE' })
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function restoreStructuredMemory(memory) {
    if (deps.memoryBusy.value) return false
    const confirmed = await deps.showAppConfirm({
      title: '恢复这个记忆版本？',
      message: '当前同类记忆会保留在历史中，这个版本会重新用于后续对话。',
      confirmText: '恢复版本',
    })
    if (!confirmed) return false
    deps.memoryBusy.value = `restore-${memory.id}`
    try {
      await deps.request(`/api/memory/items/${memory.id}/restore`, { method: 'POST', body: '{}' })
      await loadMemoryHub({ quiet: true })
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      return false
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function confirmMemoryCandidate(memory) {
    if (deps.memoryBusy.value) return
    deps.memoryBusy.value = `candidate-${memory.id}`
    try {
      await deps.request(`/api/memory/items/${memory.id}/confirm`, { method: 'POST', body: '{}' })
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function rejectMemoryCandidate(memory) {
    if (deps.memoryBusy.value) return
    deps.memoryBusy.value = `candidate-${memory.id}`
    try {
      await deps.request(`/api/memory/items/${memory.id}/reject`, { method: 'POST', body: '{}' })
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function editStructuredMemory(memory) {
    const content = await deps.showAppPrompt({
      title: '修改记忆内容',
      message: '修改后会更新 Mio 在后续对话中使用的内容',
      value: memory.content || '',
      multiline: true,
    })
    if (content === null || !content.trim() || content.trim() === memory.content || deps.memoryBusy.value) return
    deps.memoryBusy.value = `structured-${memory.id}`
    try {
      await deps.request(`/api/memory/items/${memory.id}`, {
        method: 'PUT',
        body: JSON.stringify({ content: content.trim(), expected_content: memory.content }),
      })
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function addMemoryThread() {
    if (!deps.newThreadContent.value.trim() || deps.memoryBusy.value) return
    deps.memoryBusy.value = 'thread-new'
    try {
      await deps.request('/api/threads', {
        method: 'POST',
        body: JSON.stringify({
          content: deps.newThreadContent.value,
          conversation_id: deps.selectedConversationId.value || 'default',
          follow_up_after: deps.newThreadFollowUp.value,
        }),
      })
      deps.newThreadContent.value = ''
      deps.newThreadFollowUp.value = ''
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function saveMemoryThread(thread) {
    if (!thread.content?.trim() || deps.memoryBusy.value) return
    deps.memoryBusy.value = `thread-${thread.id}`
    try {
      await deps.request(`/api/threads/${thread.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          content: thread.content,
          conversation_id: thread.conversation_id,
          follow_up_after: thread.follow_up_after || '',
        }),
      })
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function deleteMemoryThread(threadId) {
    if (deps.memoryBusy.value) return
    const confirmed = await deps.showAppConfirm({ title: '删除待跟进记忆？', message: '删除后不会再根据这条内容主动跟进。', confirmText: '删除', danger: true })
    if (!confirmed) return
    deps.memoryBusy.value = `thread-${threadId}`
    try {
      await deps.request(`/api/threads/${threadId}`, { method: 'DELETE' })
      deps.memoryData.value.threads = deps.memoryData.value.threads.filter((item) => item.id !== threadId)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function saveConversationSummary(summary) {
    if (!summary.content?.trim() || deps.memoryBusy.value) return
    deps.memoryBusy.value = `summary-${summary.conversation_id}`
    try {
      await deps.request('/api/memory/conversation-summary', {
        method: 'PUT',
        body: JSON.stringify({ conversation_id: summary.conversation_id, content: summary.content }),
      })
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function addConversationSummary() {
    if (!deps.newConversationSummary.value.trim() || deps.memoryBusy.value) return
    const conversationId = deps.selectedConversationId.value || 'default'
    deps.memoryBusy.value = 'summary-new'
    try {
      await deps.request('/api/memory/conversation-summary', {
        method: 'PUT',
        body: JSON.stringify({ conversation_id: conversationId, content: deps.newConversationSummary.value }),
      })
      deps.newConversationSummary.value = ''
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function deleteConversationSummary(summary) {
    if (deps.memoryBusy.value) return
    const confirmed = await deps.showAppConfirm({ title: '删除这份长期印象？', message: '之后会在上下文再次压缩时重新生成。', confirmText: '删除', danger: true })
    if (!confirmed) return
    deps.memoryBusy.value = `summary-${summary.conversation_id}`
    try {
      await deps.request(`/api/memory/conversation-summary/${encodeURIComponent(summary.conversation_id)}`, { method: 'DELETE' })
      deps.memoryData.value.summaries = deps.memoryData.value.summaries.filter((item) => item.conversation_id !== summary.conversation_id)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function addProfileNote() {
    if (!deps.newProfileNote.value.trim() || deps.memoryBusy.value) return
    deps.memoryBusy.value = 'note-new'
    try {
      const result = await deps.request('/api/memory/profile-notes', {
        method: 'POST',
        body: JSON.stringify({ content: deps.newProfileNote.value }),
      })
      deps.memoryData.value.profile = result.profile
      deps.newProfileNote.value = ''
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function saveProfileNote(index, note) {
    if (!String(note || '').trim() || deps.memoryBusy.value) return
    deps.memoryBusy.value = `note-${index}`
    try {
      const result = await deps.request(`/api/memory/profile-notes/${index}`, {
        method: 'PUT',
        body: JSON.stringify({ content: note }),
      })
      deps.memoryData.value.profile = result.profile
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function deleteProfileNote(index) {
    if (deps.memoryBusy.value) return
    const confirmed = await deps.showAppConfirm({ title: '删除这条 Mio 属性记忆？', message: '删除后，Mio 不会再把它作为固定属性使用。', confirmText: '删除', danger: true })
    if (!confirmed) return
    deps.memoryBusy.value = `note-${index}`
    try {
      const result = await deps.request(`/api/memory/profile-notes/${index}`, { method: 'DELETE' })
      deps.memoryData.value.profile = result.profile
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  return { loadMemoryHub, resolveThread, recordFollowUpResult, saveRuntimeSummary, memoryLayerLabel, memoryCategoryLabel, addStructuredMemory, archiveStructuredMemory, restoreStructuredMemory, confirmMemoryCandidate, rejectMemoryCandidate, editStructuredMemory, addMemoryThread, saveMemoryThread, deleteMemoryThread, saveConversationSummary, addConversationSummary, deleteConversationSummary, addProfileNote, saveProfileNote, deleteProfileNote }
}
