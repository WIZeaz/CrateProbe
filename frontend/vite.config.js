import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiBaseUrl = process.env.VITE_API_BASE_URL || 'http://localhost:8080'
const wsBaseUrl = process.env.VITE_WS_BASE_URL || 'ws://localhost:8080'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    watch: {
      usePolling: true,
      interval: 100
    },
    proxy: {
      '/api': {
        target: apiBaseUrl,
        changeOrigin: true,
      },
      '/ws': {
        target: wsBaseUrl,
        ws: true,
      }
    }
  }
})
