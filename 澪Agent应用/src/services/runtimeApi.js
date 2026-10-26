import { apiRequest } from './api.js'

export const loadUsageByDays = (days, options = {}, transport = apiRequest) => transport(`/api/runtime/usage?days=${days}`, Object.keys(options).length ? options : undefined)
export const loadPolicies = (options = {}, transport = apiRequest) => transport("/api/runtime/policies", Object.keys(options).length ? options : undefined)
export const loadUsageByDays30 = (options = {}, transport = apiRequest) => transport("/api/runtime/usage?days=30", Object.keys(options).length ? options : undefined)
export const savePolicies = (mode, payload, options = {}, transport = apiRequest) => transport(`/api/runtime/policies/${encodeURIComponent(mode)}`, { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const loadOperations = (query, options = {}, transport = apiRequest) => transport(`/api/runtime/operations?${query}`, Object.keys(options).length ? options : undefined)
