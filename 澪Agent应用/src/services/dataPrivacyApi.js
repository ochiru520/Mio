import { apiRequest } from './api.js'

export function loadDataPrivacy() {
  return Promise.all([
    apiRequest('/api/backups'),
    apiRequest('/api/privacy/status'),
    apiRequest('/api/migrations/status'),
  ]).then(([backups, privacy, migrations]) => ({
    backups: backups.backups || [],
    privacy,
    migrations,
  }))
}

export function createCompleteBackup() {
  return apiRequest('/api/backups', { method: 'POST', body: '{}' })
}

export async function importCompleteBackup(file) {
  return apiRequest(`/api/backups/import?filename=${encodeURIComponent(file.name)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/zip' },
    body: file,
    deadlineClass: 'backup',
  })
}

export function restoreCompleteBackup(name) {
  return apiRequest(`/api/backups/${encodeURIComponent(name)}/restore`, {
    method: 'POST',
    body: '{}',
  })
}

export function setPrivacyPaused(paused) {
  return apiRequest(`/api/privacy/${paused ? 'pause' : 'resume'}`, {
    method: 'POST',
    body: '{}',
  })
}
