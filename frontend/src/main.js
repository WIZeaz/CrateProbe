import { createApp } from 'vue'
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
