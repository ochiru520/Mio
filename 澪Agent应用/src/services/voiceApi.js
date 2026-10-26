import { apiRequest } from './api.js'

export const realtimeVoiceUrl = (location = globalThis.location) => `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/realtime/voice`
export function openRealtimeVoiceSocket(location = globalThis.location, WebSocketCtor = globalThis.WebSocket, { urlOverride } = {}) {
  if (typeof WebSocketCtor !== 'function') throw new Error('WebSocket is unavailable')
  return new WebSocketCtor(urlOverride === undefined ? realtimeVoiceUrl(location) : urlOverride)
}

export const loadVoiceSettings = () => apiRequest('/api/companion/chat-settings')
export const saveVoiceSettings = (payload) => apiRequest('/api/companion/chat-settings', { method: 'PATCH', body: JSON.stringify(payload) })
export const testVoice = () => apiRequest('/api/companion/voice/test', { method: 'POST', body: '{}' })
export const controlVoice = (action) => apiRequest(`/api/companion/voice/runtime/${encodeURIComponent(action)}`, { method: 'POST', body: '{}' })
