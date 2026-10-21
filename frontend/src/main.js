import { createApp } from 'vue'
import '@fontsource-variable/archivo'
import '@fontsource/ibm-plex-mono/latin-400.css'
import '@fontsource/ibm-plex-mono/latin-500.css'
import './style.css'
import App from './App.vue'
import router from './router'
import websocket from './services/websocket'

const app = createApp(App)

app.use(router)

// Make websocket available globally
app.config.globalProperties.$ws = websocket

// Connect to WebSocket (dashboard endpoint by default)
websocket.connect('/ws/dashboard')

app.mount('#app')
