import { apiRequest } from './api.js'

const setupPath = '/api/model-management/anif/setup'

export const loadAnifSetup = () => apiRequest(setupPath, { timeoutMs: 30_000 })
export const startAnifSetup = voice_id => apiRequest(setupPath, { method: 'POST', body: JSON.stringify({ voice_id }), timeoutMs: 60_000 })
export const cancelAnifSetup = () => apiRequest(`${setupPath}/cancel`, { method: 'POST', body: '{}', timeoutMs: 60_000 })
export const activateAnifSetup = () => apiRequest(`${setupPath}/activate`, { method: 'POST', body: '{}', timeoutMs: 90_000 })
export const previewAnifUninstall = () => apiRequest(`${setupPath}/uninstall-preview`, { method: 'POST', body: '{}', timeoutMs: 60_000 })
export const uninstallAnifSetup = token => apiRequest(`${setupPath}/uninstall`, { method: 'POST', body: JSON.stringify({ token }), timeoutMs: 180_000 })
export const loadAnifPreview = (jobId, language) => {
  if (!['zh', 'ja'].includes(language)) return Promise.reject(new Error('请选择中文或日语试听。'))
  return apiRequest(`${setupPath}/audio/${language}?job_id=${encodeURIComponent(jobId)}`, { responseType: 'blob', timeoutMs: 60_000 })
}
