import { selectOperation } from './apiOperations.js'
import { apiRequest } from './api.js'

export const loadSurface = (options = {}, transport = apiRequest) => transport("/api/agent/surface", Object.keys(options).length ? options : undefined)
export const submitSurfaceImages = (kind, payload, options = {}, transport = apiRequest) => transport(`/api/agent/surface/images/${selectOperation(kind, ['hero', 'personal'])}`, { ...options, method: 'POST', body: JSON.stringify(payload) })
export const deleteSurfaceImages = (kind, options = {}, transport = apiRequest) => transport(`/api/agent/surface/images/${selectOperation(kind, ['hero', 'personal'])}`, { ...options, method: 'DELETE' })
export const saveSurface = (payload, options = {}, transport = apiRequest) => transport("/api/agent/surface", { ...options, method: 'PUT', body: JSON.stringify(payload) })
