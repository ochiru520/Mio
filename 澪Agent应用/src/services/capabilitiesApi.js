import { selectOperation } from './apiOperations.js'
import { apiRequest } from './api.js'

export const loadCapabilities = (options = {}, transport = apiRequest) => transport("/api/capabilities", Object.keys(options).length ? options : undefined)
export const submitCapabilities = (id, action, payload, options = {}, transport = apiRequest) => transport(`/api/capabilities/${id}/${selectOperation(action, ['check', 'cancel'])}`, { ...options, method: 'POST', body: JSON.stringify(payload) })
