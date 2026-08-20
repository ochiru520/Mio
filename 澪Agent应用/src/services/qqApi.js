import { apiRequest } from './api.js'

export const loadQqStatus = () => apiRequest('/api/agent/qq/status')
export const controlQq = (action) => apiRequest(`/api/agent/qq/${encodeURIComponent(action)}`, { method: 'POST', body: '{}' })
export const saveQqGroups = (payload) => apiRequest('/api/agent/qq/group-settings', { method: 'PATCH', body: JSON.stringify(payload) })
export const clearQqGroupContext = () => apiRequest('/api/agent/qq/group-context/clear', { method: 'POST', body: '{}' })
