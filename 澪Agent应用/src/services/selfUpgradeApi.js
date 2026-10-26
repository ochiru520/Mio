import { apiRequest } from './api.js'

export const loadUpgradeStatus = (transport = apiRequest) => transport('/api/self-upgrade/status', { method: 'GET', timeoutMs: 15000 })
export const saveUpgradeConfiguration = (payload, transport = apiRequest) => transport('/api/self-upgrade/configuration', { method: 'PUT', body: JSON.stringify(payload), timeoutMs: 600000 })
export const createUpgradeJob = (payload, transport = apiRequest) => transport('/api/self-upgrade/jobs', { method: 'POST', body: JSON.stringify(payload), timeoutMs: 600000 })
export function controlUpgradeJob(id, action, payload = {}, transport = apiRequest) {
  if (!['apply', 'stop', 'validate', 'build', 'preview'].includes(action)) throw new Error(`Unknown upgrade action: ${action}`)
  return transport(`/api/self-upgrade/jobs/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: JSON.stringify(payload), timeoutMs: 600000 })
}
export const rollbackUpgrade = (transport = apiRequest) => transport('/api/self-upgrade/rollback', { method: 'POST', body: JSON.stringify({ confirmed: true }), timeoutMs: 600000 })
