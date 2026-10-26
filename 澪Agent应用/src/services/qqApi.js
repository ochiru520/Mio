import { selectOperation } from './apiOperations.js'
import { apiRequest } from './api.js'

export const loadQqQrCodeResponse = (timestamp = Date.now(), transport = fetch) => transport(`/api/agent/qq/qrcode?ts=${timestamp}`, { headers: { Accept: 'image/png,image/*' }, cache: 'no-store' })

export const loadQqStatus = () => apiRequest('/api/agent/qq/status')
export const controlQq = (action) => apiRequest(`/api/agent/qq/${encodeURIComponent(action)}`, { method: 'POST', body: '{}' })
export const saveQqGroups = (payload) => apiRequest('/api/agent/qq/group-settings', { method: 'PATCH', body: JSON.stringify(payload) })
export const clearQqGroupContext = () => apiRequest('/api/agent/qq/group-context/clear', { method: 'POST', body: '{}' })

export const loadStatus = (options = {}, transport = apiRequest) => transport("/api/agent/qq/status", Object.keys(options).length ? options : undefined)
export const submitQq = (action, options = {}, transport = apiRequest) => transport(`/api/agent/qq/${selectOperation(action, ['start', 'stop', 'restart', 'login'])}`, { ...options, method: 'POST', body: "{}" })
export const submitLogin = (options = {}, transport = apiRequest) => transport("/api/agent/qq/login", { ...options, method: 'POST', body: "{}" })
export const submitSetup = (payload, options = {}, transport = apiRequest) => transport("/api/agent/qq/setup", { ...options, method: 'POST', body: JSON.stringify(payload) })
export const submitTestDelivery = (payload, options = {}, transport = apiRequest) => transport("/api/agent/qq/test-delivery", { ...options, method: 'POST', body: JSON.stringify(payload) })
export const updateGroupSettings = (payload, options = {}, transport = apiRequest) => transport("/api/agent/qq/group-settings", { ...options, method: 'PATCH', body: JSON.stringify(payload) })
export const submitGroupContextClear = (options = {}, transport = apiRequest) => transport("/api/agent/qq/group-context/clear", { ...options, method: 'POST', body: "{}" })
