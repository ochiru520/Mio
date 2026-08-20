import { apiRequest } from './api.js'

export const loadMemory = () => apiRequest('/api/memory')
export const saveRuntimeSummary = (content) => apiRequest('/api/memory/runtime-summary', { method: 'PUT', body: JSON.stringify({ content }) })
export const createMemory = (payload) => apiRequest('/api/memory/items', { method: 'POST', body: JSON.stringify(payload) })
export const updateMemory = (id, payload) => apiRequest(`/api/memory/items/${id}`, { method: 'PATCH', body: JSON.stringify(payload) })
export const deleteMemory = (id) => apiRequest(`/api/memory/items/${id}`, { method: 'DELETE' })
export const restoreMemory = (id) => apiRequest(`/api/memory/items/${id}/restore`, { method: 'POST', body: '{}' })
export const recordFollowUpResult = (id, payload) => apiRequest(`/api/threads/${id}/result`, { method: 'POST', body: JSON.stringify(payload) })
