import { apiRequest } from './api.js'

export const loadDependencies = () => apiRequest('/api/dependencies')
export const verifyDependency = (depId) => apiRequest(`/api/dependencies/${encodeURIComponent(depId)}/verify`, { method: 'POST', body: '{}', timeoutMs: 75_000 })
export const installDependency = (depId) => apiRequest(`/api/dependencies/${encodeURIComponent(depId)}/install`, { method: 'POST', body: '{}' })
export const loadDependencyStatus = (depId) => apiRequest(`/api/dependencies/${encodeURIComponent(depId)}/status`)
export const activateLocalVision = () => apiRequest('/api/dependencies/ollama_vision/activate', { method: 'POST', body: '{}', timeoutMs: 150_000 })
