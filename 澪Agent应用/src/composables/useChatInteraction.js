/** ChatInteraction operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import { apiRequest } from '../services/api.js'
import { buildTurnVoicePayload } from '../chatVoice.js'
import { nextTick } from 'vue'
import { normalizeLoadedChatSettings } from '../chatSettingsState.js'
import * as voiceApi from '../services/voiceApi.js'
import { voiceLanguageLabel } from '../chatVoice.js'

export function useChatInteraction(deps) {
  async function copyTurn(turn) {
    const content = turn.parts.map((part) => deps.cleanDisplayContent(part.content)).filter(Boolean).join('\n')
    if (!content) return
    try {
      await navigator.clipboard.writeText(content)
    } catch {
      const helper = document.createElement('textarea')
      helper.value = content
      helper.style.position = 'fixed'
      helper.style.opacity = '0'
      document.body.appendChild(helper)
      helper.select()
      document.execCommand('copy')
      helper.remove()
    }
    deps.copiedTurnId.value = deps.turnId(turn)
    window.setTimeout(() => {
      if (deps.copiedTurnId.value === deps.turnId(turn)) deps.copiedTurnId.value = ''
    }, 1600)
  }
  function stopMessageVoice() {
    deps.voiceAbortController?.abort()
    deps.voiceAbortController = null
    if (deps.activeMessageAudio) {
      deps.activeMessageAudio.pause()
      deps.activeMessageAudio.src = ''
      deps.activeMessageAudio = null
    }
    if (deps.activeMessageAudioUrl) {
      URL.revokeObjectURL(deps.activeMessageAudioUrl)
      deps.activeMessageAudioUrl = ''
    }
    deps.speakingPartId.value = ''
    deps.voiceLoadingPartId.value = ''
  }
  async function playMessageVoice(turn) {
    const playbackId = deps.turnId(turn)
    if (deps.speakingPartId.value === playbackId || deps.voiceLoadingPartId.value === playbackId) {
      stopMessageVoice()
      return
    }
    const payload = buildTurnVoicePayload(
      turn,
      deps.cleanDisplayContent,
      deps.chatVoiceLanguage.value,
    )
    if (!payload.text) return
    stopMessageVoice()
    deps.errorMessage.value = ''
    deps.voiceLoadingPartId.value = playbackId
    deps.voiceAbortController = new AbortController()
    try {
      const audioBlob = await apiRequest('/api/companion/voice/audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: deps.voiceAbortController.signal,
        deadlineClass: 'media',
        responseType: 'blob',
      })
      deps.activeMessageAudioUrl = URL.createObjectURL(audioBlob)
      deps.activeMessageAudio = new Audio(deps.activeMessageAudioUrl)
      deps.activeMessageAudio.addEventListener('ended', stopMessageVoice, { once: true })
      deps.activeMessageAudio.addEventListener('error', stopMessageVoice, { once: true })
      deps.voiceLoadingPartId.value = ''
      deps.speakingPartId.value = playbackId
      await deps.activeMessageAudio.play()
    } catch (error) {
      if (error?.code !== 'request_cancelled') deps.errorMessage.value = `语音播放失败：${error.message}`
      stopMessageVoice()
    }
  }
  function turnVoiceLanguageLabel(turn) {
    const payload = buildTurnVoicePayload(
      turn,
      deps.cleanDisplayContent,
      deps.chatVoiceLanguage.value,
    )
    return voiceLanguageLabel(payload.language)
  }
  function handleComposerKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      deps.sendMessage()
    }
  }
  async function scrollToBottom() {
    await nextTick()
    if (deps.chatScroll.value) deps.chatScroll.value.scrollTop = deps.chatScroll.value.scrollHeight
  }
  async function settleChatScrollToBottom() {
    await nextTick()
    await new Promise((resolve) => {
      window.requestAnimationFrame(() => window.requestAnimationFrame(resolve))
    })
    await scrollToBottom()
    window.setTimeout(scrollToBottom, 100)
    window.setTimeout(scrollToBottom, 300)
  }
  function persistReasoning() {
    localStorage.setItem(deps.reasoningStorageKey, deps.reasoningLevel.value)
  }
  function persistSelectedModel() {
    localStorage.setItem(deps.modelStorageKey, deps.selectedModel.value)
  }
  function toggleModelMenu() {
    deps.showModelMenu.value = !deps.showModelMenu.value
    deps.modelMenuSection.value = 'root'
  }
  function closeModelMenu() {
    deps.showModelMenu.value = false
    deps.modelMenuSection.value = 'root'
  }
  async function loadSharedChatSettings() {
    try {
      const saved = await voiceApi.loadVoiceSettings()
      const loaded = normalizeLoadedChatSettings(
        saved,
        deps.modelOptions.value.map((item) => item.id),
      )
      // 启动时以后端保存值为准；localStorage 只作为后端暂时不可读时的兜底。
      if (!deps.isAgentWorkspace) {
        deps.selectedModel.value = loaded.model_id
        deps.reasoningLevel.value = loaded.reasoning_level
      }
      deps.chatVoiceLanguage.value = loaded.voice_language
      deps.ensureReasoningForActiveModel()
      persistSelectedModel()
      persistReasoning()
      deps.chatSettingsDraft.value = {
        model_id: deps.selectedModel.value,
        reasoning_level: deps.reasoningLevel.value,
        voice_language: deps.chatVoiceLanguage.value,
      }
      deps.savedChatSettings.value = deps.serializeSettings(deps.chatSettingsDraft.value)
      if (deps.companionStatus.value.pet?.settings) {
        deps.companionStatus.value.pet.settings.gpt_sovits_text_language = deps.chatVoiceLanguage.value
      }
    } catch {
      // 旧后端没有读取接口时，继续使用跟随原文。
    }
  }
  async function syncSharedChatSettings(voiceLanguage = deps.chatVoiceLanguage.value) {
    try {
      const saved = await deps.request('/api/companion/chat-settings', {
        method: 'PATCH',
        body: JSON.stringify({
          model_id: deps.selectedModel.value,
          reasoning_level: deps.reasoningLevel.value,
          voice_language: voiceLanguage || 'auto',
        }),
      })
      deps.chatVoiceLanguage.value = saved.voice_language || 'auto'
      if (saved.voice_language && deps.companionStatus.value.pet?.settings) {
        deps.companionStatus.value.pet.settings.gpt_sovits_text_language = saved.voice_language
      }
      return saved
    } catch (error) {
      deps.errorMessage.value = `QQ设置同步失败：${error.message}`
      return null
    }
  }
  function chooseModel(modelId) {
    deps.selectedModel.value = modelId
    persistSelectedModel()
    deps.ensureReasoningForActiveModel()
    persistReasoning()
    if (!deps.isAgentWorkspace) void syncSharedChatSettings()
    deps.modelMenuSection.value = 'root'
  }
  function chooseReasoning(level) {
    deps.reasoningLevel.value = level
    persistReasoning()
    if (!deps.isAgentWorkspace) void syncSharedChatSettings()
    deps.modelMenuSection.value = 'root'
  }
  function handleDocumentPointerDown(event) {
    if (deps.showModelMenu.value && !deps.modelPicker.value?.contains(event.target)) closeModelMenu()
  }
  function handleDocumentKeydown(event) {
    if (event.key === 'Escape') closeModelMenu()
  }
  return { copyTurn, stopMessageVoice, playMessageVoice, turnVoiceLanguageLabel, handleComposerKeydown, scrollToBottom, settleChatScrollToBottom, persistReasoning, persistSelectedModel, toggleModelMenu, closeModelMenu, loadSharedChatSettings, syncSharedChatSettings, chooseModel, chooseReasoning, handleDocumentPointerDown, handleDocumentKeydown }
}
