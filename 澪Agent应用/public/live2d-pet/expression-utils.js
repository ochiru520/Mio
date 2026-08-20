(function (root) {
  'use strict'

  const emotionPatterns = {
    gentle: [/gentle|soft|smile|normal|温柔|微笑/i, /happy|joy|开心|爱心/i],
    cheerful: [/happy|joy|smile|cheer|laugh|开心|星星|爱心/i],
    concerned: [/worry|concern|sad|trouble|担心|难过|泪/i],
    serious: [/serious|angry|focus|stern|认真|生气/i],
    shy: [/shy|blush|embarrass|害羞/i, /smile|微笑/i],
    neutral: [/neutral|normal|idle|普通|默认/i],
  }

  function expressionNames(entries) {
    return (Array.isArray(entries) ? entries : [])
      .map((entry) => String(entry?.name || entry?.Name || entry || ''))
      .filter(Boolean)
  }

  function selectExpression(entries, emotion, configured = '') {
    const names = expressionNames(entries)
    if (configured && names.includes(String(configured))) return String(configured)
    const patterns = emotionPatterns[emotion] || []
    return names.find((name) => patterns.some((pattern) => pattern.test(name))) || ''
  }

  root.MioLive2DExpression = { expressionNames, selectExpression }
})(globalThis)
