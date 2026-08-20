import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const localBackendProxy = () => ({
  target: 'http://127.0.0.1:8000',
  changeOrigin: false,
  configure(proxy) {
    proxy.on('proxyReq', (proxyRequest, request) => {
      if (request.headers.host) proxyRequest.setHeader('host', request.headers.host)
    })
  },
})

export default defineConfig({
  base: './',
  plugins: [vue()],
  server: {
    port: 1420,
    strictPort: false,
    proxy: {
      '/api': localBackendProxy(),
      '/onebot': localBackendProxy(),
      '/diaries': localBackendProxy(),
      '/reviews': localBackendProxy(),
      '/weekly': localBackendProxy(),
      '/stats': localBackendProxy(),
      '/static': localBackendProxy(),
      '/agent-files': localBackendProxy(),
    }
  }
})
