import axios from 'axios'
import { getAdminToken } from './adminAuth'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// Inject the admin token the same way for every admin-protected endpoint:
// runner management under /admin/* and task definition mutations
// (POST/PUT/DELETE /tasks*). Listing tasks (GET) stays public.
api.interceptors.request.use(config => {
  const url = config.url || ''
  const method = (config.method || 'get').toLowerCase()
  const needsAdminToken =
    url.startsWith('/admin/') ||
    (url.startsWith('/tasks') && method !== 'get')
  if (needsAdminToken) {
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

// Response interceptor for error handling
api.interceptors.response.use(
  response => response,
  error => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

// Encode a relative file path for use in a URL, keeping '/' separators.
function encodePath(path) {
  return path
    .split('/')
    .map(segment => encodeURIComponent(segment))
    .join('/')
}

export default {
  // Job endpoints
  async createJob(crateName, version, taskId) {
    const response = await api.post('/jobs', {
      crate_name: crateName,
      version,
      task_id: taskId,
    })
    return response.data
  },

  async getJobs({
    page = 1,
    pageSize = 20,
    status = null,
    crateName = null,
    taskName = null,
    sortBy = 'created_at',
    sortDir = 'desc',
  } = {}) {
    const params = {
      page,
      page_size: pageSize,
      sort_by: sortBy,
      sort_dir: sortDir,
    }
    if (status && status !== 'all') params.status = status
    if (crateName) params.crate_name = crateName
    if (taskName) params.task_name = taskName
    const response = await api.get('/jobs', { params })
    return response.data
  },

  async getJob(jobId) {
    const response = await api.get(`/jobs/${jobId}`)
    return response.data
  },

  async cancelJob(jobId) {
    const response = await api.post(`/jobs/${jobId}/cancel`)
    return response.data
  },

  async deleteJob(jobId) {
    const response = await api.delete(`/jobs/${jobId}`)
    return response.data
  },

  async retryJob(jobId) {
    const response = await api.post(`/jobs/${jobId}/retry`)
    return response.data
  },

  async batchRetry(jobIds) {
    const response = await api.post('/jobs/batch-retry', { job_ids: jobIds })
    return response.data
  },

  async batchDelete(jobIds) {
    const response = await api.post('/jobs/batch-delete', { job_ids: jobIds })
    return response.data
  },

  async batchSetPriority(jobIds, priority) {
    const response = await api.post('/jobs/batch-priority', {
      job_ids: jobIds,
      priority,
    })
    return response.data
  },

  async batchCancel(jobIds) {
    const response = await api.post('/jobs/batch-cancel', { job_ids: jobIds })
    return response.data
  },

  async getQueue() {
    const response = await api.get('/queue')
    return response.data
  },

  // Job file / artifact endpoints
  async getJobFiles(jobId) {
    const response = await api.get(`/jobs/${jobId}/files`)
    return response.data
  },

  async getJobFile(jobId, path, { lines, raw } = {}) {
    const params = {}
    if (lines !== undefined && lines !== null) {
      params.lines = lines
    }
    if (raw !== undefined && raw !== null) {
      params.raw = raw
    }
    const config = { params }
    if (raw) {
      // raw=true returns text/plain; keep it as a string.
      config.responseType = 'text'
    }
    const response = await api.get(
      `/jobs/${jobId}/files/${encodePath(path)}`,
      config
    )
    return response.data
  },

  async getJobArtifacts(jobId) {
    const response = await api.get(`/jobs/${jobId}/artifacts`)
    return response.data
  },

  // Relative download URL for an artifact (for <a :href>).
  artifactUrl(jobId, path) {
    return `/api/jobs/${jobId}/artifacts/${encodePath(path)}`
  },

  // Task definition endpoints
  async getTasks() {
    const response = await api.get('/tasks')
    return response.data
  },

  async getTask(taskId) {
    const response = await api.get(`/tasks/${taskId}`)
    return response.data
  },

  // Upload a task definition zip (admin token via interceptor).
  async uploadTask(file) {
    const form = new FormData()
    form.append('file', file)
    const response = await api.post('/tasks', form)
    return response.data
  },

  // Replace a task definition's zip (admin token via interceptor).
  async updateTask(taskId, file) {
    const form = new FormData()
    form.append('file', file)
    const response = await api.put(`/tasks/${taskId}`, form)
    return response.data
  },

  async deleteTask(taskId) {
    const response = await api.delete(`/tasks/${taskId}`)
    return response.data
  },

  // Raw task.toml text stored inside a task definition's zip.
  async getTaskToml(taskId) {
    const response = await api.get(`/tasks/${taskId}/task-toml`, {
      responseType: 'text',
    })
    return response.data
  },

  // File and directory metadata stored inside a task definition's zip.
  async getTaskFiles(taskId) {
    const response = await api.get(`/tasks/${taskId}/files`)
    return response.data
  },

  // Relative download URL for a task definition zip (for <a :href>).
  taskZipUrl(taskId) {
    return `/api/tasks/${taskId}/zip`
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
}
