import { apiRequest } from './api.js'

export const loadDiaryDashboard = () => apiRequest('/api/agent/day-dashboard')
export const generateTodayDiary = () => apiRequest('/api/diary/generate-today', { method: 'POST', body: '{}' })
export const listDailyReviews = () => apiRequest('/api/reviews')
export const listWeeklyReviews = () => apiRequest('/api/weekly')
export const listMonthlyReviews = () => apiRequest('/api/monthly')
