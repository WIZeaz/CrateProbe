import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import * as toml from 'toml'

// Read config from project root
function loadConfig() {
  try {
    const configPath = resolve(__dirname, '../config.toml')
    const configContent = readFileSync(configPath, 'utf-8')
    const config = toml.parse(configContent)
    return config
  } catch (error) {
    console.warn('Failed to load config.toml, using defaults:', error.message)
    return {
      server: { port: 8080 },
      frontend: {
        dev_port: 5173,
        api_proxy_target: 'http://localhost:8080',
        ws_proxy_target: 'ws://localhost:8080'
      }
    }
  }
}

const config = loadConfig()
const frontendConfig = config.frontend || {}

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: frontendConfig.dev_port || 5173,
    proxy: {
      '/api': {
        target: frontendConfig.api_proxy_target || 'http://localhost:8080',
        changeOrigin: true,
      },
      '/ws': {
        target: frontendConfig.ws_proxy_target || 'ws://localhost:8080',
        ws: true,
      }
    }
  }
})
