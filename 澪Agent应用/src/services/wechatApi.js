import { apiRequest } from './api.js'

export const loadStatus = (options = {}, transport = apiRequest) => transport("/api/wechat/status", Object.keys(options).length ? options : undefined)
export const submitLogin = (options = {}, transport = apiRequest) => transport("/api/wechat/login", { ...options, method: 'POST' })
export const submitLoginStatus = (payload, options = {}, transport = apiRequest) => transport("/api/wechat/login/status", { ...options, method: 'POST', body: JSON.stringify(payload) })
export const updateConnection = (payload, options = {}, transport = apiRequest) => transport("/api/wechat/connection", { ...options, method: 'PATCH', body: JSON.stringify(payload) })
export const deleteConnection = (options = {}, transport = apiRequest) => transport("/api/wechat/connection", { ...options, method: 'DELETE' })
