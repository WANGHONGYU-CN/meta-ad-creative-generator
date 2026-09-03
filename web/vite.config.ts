import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发时 API 与图片文件都代理到 FastAPI（8000）；生产构建产物放 web/dist，
// 由 FastAPI 直接托管（同源，无需代理）。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/files': 'http://127.0.0.1:8000',
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
  },
})
