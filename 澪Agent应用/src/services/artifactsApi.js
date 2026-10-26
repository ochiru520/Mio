import { apiRequest } from './api.js'
import { selectOperation } from './apiOperations.js'

export const loadArtifacts = (params, options = {}, transport = apiRequest) => transport(`/api/artifacts?${params}`, Object.keys(options).length ? options : undefined)
export const mutateArtifact = (id, operation, payload, options = {}, transport = apiRequest) => transport(`/api/artifacts/${id}/${selectOperation(operation, ['favorite', 'verify', 'review'])}`, { ...options, method: 'POST', ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) })
export const loadPreview = (id, options = {}, transport = apiRequest) => transport(`/api/artifacts/${id}/preview`, Object.keys(options).length ? options : undefined)
export const loadArtifactsByConversationIdLimit50 = (conversation, options = {}, transport = apiRequest) => transport(`/api/artifacts?conversation_id=${encodeURIComponent(conversation)}&limit=50`, Object.keys(options).length ? options : undefined)

export const loadArtifactsByProjectIdLimit30Offset = (projectId, pageOffset, options = {}, transport = apiRequest) => transport(`/api/artifacts?project_id=${encodeURIComponent(projectId)}&limit=30&offset=${pageOffset}`, Object.keys(options).length ? options : undefined)
