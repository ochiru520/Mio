import { apiRequest } from './api.js'

export const submitProviders = (payload, options = {}, transport = apiRequest) => transport("/api/agent/providers", { ...options, method: 'POST', body: JSON.stringify(payload) })
export const submitModelsDiscover = (payload, options = {}, transport = apiRequest) => transport("/api/agent/models/discover", { ...options, method: 'POST', body: JSON.stringify(payload) })
export const deleteModel = (id, options = {}, transport = apiRequest) => transport(`/api/agent/models/${encodeURIComponent(id)}`, { ...options, method: 'DELETE' })
export const deleteProvider = (provider_id, options = {}, transport = apiRequest) => transport(`/api/agent/providers/${encodeURIComponent(provider_id)}`, { ...options, method: 'DELETE' })
export const submitProviderRestore = (provider_id, options = {}, transport = apiRequest) => transport(`/api/agent/providers/${encodeURIComponent(provider_id)}/restore`, { ...options, method: 'POST', body: "{}" })
export const submitModelTest = (id, options = {}, transport = apiRequest) => transport(`/api/agent/models/${encodeURIComponent(id)}/test`, { ...options, method: 'POST', body: "{}" })

export const loadModelsProviderPresets = (options = {}, transport = apiRequest) => transport("/api/agent/models/provider-presets", Object.keys(options).length ? options : undefined)
