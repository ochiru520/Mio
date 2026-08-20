export const MAX_VOICE_PROFILES = 20

export function createVoiceProfile(profiles, name, now = Date.now()) {
  const cleanName = String(name || '').trim().slice(0, 80)
  const current = profiles && typeof profiles === 'object' ? profiles : {}
  if (!cleanName || Object.keys(current).length >= MAX_VOICE_PROFILES) return null

  const baseId = `voice-${Number(now).toString(36)}`
  let id = baseId
  let suffix = 1
  while (current[id]) id = `${baseId}-${suffix++}`
  return {
    id,
    profile: {
      name: cleanName,
      engine: 'gpt_sovits',
      gpt_sovits_ref_audio: '',
      gpt_sovits_prompt_text: '',
      gpt_sovits_prompt_language: 'zh',
      gpt_sovits_text_language: 'auto',
      gpt_sovits_translate_to_japanese: false,
      gpt_sovits_gpt_weights: '',
      gpt_sovits_sovits_weights: '',
      use_emotion_references: true,
    },
  }
}

export function removeVoiceProfile(profiles, bindings, defaultId, selectedId) {
  const entries = Object.entries(profiles || {})
  if (!selectedId || entries.length <= 1 || !profiles?.[selectedId]) return null

  const remainingProfiles = Object.fromEntries(entries.filter(([id]) => id !== selectedId))
  const fallbackId = Object.keys(remainingProfiles)[0]
  return {
    profiles: remainingProfiles,
    bindings: Object.fromEntries(
      Object.entries(bindings || {}).filter(([, profileId]) => profileId !== selectedId),
    ),
    defaultId: defaultId === selectedId ? fallbackId : defaultId,
    selectedId: fallbackId,
  }
}
