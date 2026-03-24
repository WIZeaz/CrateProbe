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
    const response = await api.post(`/tasks/${taskId}/cancel`)
    return response.data
  },

  async deleteTask(taskId) {
    const response = await api.delete(`/tasks/${taskId}`)
    return response.data
  },

  async retryTask(taskId) {
    const response = await api.post(`/tasks/${taskId}/retry`)
    return response.data
  },

  async batchRetry(taskIds) {
    const response = await api.post('/tasks/batch-retry', { task_ids: taskIds })
    return response.data
  },

  async batchDelete(taskIds) {
    const response = await api.post('/tasks/batch-delete', { task_ids: taskIds })
    return response.data
  },

  async getTaskRealtimeStats(taskId) {
    const response = await api.get(`/tasks/${taskId}/stats`)
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

  async getTaskStats(taskId) {
    try {
      const response = await api.get(`/tasks/${taskId}/logs/stats-yaml`)
      const lines = response.data.lines || []
      const stats = {}
      const integerPattern = /^-?\d+$/

      lines.forEach(line => {
        const match = line.match(/^([A-Za-z0-9_]+):\s*(.*)$/)
        if (!match) {
          return
        }

        const [, key, value] = match
        stats[key] = integerPattern.test(value) ? Number(value) : value
      })

      return stats
    } catch (error) {
      if (error.response?.status === 404) {
        return {}
      }
      throw error
    }
  },

  async downloadLog(taskId, logType) {
    const response = await api.get(`/tasks/${taskId}/logs/${logType}/raw`, {
      responseType: 'blob'
    })
    return response.data
  }
}
