import http from 'node:http'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiBaseUrl = process.env.VITE_API_BASE_URL || 'http://localhost:8080'
const wsBaseUrl = process.env.VITE_WS_BASE_URL || 'ws://localhost:8080'

// Disable keep-alive for the dev API proxy. Node 18+ reuses idle sockets by
// default, but uvicorn closes them after its 5s keep-alive timeout; the race
// makes the proxy send requests over a dying socket and answer 502. A fresh
// connection per request is cheap on localhost.
const noKeepAliveAgent = new http.Agent({ keepAlive: false })

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
        agent: noKeepAliveAgent,
      },
      '/ws': {
        target: wsBaseUrl,
        ws: true,
      }
    }
  }
})
