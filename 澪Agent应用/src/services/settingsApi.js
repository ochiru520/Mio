import { apiRequest } from './api.js'

export const loadRuntimeSettings = () => apiRequest('/api/settings/runtime')
export const saveRuntimeSettings = (changes, { revision = '' } = {}) => apiRequest('/api/settings/runtime', {
  method: 'PATCH',
  headers: revision ? { 'If-Match': revision } : {},
  body: JSON.stringify(changes),
})
export const testWebSearch = (query) => apiRequest('/api/settings/web-search/test', { method: 'POST', body: JSON.stringify({ query }) })
export const loadProfileSettings = () => apiRequest('/api/settings/profile')
export const saveProfileSettings = (profile) => apiRequest('/api/settings/profile', { method: 'PATCH', body: JSON.stringify({ profile }) })

export const uploadProfileAvatar = (dataUrl) => apiRequest('/api/settings/avatar', {
  method: 'POST', body: JSON.stringify({ data_url: dataUrl }),
})
export const resetProfileAvatar = () => apiRequest('/api/settings/avatar', { method: 'DELETE' })

const appearanceImageEndpoint = (kind) => kind === 'background'
  ? '/api/settings/chat-background'
  : '/api/settings/user-avatar'

export const uploadAppearanceImage = (kind, dataUrl) => apiRequest(appearanceImageEndpoint(kind), {
  method: 'POST', body: JSON.stringify({ data_url: dataUrl }),
})
export const resetAppearanceImage = (kind) => apiRequest(appearanceImageEndpoint(kind), { method: 'DELETE' })
