import { apiRequest } from './api.js'

export const reportActiveView = (payload) => apiRequest('/api/agent/self/active-view', {
  method: 'POST',
  body: JSON.stringify(payload),
})
