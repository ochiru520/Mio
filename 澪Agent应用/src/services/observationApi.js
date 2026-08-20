import { apiRequest } from './api.js'

export const loadCompanionStatus = () => apiRequest('/api/companion/status')
export const listWindows = () => apiRequest('/api/companion/windows')
export const stopScreen = () => apiRequest('/api/companion/screen/stop', { method: 'POST', body: '{}' })
export const startScreen = (payload) => apiRequest('/api/companion/screen/start', { method: 'POST', body: JSON.stringify(payload) })
export const analyzeScreen = (payload) => apiRequest('/api/companion/screen/analyze', { method: 'POST', body: JSON.stringify(payload) })
export const selectGame = (hwnd) => apiRequest('/api/companion/game/select', { method: 'POST', body: JSON.stringify({ hwnd }) })
export const startGame = (intervalMs) => apiRequest('/api/companion/game/start', { method: 'POST', body: JSON.stringify({ interval_ms: intervalMs }) })
export const analyzeGame = () => apiRequest('/api/companion/game/analyze', { method: 'POST', body: '{}' })
