import { apiRequest } from './api.js'

export const loadDependencies = () => apiRequest('/api/dependencies')
export const installDependency = (depId) => apiRequest(`/api/dependencies/${encodeURIComponent(depId)}/install`, { method: 'POST', body: '{}' })
export const loadDependencyStatus = (depId) => apiRequest(`/api/dependencies/${encodeURIComponent(depId)}/status`)
