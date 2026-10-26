import { apiRequest } from './api.js'

const taskPath = id => `/api/agent/work/${encodeURIComponent(id)}`
const pagePath = (task, page) => `${taskPath(task)}/browser/pages/${encodeURIComponent(page)}`
export const updateInput = (task, id, payload, options = {}, transport = apiRequest) => transport(`${taskPath(task)}/inputs/${encodeURIComponent(id)}`, { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const withdrawInput = (task, id, revision, options = {}, transport = apiRequest) => transport(`${taskPath(task)}/inputs/${encodeURIComponent(id)}?expected_revision=${revision}`, { ...options, method: 'DELETE' })
export const browserPages = (task, options = {}, transport = apiRequest) => transport(`${taskPath(task)}/browser/pages`, options)
export const browserControl = (task, page, payload, options = {}, transport = apiRequest) => transport(`${pagePath(task, page)}/control`, { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const browserFrame = (task, page, options = {}, transport = apiRequest) => transport(`${pagePath(task, page)}/frame`, { ...options, responseType: 'blob' })
export const selectBrowserElement = (task, page, payload, options = {}, transport = apiRequest) => transport(`${pagePath(task, page)}/select`, { ...options, method: 'POST', body: JSON.stringify(payload) })
export const terminalInput = (task, job, text, options = {}, transport = apiRequest) => transport(`${taskPath(task)}/terminal/${encodeURIComponent(job)}/input`, { ...options, method: 'POST', body: JSON.stringify({ text }) })
export const terminalCloseInput = (task, job, options = {}, transport = apiRequest) => transport(`${taskPath(task)}/terminal/${encodeURIComponent(job)}/input`, { ...options, method: 'POST', body: JSON.stringify({ text: '', close: true }) })
export const notifications = (task, options = {}, transport = apiRequest) => transport(`${taskPath(task)}/notifications`, options)
export const allNotifications = (conversation = '', options = {}, transport = apiRequest) => transport(`/api/agent/work/notifications?conversation_id=${encodeURIComponent(conversation)}`, options)
export const retrievalOptions = (options = {}, transport = apiRequest) => transport('/api/workspace/knowledge/retrieval-options', options)
export const saveRetrievalOptions = (payload, options = {}, transport = apiRequest) => transport('/api/workspace/knowledge/retrieval-options', { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const dismissNotification = (task, id, options = {}, transport = apiRequest) => transport(`${taskPath(task)}/notifications/${encodeURIComponent(id)}/dismiss`, { ...options, method: 'POST', body: '{}' })
export const presentNotification = (task, id, options = {}, transport = apiRequest) => transport(`${taskPath(task)}/notifications/${encodeURIComponent(id)}/present`, { ...options, method: 'POST', body: '{}' })
export const inputReference = (conversation, task, id, offset=0, options={},transport=apiRequest) => transport(task ? `${taskPath(task)}/input-references/${encodeURIComponent(id)}?offset=${offset}&max_chars=12000` : `/api/agent/conversations/${encodeURIComponent(conversation)}/input-references/${encodeURIComponent(id)}?offset=${offset}&max_chars=12000`,options)
export const referenceCandidates = (params, options = {}, transport = apiRequest) => transport(`/api/workspace/references/candidates?${new URLSearchParams(params)}`, options)
export const resolveReferences = (payload, options = {}, transport = apiRequest) => transport('/api/workspace/references/resolve', { ...options, method: 'POST', body: JSON.stringify(payload) })
export const rewind = (conversation, payload, options = {}, transport = apiRequest) => transport(`/api/agent/conversations/${encodeURIComponent(conversation)}/rewind`, { ...options, method: 'POST', body: JSON.stringify(payload) })
export const turnIndex = (conversation, before = 0, options = {}, transport = apiRequest) => transport(`/api/agent/conversations/${encodeURIComponent(conversation)}/turn-index?before_id=${before}&limit=1000`, options)
export const saveLearningDraft = (project, session, payload, options = {}, transport = apiRequest) => transport(`/api/workspace/projects/${encodeURIComponent(project)}/learning/${encodeURIComponent(session)}/draft`, { ...options, method: 'PUT', body: JSON.stringify(payload) })

export const browserUserAction = (task,page,payload,options={},transport=apiRequest) => transport(`${pagePath(task,page)}/user-action`,{...options,method:'POST',body:JSON.stringify(payload)})
