import { apiRequest } from './api.js'

export const loadBootstrap = () => apiRequest('/api/agent/bootstrap')
export const listConversations = () => apiRequest('/api/agent/conversations')
export const createConversation = (title = '新对话') => apiRequest('/api/agent/conversations', { method: 'POST', body: JSON.stringify({ title }) })
export const renameConversation = (id, title) => apiRequest(`/api/agent/conversations/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ title }) })
export const deleteConversation = (id) => apiRequest(`/api/agent/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
export const loadMessages = (conversationId, limit = 120) => apiRequest(`/api/agent/messages?limit=${limit}&conversation_id=${encodeURIComponent(conversationId)}`)
export const loadContextUsage = (conversationId) => apiRequest(`/api/agent/context-usage?conversation_id=${encodeURIComponent(conversationId)}`)
export const sendChat = (payload, signal) => apiRequest('/api/agent/chat', { method: 'POST', body: JSON.stringify(payload), signal })
