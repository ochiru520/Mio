import { apiRequest } from './api.js'

export const loadRuntimeSettings = () => apiRequest('/api/settings/runtime')
export const saveRuntimeSettings = (changes) => apiRequest('/api/settings/runtime', { method: 'PATCH', body: JSON.stringify(changes) })
export const testWebSearch = (query) => apiRequest('/api/settings/web-search/test', { method: 'POST', body: JSON.stringify({ query }) })
export const loadProfileSettings = () => apiRequest('/api/settings/profile')
export const saveProfileSettings = (profile) => apiRequest('/api/settings/profile', { method: 'PATCH', body: JSON.stringify({ profile }) })
