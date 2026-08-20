const JAPANESE_KANA_RE = /[\u3040-\u30ff\u31f0-\u31ff]/g
const HAN_RE = /[\u3400-\u4dbf\u4e00-\u9fff]/g
const CHINESE_MARKER_RE = /[这那的了我你请说听给今晚今天明天可以现在]/g

export function voiceLanguageLabel(language = 'auto') {
  if (language === 'ja') return '日语语音'
  if (language === 'zh') return '中文语音'
  return '跟随原文'
}

export function detectTurnVoiceLanguage(text, preferredLanguage = 'auto') {
  if (preferredLanguage === 'zh' || preferredLanguage === 'ja') return preferredLanguage

  const content = String(text || '')
  const kanaCount = content.match(JAPANESE_KANA_RE)?.length || 0
  if (!kanaCount) return 'zh'

  const hanCount = content.match(HAN_RE)?.length || 0
  const chineseMarkerCount = content.match(CHINESE_MARKER_RE)?.length || 0
  if (hanCount && chineseMarkerCount >= 2) return 'zh'
  return 'ja'
}

export function turnVoiceModeLabel(text, mode = 'original') {
  const originalLanguage = detectTurnVoiceLanguage(text)
  const targetLanguage = mode === 'zh' || mode === 'ja' ? mode : originalLanguage
  const languageLabel = targetLanguage === 'ja' ? '日语' : '中文'
  return `${languageLabel}${targetLanguage === originalLanguage ? '原文' : '翻译'}`
}

export function buildTurnVoicePayload(
  turn,
  cleanContent = (value) => String(value || '').trim(),
  mode = 'original',
) {
  const text = (turn?.parts || [turn])
    .filter(Boolean)
    .map((part) => cleanContent(part.content))
    .filter(Boolean)
    .join('\n')

  return {
    text,
    model_id: turn?.model_id || '',
    language: detectTurnVoiceLanguage(text, mode),
  }
}
