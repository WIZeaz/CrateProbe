import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// Request interceptor for error handling
api.interceptors.response.use(
  response => response,
  error => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

export default {
  // Task endpoints
  async createTask(crate_name, version = null) {
    const response = await api.post('/tasks', { crate_name, version })
    return response.data
  },

  async getAllTasks() {
    const response = await api.get('/tasks')
    return response.data
  },

  async getTask(taskId) {
    const response = await api.get(`/tasks/${taskId}`)
    return response.data
  },

  async cancelTask(taskId) {
    const response = await api.delete(`/tasks/${taskId}`)
    return response.data
  },

  // Dashboard endpoints
  async getDashboardStats() {
    const response = await api.get('/dashboard/stats')
    return response.data
  },

  async getSystemStats() {
    const response = await api.get('/dashboard/system')
    return response.data
  },

  // Log endpoints
  async getLog(taskId, logType, lines = 1000) {
    const response = await api.get(`/tasks/${taskId}/logs/${logType}`, {
      params: { lines }
    })
    return response.data
  },

  async downloadLog(taskId, logType) {
    const response = await api.get(`/tasks/${taskId}/logs/${logType}/download`, {
      responseType: 'blob'
    })
    return response.data
  }
}
