import { apiRequest } from './api.js'

export const loadDiaryDashboard = () => apiRequest('/api/agent/day-dashboard')
export const generateTodayDiary = () => apiRequest('/api/diary/generate-today', { method: 'POST', body: '{}' })
export const listDailyReviews = () => apiRequest('/api/reviews')
export const listWeeklyReviews = () => apiRequest('/api/weekly')
export const listMonthlyReviews = () => apiRequest('/api/monthly')

export const loadReviews = (options = {}, transport = apiRequest) => transport("/api/reviews", Object.keys(options).length ? options : undefined)
export const loadWeekly = (options = {}, transport = apiRequest) => transport("/api/weekly", Object.keys(options).length ? options : undefined)
export const loadMonthly = (options = {}, transport = apiRequest) => transport("/api/monthly", Object.keys(options).length ? options : undefined)
export const loadDiariesByQ = (query, options = {}, transport = apiRequest) => transport(`/api/diaries?q=${encodeURIComponent(query)}`, Object.keys(options).length ? options : undefined)
export const loadDiary = (date, options = {}, transport = apiRequest) => transport(`/api/diaries/${date}`, Object.keys(options).length ? options : undefined)
export const submitDiaryConfirm = (date, payload, options = {}, transport = apiRequest) => transport(`/api/diaries/${date}/confirm`, { ...options, method: 'POST', body: JSON.stringify(payload) })
export const saveDiary = (date, payload, options = {}, transport = apiRequest) => transport(`/api/diaries/${date}`, { ...options, method: 'PUT', body: JSON.stringify(payload) })
export const submitReviewsGenerate = (date, options = {}, transport = apiRequest) => transport(`/api/reviews/${date}/generate`, { ...options, method: 'POST', body: "{}" })
export const submitWeeklyGenerate = (start, options = {}, transport = apiRequest) => transport(`/api/weekly/${start}/generate`, { ...options, method: 'POST', body: "{}" })
export const submitMonthlyGenerate = (month, options = {}, transport = apiRequest) => transport(`/api/monthly/${month}/generate`, { ...options, method: 'POST', body: "{}" })

export const loadDiaryVersions = (date, options = {}, transport = apiRequest) => transport(`/api/diaries/${date}/versions`, Object.keys(options).length ? options : undefined)
export const loadDiaryVersionDiff = (date, version, options = {}, transport = apiRequest) => transport(`/api/diaries/${date}/versions/${version}/diff`, Object.keys(options).length ? options : undefined)
export const submitDiaryVersionRestore = (date, version, payload, options = {}, transport = apiRequest) => transport(`/api/diaries/${date}/versions/${version}/restore`, { ...options, method: 'POST', body: JSON.stringify(payload) })
