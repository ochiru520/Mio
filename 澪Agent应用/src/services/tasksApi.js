import { selectOperation } from './apiOperations.js'
import { apiRequest } from './api.js'

export function openWorkStream(taskId, afterId = 0, EventSourceCtor = globalThis.EventSource) {
  if (typeof EventSourceCtor !== 'function') throw new Error('EventSource is unavailable')
  return new EventSourceCtor(`/api/agent/work/${encodeURIComponent(taskId)}/stream?after_id=${encodeURIComponent(afterId)}`)
}

export const loadTasksByLimit200 = (options = {}, transport = apiRequest) => transport("/api/agent/tasks?limit=200", Object.keys(options).length ? options : undefined)
export const submitTaskApprove = (id, options = {}, transport = apiRequest) => transport(`/api/agent/tasks/${id}/approve`, { ...options, method: 'POST', body: "{}" })
export const submitTaskCancel = (id, options = {}, transport = apiRequest) => transport(`/api/agent/tasks/${id}/cancel`, { ...options, method: 'POST', body: "{}" })

export const loadWorkFileRoots = (options = {}, transport = apiRequest) => transport("/api/agent/work/file-roots", Object.keys(options).length ? options : undefined)
export const saveWorkFileRoots = (payload, options = {}, transport = apiRequest) => transport("/api/agent/work/file-roots", { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const loadWorkCitationsByAfterIdLimit50 = (id, afterId, options = {}, transport = apiRequest) => transport(`/api/agent/work/${id}/citations?after_id=${afterId}&limit=50`, Object.keys(options).length ? options : undefined)
export const loadWorkByLimit6 = (options = {}, transport = apiRequest) => transport("/api/agent/work?limit=6", Object.keys(options).length ? options : undefined)
export const loadWorkByLimit20 = (options = {}, transport = apiRequest) => transport("/api/agent/work?limit=20", Object.keys(options).length ? options : undefined)
export const loadWorkByConversationIdLimit1 = (id, options = {}, transport = apiRequest) => transport(`/api/agent/work?conversation_id=${encodeURIComponent(id)}&limit=1`, Object.keys(options).length ? options : undefined)
export const loadWorkByConversationIdLimit30 = (conversation, options = {}, transport = apiRequest) => transport(`/api/agent/work?conversation_id=${encodeURIComponent(conversation)}&limit=30`, Object.keys(options).length ? options : undefined)
export const loadWork = (task_id, options = {}, transport = apiRequest) => transport(`/api/agent/work/${task_id}`, Object.keys(options).length ? options : undefined)
export const loadWorkInputsByAfterVersion = (id, after, options = {}, transport = apiRequest) => transport(`/api/agent/work/${encodeURIComponent(id)}/inputs?after_version=${after}`, Object.keys(options).length ? options : undefined)
export const loadWorkByLimit1 = (options = {}, transport = apiRequest) => transport("/api/agent/work?limit=1", Object.keys(options).length ? options : undefined)
export const saveWorkLimits = (payload, options = {}, transport = apiRequest) => transport("/api/agent/work/limits", { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const loadWorkByConversationIdStatusOffset = (conversationId, requestedFilter, requestedOffset, options = {}, transport = apiRequest) => transport(`/api/agent/work?conversation_id=${encodeURIComponent(conversationId)}&status=${requestedFilter}&offset=${requestedOffset}`, Object.keys(options).length ? options : undefined)
export const submitWork = (id, command, options = {}, transport = apiRequest) => transport(`/api/agent/work/${encodeURIComponent(id)}/${selectOperation(command, ['pause', 'resume', 'cancel'])}`, { ...options, method: 'POST', body: "{}" })

export const submitWorkPause = (id, options = {}, transport = apiRequest) => transport(`/api/agent/work/${id}/pause`, { ...options, method: 'POST' })
export const submitWorkResume = (id, options = {}, transport = apiRequest) => transport(`/api/agent/work/${id}/resume`, { ...options, method: 'POST' })
export const submitWorkCancel = (id, options = {}, transport = apiRequest) => transport(`/api/agent/work/${id}/cancel`, { ...options, method: 'POST' })
export const submitWorkActionsApprove = (id, action_id, options = {}, transport = apiRequest) => transport(`/api/agent/work/${id}/actions/${action_id}/approve`, { ...options, method: 'POST' })

export const approveWorkAction = (id, actionId, options = {}, transport = apiRequest) => transport(`/api/agent/work/${encodeURIComponent(id)}/actions/${actionId}/approve`, { ...options, method: 'POST', body: '{}' })
