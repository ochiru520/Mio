import { apiRequest } from './api.js'

export function loadOnboardingStatus() {
  return apiRequest('/api/onboarding/status')
}

export function loadOnboardingEnvironment() {
  return apiRequest('/api/onboarding/environment')
}

export function completeOnboarding(payload) {
  return apiRequest('/api/onboarding/complete', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function discoverOnboardingModels(payload) {
  return apiRequest('/api/agent/models/discover', {
    method: 'POST',
    deadlineClass: 'model',
    body: JSON.stringify(payload),
  })
}

export function createOnboardingProvider(payload) {
  return apiRequest('/api/agent/providers', {
    method: 'POST',
    deadlineClass: 'model',
    body: JSON.stringify(payload),
  })
}

export function testOnboardingModel(modelId) {
  return apiRequest(`/api/agent/models/${encodeURIComponent(modelId)}/test`, {
    method: 'POST',
    deadlineClass: 'model',
    body: '{}',
  })
}
