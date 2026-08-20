import { apiRequest } from './api.js'

export const loadVoiceSettings = () => apiRequest('/api/companion/chat-settings')
export const saveVoiceSettings = (payload) => apiRequest('/api/companion/chat-settings', { method: 'PATCH', body: JSON.stringify(payload) })
export const testVoice = () => apiRequest('/api/companion/voice/test', { method: 'POST', body: '{}' })
export const controlVoice = (action) => apiRequest(`/api/companion/voice/runtime/${encodeURIComponent(action)}`, { method: 'POST', body: '{}' })
