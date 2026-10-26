/** Memory operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import * as diaryApi from '../services/diaryApi.js'
import * as memoryApi from '../services/memoryApi.js'

export function useMemory(deps) {
  let loadVersion = 0
  async function loadMemoryHub({ quiet = false } = {}) {
    const version = ++loadVersion
    deps.memoryLoading.value = true
    deps.memoryError.value = ''
    try {
      const [memory, reviews, weekly, monthly] = await Promise.all([
        memoryApi.loadMemory(),
        diaryApi.listDailyReviews(),
        diaryApi.listWeeklyReviews(),
        diaryApi.listMonthlyReviews(),
      ])
      if (version !== loadVersion) return
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
      if (version === loadVersion) deps.memoryError.value = error.message
    } finally {
      if (version === loadVersion) deps.memoryLoading.value = false
    }
  }
  async function resolveThread(threadId) {
    if (deps.memoryBusy.value) return
    deps.memoryBusy.value = `thread-${threadId}`
    try {
      await memoryApi.submitApiThreadResolve(threadId, {}, deps.request)
      deps.memoryData.value.threads = deps.memoryData.value.threads.filter((item) => item.id !== threadId)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function recordFollowUpResult(thread, payload) {
    if (!thread?.id || deps.memoryBusy.value) return false
    deps.memoryBusy.value = `thread-result-${thread.id}`
    try {
      await memoryApi.submitApiThreadResult(thread.id, payload, {}, deps.request)
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
      await memoryApi.saveRuntimeSummaryByPayload({ content: deps.runtimeSummaryDraft.value }, {}, deps.request)
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
      await memoryApi.submitItems({
          ...deps.newStructuredMemory.value,
          confidence: 1,
          conversation_id: deps.selectedConversationId.value || 'default',
        }, {}, deps.request)
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
      await memoryApi.deleteItem(memory.id, {}, deps.request)
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
      await memoryApi.submitItemRestore(memory.id, {}, deps.request)
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
      await memoryApi.submitItemConfirm(memory.id, {}, deps.request)
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function setMemoryAvailability(memory, action) {
    if (deps.memoryBusy.value) return
    deps.memoryBusy.value = `${action}-${memory.id}`
    try {
      await memoryApi.submitItem(memory.id, action, {}, deps.request)
      await loadMemoryHub({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  const sleepStructuredMemory = memory => setMemoryAvailability(memory, 'sleep')
  const wakeStructuredMemory = memory => setMemoryAvailability(memory, 'wake')
  async function rejectMemoryCandidate(memory) {
    if (deps.memoryBusy.value) return
    deps.memoryBusy.value = `candidate-${memory.id}`
    try {
      await memoryApi.submitItemReject(memory.id, {}, deps.request)
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
      await memoryApi.saveItem(memory.id, { content: content.trim(), expected_content: memory.content }, {}, deps.request)
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
      await memoryApi.submitApiThreads({
          content: deps.newThreadContent.value,
          conversation_id: deps.selectedConversationId.value || 'default',
          follow_up_after: deps.newThreadFollowUp.value,
        }, {}, deps.request)
      deps.newThreadContent.value = ''
      deps.newThreadFollowUp.value = ''
      await loadMemoryHub({ quiet: true })
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      return false
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  async function saveMemoryThread(thread) {
    if (!thread.content?.trim() || deps.memoryBusy.value) return
    deps.memoryBusy.value = `thread-${thread.id}`
    try {
      await memoryApi.saveApiThread(thread.id, {
          content: thread.content,
          conversation_id: thread.conversation_id,
          follow_up_after: thread.follow_up_after || '',
        }, {}, deps.request)
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
      await memoryApi.deleteApiThread(threadId, {}, deps.request)
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
      await memoryApi.saveConversationSummary({ conversation_id: summary.conversation_id, content: summary.content }, {}, deps.request)
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
      await memoryApi.saveConversationSummary({ conversation_id: conversationId, content: deps.newConversationSummary.value }, {}, deps.request)
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
      await memoryApi.deleteConversationSummary(summary.conversation_id, {}, deps.request)
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
      const result = await memoryApi.submitProfileNotes({ content: deps.newProfileNote.value }, {}, deps.request)
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
      const result = await memoryApi.saveProfileNotes(index, { content: note }, {}, deps.request)
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
      const result = await memoryApi.deleteProfileNotes(index, {}, deps.request)
      deps.memoryData.value.profile = result.profile
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.memoryBusy.value = ''
    }
  }
  return { loadMemoryHub, resolveThread, recordFollowUpResult, saveRuntimeSummary, memoryLayerLabel, memoryCategoryLabel, addStructuredMemory, archiveStructuredMemory, restoreStructuredMemory, confirmMemoryCandidate, rejectMemoryCandidate, sleepStructuredMemory, wakeStructuredMemory, editStructuredMemory, addMemoryThread, saveMemoryThread, deleteMemoryThread, saveConversationSummary, addConversationSummary, deleteConversationSummary, addProfileNote, saveProfileNote, deleteProfileNote }
}
