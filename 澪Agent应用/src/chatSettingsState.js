const allowedVoiceLanguages = new Set(['auto', 'zh', 'ja'])

export function normalizeLoadedChatSettings(saved = {}, availableModelIds = []) {
  const knownModels = new Set(['auto', ...availableModelIds.filter(Boolean)])
  const requestedModel = String(saved.model_id || 'auto').trim() || 'auto'
  const requestedReasoning = String(saved.reasoning_level || 'auto').trim() || 'auto'
  const requestedVoice = String(saved.voice_language || 'auto').trim() || 'auto'
  return {
    model_id: knownModels.has(requestedModel) ? requestedModel : 'auto',
    reasoning_level: requestedReasoning,
    voice_language: allowedVoiceLanguages.has(requestedVoice) ? requestedVoice : 'auto',
  }
}
