import { apiRequest } from './api.js'

// Root-window lifecycle requests. Domain work is handled by the other APIs.
export function loadSourceHandoff(conversationId, transport = apiRequest) {
  return transport(`/api/agent/work/handoff/source/${encodeURIComponent(conversationId)}`)
}

export function checkAutomaticDiary(transport = apiRequest) {
  return transport('/api/diary/automatic/check', { method: 'POST', body: '{}' })
}

export function reportAppReady(transport = apiRequest) {
  return transport('/api/companion/app-ready', { method: 'POST' })
}

export function loadModelCatalog(transport = apiRequest) {
  return transport('/api/agent/models/catalog')
}

export function reportPetChatWindow(open, transport = apiRequest) {
  return transport('/api/companion/chat-window/state', {
    method: 'POST', body: JSON.stringify({ open: Boolean(open) }),
  })
}
