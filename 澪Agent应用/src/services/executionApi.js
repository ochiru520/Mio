import { selectOperation } from './apiOperations.js'
import { apiRequest } from './api.js'

export const loadProject = (projectId, options = {}, transport = apiRequest) => transport(`/api/workspace/execution/projects/${projectId}`, Object.keys(options).length ? options : undefined)
export const loadProjectFiles = (projectId, query, options = {}, transport = apiRequest) => transport(`/api/workspace/execution/projects/${projectId}/files?${query}`, Object.keys(options).length ? options : undefined)
export const loadProjectHistory = (projectId, query, options = {}, transport = apiRequest) => transport(`/api/workspace/execution/projects/${projectId}/history?${query}`, Object.keys(options).length ? options : undefined)
export const loadProjectFile = (projectId, query, options = {}, transport = apiRequest) => transport(`/api/workspace/execution/projects/${projectId}/file?${query}`, Object.keys(options).length ? options : undefined)
export const loadProjectChange = (projectId, id, query, options = {}, transport = apiRequest) => transport(`/api/workspace/execution/projects/${projectId}/changes/${id}?${query}`, Object.keys(options).length ? options : undefined)
export const loadProjectJob = (projectId, id, query, options = {}, transport = apiRequest) => transport(`/api/workspace/execution/projects/${projectId}/jobs/${id}?${query}`, Object.keys(options).length ? options : undefined)
export const stopProjectJob = (projectId, id, query, options = {}, transport = apiRequest) => transport(`/api/workspace/execution/projects/${projectId}/jobs/${id}/stop?${query}`, { ...options, method: 'POST' })
export const loadExecutionPolicy = (options = {}, transport = apiRequest) => transport("/api/execution/policy", Object.keys(options).length ? options : undefined)
export const saveExecutionPolicy = (payload, options = {}, transport = apiRequest) => transport("/api/execution/policy", { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const loadDefaults = (options = {}, transport = apiRequest) => transport("/api/workspace/execution/defaults", Object.keys(options).length ? options : undefined)
export const saveDefaults = (payload, options = {}, transport = apiRequest) => transport("/api/workspace/execution/defaults", { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const saveProject = (id, kind, payload, options = {}, transport = apiRequest) => transport(`/api/workspace/execution/projects/${id}/${selectOperation(kind, ['code', 'desktop'])}`, { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const loadDesktopWindows = (options = {}, transport = apiRequest) => transport("/api/workspace/execution/desktop/windows", Object.keys(options).length ? options : undefined)
