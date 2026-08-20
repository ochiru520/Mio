import { apiRequest } from './api.js'

export const loadAutonomy = (limit = 100) => apiRequest(`/api/agent/autonomy?limit=${limit}`)

export const updateAutonomyPolicy = (changes) => apiRequest('/api/agent/autonomy/policy', {
  method: 'PATCH',
  body: JSON.stringify(changes),
})

export const createAutonomyGoal = (payload) => apiRequest('/api/agent/autonomy/goals', {
  method: 'POST',
  body: JSON.stringify(payload),
})

export const updateAutonomyGoalStatus = (goalId, status) => apiRequest(`/api/agent/autonomy/goals/${goalId}/status`, {
  method: 'POST',
  body: JSON.stringify({ status }),
})

export const approveAutonomyBehavior = (behaviorId) => apiRequest(`/api/agent/autonomy/behaviors/${behaviorId}/approve`, {
  method: 'POST',
  body: '{}',
})

export const cancelAutonomyBehavior = (behaviorId) => apiRequest(`/api/agent/autonomy/behaviors/${behaviorId}/cancel`, {
  method: 'POST',
  body: '{}',
})
