/** PetCall operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import { bargeInThreshold } from '../petCallAudio.js'
import { bytesToBase64 } from '../petCallAudio.js'
import { encodePcmWav } from '../petCallAudio.js'

export function usePetCall(deps) {
  function handlePetChatKeydown(event) {
    if (event.key === 'Escape') {
      event.preventDefault()
      if (window.mioPetChat?.hide) window.mioPetChat.hide()
      else window.pywebview?.api?.hide_pet_chat_window?.()
      return
    }
    if (event.key === 'Backspace' && !deps.petChatDraft.value && deps.petChatImages.value.length) {
      deps.petChatImages.value = deps.petChatImages.value.slice(0, -1)
      return
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      sendPetChat()
    }
  }
  function beginPetChatWindowDrag(event) {
    if (event.button !== 0) return
    if (window.mioPetChat?.isElectron) return
    event.preventDefault()
    window.pywebview?.api?.window_drag?.()
  }
  function resetPetCallCapture() {
    deps.petCallFrames = []
    deps.petCallSpeechStartedAt = 0
    deps.petCallSpeechCandidateStartedAt = 0
    deps.petCallLastVoiceAt = 0
    deps.petCallInterruptSent = false
    deps.petCallBargeInStartedAt = 0
  }
  async function submitPetCallTurn() {
    if (!deps.petCallFrames.length || deps.petCallTurnPending || !deps.petCallAudioContext || !deps.petCallSessionId) return
    const callSessionId = deps.petCallSessionId
    const turnId = deps.petCallNextTurnId
    const frameCount = deps.petCallFrames.reduce((total, frame) => total + frame.length, 0)
    const samples = new Float32Array(frameCount)
    let offset = 0
    for (const frame of deps.petCallFrames) {
      samples.set(frame, offset)
      offset += frame.length
    }
    resetPetCallCapture()
    deps.petCallTurnPending = true
    deps.petCallState.value = 'thinking'
    try {
      const result = await deps.request('/api/companion/call/turn', {
        method: 'POST',
        body: JSON.stringify({
          wav_base64: bytesToBase64(encodePcmWav(samples, deps.petCallAudioContext.sampleRate)),
          language: deps.petCallSettings.value.pet_call_input_language || 'zh',
          call_session_id: callSessionId,
          turn_id: turnId,
        }),
      })
      if (!deps.petCallActive.value || deps.petCallSessionId !== callSessionId) return
      deps.petCallNextTurnId = Math.max(deps.petCallNextTurnId, Number(result.turn_id || turnId) + 1)
      if (result.heard) {
        deps.petCallResponseId = String(result.response_id || '')
        deps.petCallAwaitingVoiceSince = performance.now()
        deps.petCallState.value = 'waiting_voice'
      } else {
        deps.petCallResponseId = ''
        deps.petCallAwaitingVoiceSince = 0
        deps.petCallState.value = 'listening'
      }
    } catch (error) {
      if (!deps.petCallActive.value || deps.petCallSessionId !== callSessionId) return
      deps.petCallError.value = error.message
      deps.petCallState.value = 'error'
    } finally {
      if (deps.petCallSessionId === callSessionId) deps.petCallTurnPending = false
    }
  }
  function processPetCallAudio(event) {
    if (!deps.petCallActive.value) return
    const frame = new Float32Array(event.inputBuffer.getChannelData(0))
    let energy = 0
    for (const sample of frame) energy += sample * sample
    const rms = Math.sqrt(energy / Math.max(1, frame.length))
    const now = performance.now()
    const settings = deps.petCallSettings.value
    const voiced = rms >= Number(settings.pet_call_voice_threshold || 0.018)
    const bargeInVoiced = rms >= bargeInThreshold(settings.pet_call_voice_threshold)
    const frameMs = frame.length / deps.petCallAudioContext.sampleRate * 1000
    const maxPreRollFrames = Math.max(2, Math.ceil(240 / frameMs))

    if (!deps.petCallSpeechStartedAt) {
      if (['thinking', 'waiting_voice', 'connecting'].includes(deps.petCallState.value)) {
        deps.petCallPreRoll = []
        deps.petCallSpeechCandidateStartedAt = 0
        deps.petCallBargeInStartedAt = 0
        return
      }
      if (deps.petCallState.value === 'speaking') {
        if (!bargeInVoiced) {
          deps.petCallPreRoll = []
          deps.petCallBargeInStartedAt = 0
          return
        }
        deps.petCallPreRoll.push(frame)
        if (deps.petCallPreRoll.length > maxPreRollFrames) deps.petCallPreRoll.shift()
        deps.petCallBargeInStartedAt ||= now
        if (now - deps.petCallBargeInStartedAt < 360) return
      } else if (!voiced || deps.petCallTurnPending) {
        deps.petCallPreRoll.push(frame)
        if (deps.petCallPreRoll.length > maxPreRollFrames) deps.petCallPreRoll.shift()
        deps.petCallSpeechCandidateStartedAt = 0
        deps.petCallBargeInStartedAt = 0
        return
      } else {
        deps.petCallPreRoll.push(frame)
        if (deps.petCallPreRoll.length > maxPreRollFrames) deps.petCallPreRoll.shift()
        deps.petCallSpeechCandidateStartedAt ||= now
        if (now - deps.petCallSpeechCandidateStartedAt < 160) return
      }
      deps.petCallSpeechStartedAt = now
      deps.petCallLastVoiceAt = now
      deps.petCallFrames = [...deps.petCallPreRoll]
      deps.petCallPreRoll = []
      if (deps.petCallState.value === 'speaking' && !deps.petCallInterruptSent) {
        deps.petCallInterruptSent = true
        void deps.request('/api/companion/call/interrupt', {
          method: 'POST',
          body: JSON.stringify({ call_session_id: deps.petCallSessionId, response_id: deps.petCallResponseId }),
        }).catch(() => {})
        deps.petCallState.value = 'listening'
      }
    }
    deps.petCallFrames.push(frame)
    if (voiced) deps.petCallLastVoiceAt = now
    const speechMs = now - deps.petCallSpeechStartedAt
    const silenceMs = now - deps.petCallLastVoiceAt
    const reachedSilence = silenceMs >= Number(settings.pet_call_silence_ms || 650)
    const reachedMaximum = speechMs >= Number(settings.pet_call_max_turn_seconds || 18) * 1000
    if ((reachedSilence && speechMs >= Number(settings.pet_call_min_speech_ms || 280)) || reachedMaximum) {
      void submitPetCallTurn()
    }
  }
  async function startPetCall() {
    if (deps.petCallActive.value) return
    deps.petCallError.value = ''
    deps.petCallState.value = 'connecting'
    try {
      const result = await deps.request('/api/companion/call/start', { method: 'POST' })
      deps.petCallSessionId = String(result.call_session_id || '')
      deps.petCallNextTurnId = Number(result.next_turn_id || 1)
      deps.petCallResponseId = ''
      deps.petCallAwaitingVoiceSince = 0
      if (!deps.petCallSessionId) throw new Error('电话服务没有返回会话 ID')
      deps.petCallSettings.value = { ...deps.petCallSettings.value, ...(result.settings || {}) }
      deps.petCallStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
          sampleRate: { ideal: 16000 },
        },
      })
      deps.petCallAudioContext = new AudioContext({ latencyHint: 'interactive' })
      await deps.petCallAudioContext.resume()
      deps.petCallSource = deps.petCallAudioContext.createMediaStreamSource(deps.petCallStream)
      deps.petCallProcessor = deps.petCallAudioContext.createScriptProcessor(2048, 1, 1)
      deps.petCallProcessor.onaudioprocess = processPetCallAudio
      deps.petCallSource.connect(deps.petCallProcessor)
      deps.petCallProcessor.connect(deps.petCallAudioContext.destination)
      const microphoneTrack = deps.petCallStream.getAudioTracks()[0]
      const microphoneSettings = microphoneTrack?.getSettings?.() || {}
      void deps.request('/api/companion/call/device', {
        method: 'POST',
        body: JSON.stringify({
          call_session_id: deps.petCallSessionId,
          device_id: String(microphoneSettings.deviceId || ''),
          label: String(microphoneTrack?.label || ''),
          sample_rate: Number(microphoneSettings.sampleRate || deps.petCallAudioContext.sampleRate || 0),
          channel_count: Number(microphoneSettings.channelCount || 1),
          echo_cancellation: typeof microphoneSettings.echoCancellation === 'boolean' ? microphoneSettings.echoCancellation : null,
          noise_suppression: typeof microphoneSettings.noiseSuppression === 'boolean' ? microphoneSettings.noiseSuppression : null,
          auto_gain_control: typeof microphoneSettings.autoGainControl === 'boolean' ? microphoneSettings.autoGainControl : null,
        }),
      }).catch(() => {})
      resetPetCallCapture()
      deps.petCallActive.value = true
      deps.petCallState.value = 'listening'
      deps.petCallMonitorTimer = window.setInterval(async () => {
        if (!deps.petCallActive.value || !deps.petCallSessionId) return
        try {
          const status = await deps.request('/api/companion/call/status')
          if (String(status.call_session_id || '') !== deps.petCallSessionId) return
          if (!status.active) {
            await stopPetCall({ notifyServer: false, preserveError: true })
            return
          }
          const startedId = String(status.voice_started?.response_id || '')
          const endedId = String(status.voice_ended?.response_id || '')
          if (deps.petCallResponseId && startedId === deps.petCallResponseId && endedId !== deps.petCallResponseId) {
            deps.petCallState.value = 'speaking'
            deps.petCallAwaitingVoiceSince = 0
          } else if (deps.petCallResponseId && endedId === deps.petCallResponseId) {
            if (String(status.voice_ended?.reason || '') === 'error') {
              deps.petCallError.value = 'Mio 的声音播放失败'
              deps.petCallState.value = 'error'
            } else {
              deps.petCallState.value = 'listening'
            }
            deps.petCallResponseId = ''
            deps.petCallAwaitingVoiceSince = 0
          } else if (
            deps.petCallState.value === 'waiting_voice'
            && deps.petCallAwaitingVoiceSince
            && performance.now() - deps.petCallAwaitingVoiceSince > 30000
          ) {
            deps.petCallError.value = 'Mio 已经生成回复，但 30 秒内没有收到真实首音'
            deps.petCallState.value = 'error'
          }
        } catch (_) {}
      }, 250)
    } catch (error) {
      deps.petCallError.value = error.message
      deps.petCallState.value = 'error'
      await stopPetCall({ notifyServer: true, preserveError: true })
    }
  }
  async function stopPetCall({ notifyServer = true, preserveError = false } = {}) {
    const wasActive = deps.petCallActive.value || deps.petCallState.value === 'connecting'
    const callSessionId = deps.petCallSessionId
    const responseId = deps.petCallResponseId
    deps.petCallActive.value = false
    deps.petCallSessionId = ''
    deps.petCallResponseId = ''
    deps.petCallAwaitingVoiceSince = 0
    resetPetCallCapture()
    if (deps.petCallMonitorTimer) window.clearInterval(deps.petCallMonitorTimer)
    deps.petCallMonitorTimer = null
    if (deps.petCallProcessor) {
      deps.petCallProcessor.onaudioprocess = null
      try { deps.petCallProcessor.disconnect() } catch (_) {}
    }
    if (deps.petCallSource) {
      try { deps.petCallSource.disconnect() } catch (_) {}
    }
    for (const track of deps.petCallStream?.getTracks?.() || []) track.stop()
    if (deps.petCallAudioContext) await deps.petCallAudioContext.close().catch(() => {})
    deps.petCallProcessor = null
    deps.petCallSource = null
    deps.petCallStream = null
    deps.petCallAudioContext = null
    deps.petCallTurnPending = false
    if (!preserveError) deps.petCallState.value = 'idle'
    if (notifyServer && wasActive && callSessionId) {
      await deps.request('/api/companion/call/stop', {
        method: 'POST',
        body: JSON.stringify({ call_session_id: callSessionId, response_id: responseId }),
      }).catch(() => {})
    }
  }
  async function togglePetCall() {
    if (deps.petCallActive.value) await stopPetCall()
    else await startPetCall()
  }
  async function sendPetChat() {
    const content = deps.petChatDraft.value.trim()
    if ((!content && !deps.petChatImages.value.length) || deps.petChatSending.value) return
    const images = deps.petChatImages.value.map(({ name, data_url }) => ({ name, data_url }))
    const clientRequestId = deps.createClientRequestId()
    deps.petChatDraft.value = ''
    deps.petChatImages.value = []
    deps.petChatSending.value = true
    deps.errorMessage.value = ''
    try {
      const result = await deps.request('/api/companion/chat', {
        method: 'POST',
        body: JSON.stringify({ message: content, images, client_request_id: clientRequestId }),
      })
      if (result.voice_attempted && !result.spoken && !result.voice_delegated) {
        deps.errorMessage.value = `桌宠已经回复，但语音没有播放：${result.voice_error || '未知原因'}`
      }
    } catch (error) {
      deps.petChatDraft.value = content
      deps.petChatImages.value = images.map((item, index) => ({ ...item, id: `retry-${Date.now()}-${index}` }))
      deps.errorMessage.value = `桌宠 Mio 暂时没有回复：${error.message}`
    } finally {
      deps.petChatSending.value = false
    }
  }
  async function handlePetChatPaste(event) {
    const files = [...(event.clipboardData?.items || [])]
      .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter(Boolean)
    if (!files.length) return
    event.preventDefault()
    try {
      for (const file of files.slice(0, Math.max(0, 5 - deps.petChatImages.value.length))) {
        if (file.size > deps.attachmentLimits.value.imageMaxBytes) {
          throw new Error(`图片超过 ${(deps.attachmentLimits.value.imageMaxBytes / 1024 / 1024).toFixed(0)}MB`)
        }
        deps.petChatImages.value.push({
          id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}`,
          name: file.name || '粘贴的图片.png',
          data_url: await deps.readFileAsDataUrl(file),
        })
      }
    } catch (error) {
      deps.errorMessage.value = error.message
    }
  }
  return { handlePetChatKeydown, beginPetChatWindowDrag, resetPetCallCapture, submitPetCallTurn, processPetCallAudio, startPetCall, stopPetCall, togglePetCall, sendPetChat, handlePetChatPaste }
}
