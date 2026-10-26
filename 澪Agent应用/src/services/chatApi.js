import { apiRequest } from './api.js'

export const submitConversationChat = (companion, payload, options = {}, transport = apiRequest) => transport(companion ? '/api/companion/chat' : '/api/agent/chat', { ...options, method: 'POST', body: JSON.stringify(payload) })
export const cancelConversationChat = (companion, payload, options = {}, transport = apiRequest) => transport(companion ? '/api/companion/chat/cancel' : '/api/agent/chat/cancel', { ...options, method: 'POST', body: JSON.stringify(payload), deadlineClass: 'mutation' })

export const loadBootstrap = () => apiRequest('/api/agent/bootstrap')
export const listConversations = () => apiRequest('/api/agent/conversations')
export const createConversation = (title = '新对话', workspace = 'main') => apiRequest('/api/agent/conversations', { method: 'POST', body: JSON.stringify({ title, workspace }) })
export const renameConversation = (id, title) => apiRequest(`/api/agent/conversations/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ title }) })
export const deleteConversation = (id) => apiRequest(`/api/agent/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
export const deleteConversationMessages = (id, messageIds) => apiRequest(`/api/agent/conversations/${encodeURIComponent(id)}/messages/delete`, { method: 'POST', body: JSON.stringify({ message_ids: messageIds }) })
export const loadMessages = (conversationId, limit = 120) => apiRequest(`/api/agent/messages?limit=${limit}&conversation_id=${encodeURIComponent(conversationId)}`)
export const loadContextUsage = (conversationId) => apiRequest(`/api/agent/context-usage?conversation_id=${encodeURIComponent(conversationId)}`)
export const sendChat = (payload, signal) => apiRequest('/api/agent/chat', { method: 'POST', body: JSON.stringify(payload), signal })

export const loadContextUsageByConversationId = (conversationId, options = {}, transport = apiRequest) => transport(`/api/agent/context-usage?conversation_id=${encodeURIComponent(conversationId)}`, Object.keys(options).length ? options : undefined)
export const loadTokenUsageByDays30 = (options = {}, transport = apiRequest) => transport("/api/agent/token-usage?days=30", Object.keys(options).length ? options : undefined)
export const loadMessagesByLimit120ConversationId = (conversationId, options = {}, transport = apiRequest) => transport(`/api/agent/messages?limit=120&conversation_id=${encodeURIComponent(conversationId)}`, Object.keys(options).length ? options : undefined)
export const loadChatRequestsByConversationId = (clientRequestId, conversationId, options = {}, transport = apiRequest) => transport(`/api/agent/chat/requests/${encodeURIComponent(clientRequestId)}?conversation_id=${encodeURIComponent(conversationId)}`, Object.keys(options).length ? options : undefined)
export const loadMessagesByLimit120BeforeIdConversationId = (first, id, options = {}, transport = apiRequest) => transport(`/api/agent/messages?limit=120&before_id=${first}&conversation_id=${encodeURIComponent(id)}`, Object.keys(options).length ? options : undefined)
export const submitStartupGreeting = (payload, options = {}, transport = apiRequest) => transport("/api/agent/startup-greeting", { ...options, method: 'POST', body: JSON.stringify(payload) })
export const updateConversation = (id, payload, options = {}, transport = apiRequest) => transport(`/api/agent/conversations/${encodeURIComponent(id)}`, { ...options, method: 'PATCH', body: JSON.stringify(payload) })
export const deleteConversationById = (id, options = {}, transport = apiRequest) => transport(`/api/agent/conversations/${encodeURIComponent(id)}`, { ...options, method: 'DELETE' })
export const loadStatsByYearMonth = (year, month, options = {}, transport = apiRequest) => transport(`/api/agent/stats?year=${year}&month=${month}`, Object.keys(options).length ? options : undefined)

export const loadConversations = (options = {}, transport = apiRequest) => transport("/api/agent/conversations", Object.keys(options).length ? options : undefined)
export const updateConversationState = (id, payload, options = {}, transport = apiRequest) => transport(`/api/agent/conversations/${id}/state`, { ...options, method: 'PATCH', body: JSON.stringify(payload) })
export const submitConversationFork = (id, options = {}, transport = apiRequest) => transport(`/api/agent/conversations/${id}/fork`, { ...options, method: 'POST' })
export const loadConversationExport = (id, options = {}, transport = apiRequest) => transport(`/api/agent/conversations/${id}/export`, Object.keys(options).length ? options : undefined)
