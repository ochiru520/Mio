import { selectOperation } from './apiOperations.js'
import { apiRequest } from './api.js'

export function loadToolSection(section, options = {}, transport = apiRequest) {
  const endpoints = { catalog: '/api/agent/tools', autonomy: '/api/agent/autonomy?limit=100', receipts: '/api/agent/tool-receipts?limit=100' }
  if (!Object.hasOwn(endpoints, section)) throw new Error(`Unknown tool section: ${section}`)
  return transport(endpoints[section], options)
}

export const updateAutonomyPolicy = (payload, options = {}, transport = apiRequest) => transport("/api/agent/autonomy/policy", { ...options, method: 'PATCH', body: JSON.stringify(payload) })
export const submitAutonomy = (action, options = {}, transport = apiRequest) => transport(`/api/agent/autonomy/${selectOperation(action, ['pause', 'resume'])}`, { ...options, method: 'POST' })
