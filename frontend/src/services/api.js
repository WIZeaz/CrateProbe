import axios from 'axios'
import { getAdminToken } from './adminAuth'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

api.interceptors.request.use(config => {
  if (config.url && config.url.startsWith('/admin/')) {
    const token = getAdminToken()
    if (token) {
      config.headers = {
        ...config.headers,
        'X-Admin-Token': token,
      }
    }
  }
  return config
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

  async batchSetPriority(taskIds, priority) {
    const response = await api.post('/tasks/batch-priority', { task_ids: taskIds, priority })
    return response.data
  },

  async batchCancel(taskIds) {
    const response = await api.post('/tasks/batch-cancel', { task_ids: taskIds })
    return response.data
  },

  async getQueue() {
    const response = await api.get('/queue')
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

  // Admin endpoints
  async getRunners() {
    const response = await api.get('/admin/runners')
    return response.data
  },

  async createRunner(payload) {
    const response = await api.post('/admin/runners', payload)
    return response.data
  },

  async deleteRunner(runnerId) {
    const response = await api.delete(`/admin/runners/${runnerId}`)
    return response.data
  },

  async disableRunner(runnerId) {
    const response = await api.post(`/admin/runners/${runnerId}/disable`)
    return response.data
  },

  async enableRunner(runnerId) {
    const response = await api.post(`/admin/runners/${runnerId}/enable`)
    return response.data
  },

  async validateAdminToken() {
    const response = await api.head('/admin/runners')
    return response.status === 200 || response.status === 204
  },

  async getRunnerOverview() {
    const response = await api.get('/admin/runners/overview')
    return response.data
  },

  async getRunnerMetrics(runnerId, window = '1h') {
    const response = await api.get(`/admin/runners/${runnerId}/metrics`, {
      params: { window }
    })
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
    const response = await api.get(`/tasks/${taskId}/logs/${logType}/raw`, {
      responseType: 'blob'
    })
    return response.data
  }
}
