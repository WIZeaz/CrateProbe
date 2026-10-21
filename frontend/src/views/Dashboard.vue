<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'
import StatCard from '../components/StatCard.vue'
import RunnerMonitorPanel from '../components/RunnerMonitorPanel.vue'

const router = useRouter()
const dashboard = ref({
  total_jobs: 0,
  running_jobs: 0,
  completed_jobs: 0,
  failed_jobs: 0,
  recent_jobs: []
})
const loading = ref(true)
const error = ref(null)
const runnerOverview = ref([])
const runnerLoading = ref(true)
const runnerError = ref('')
let refreshInterval = null

async function fetchDashboard(isRefresh = false) {
  try {
    // Fetch dashboard stats and the 10 most recent jobs
    const [stats, recent] = await Promise.all([
      api.getDashboardStats(),
      api.getJobs({ pageSize: 10 }),
    ])

    // Map backend field names to frontend expected format
    dashboard.value = {
      total_jobs: stats.total,
      running_jobs: stats.running,
      completed_jobs: stats.completed,
      failed_jobs: stats.failed,
      pending_jobs: stats.pending,
      cancelled_jobs: stats.cancelled,
      timeout_jobs: stats.timeout,
      oom_jobs: stats.oom,
      runner_failed_jobs: stats.runner_failed,
      recent_jobs: recent.items
    }
    // Only hide loading spinner after initial load
    if (!isRefresh) {
      loading.value = false
    }
  } catch (err) {
    error.value = err.message
    if (!isRefresh) {
      loading.value = false
    }
  }
}

async function fetchRunnerOverview(isRefresh = false) {
  if (!isRefresh) {
    runnerLoading.value = true
  }
  runnerError.value = ''

  try {
    runnerOverview.value = await api.getRunnerOverview()
  } catch (err) {
    if (err.response?.status === 403) {
      runnerError.value = 'Admin token is missing or invalid. Please update it in Settings.'
    } else {
      runnerError.value = err.response?.data?.detail || err.message || 'Failed to load runner overview.'
    }
  } finally {
    if (!isRefresh) {
      runnerLoading.value = false
    }
  }
}

function handleJobUpdate(data) {
  // Refresh dashboard on job updates (non-intrusive)
  fetchDashboard(true)
}

function viewJob(jobId) {
  router.push(`/jobs/${jobId}`)
}

function formatDuration(startStr, endStr) {
  if (!startStr) return 'N/A'
  const start = new Date(startStr)
  const end = endStr ? new Date(endStr) : new Date()
  const diff = Math.floor((end - start) / 1000) // seconds

  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`
  const hours = Math.floor(diff / 3600)
  const minutes = Math.floor((diff % 3600) / 60)
  return `${hours}h ${minutes}m`
}

function navigateToJobs(status = 'all') {
  if (status === 'all') {
    router.push('/jobs')
  } else {
    router.push(`/jobs?status=${status}`)
  }
}

onMounted(() => {
  fetchDashboard()
  fetchRunnerOverview()
  websocket.connect('/ws/dashboard')
  websocket.on('job_update', handleJobUpdate)
  websocket.on('job_created', handleJobUpdate)
  websocket.on('job_completed', handleJobUpdate)

  // Auto-refresh every 10 seconds (non-intrusive)
  refreshInterval = setInterval(() => {
    fetchDashboard(true)
    fetchRunnerOverview(true)
  }, 10000)
})

onUnmounted(() => {
  websocket.off('job_update', handleJobUpdate)
  websocket.off('job_created', handleJobUpdate)
  websocket.off('job_completed', handleJobUpdate)

  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
})
</script>

<template>
  <div>
    <h1 class="text-3xl font-bold text-gray-900 mb-8">Dashboard</h1>

    <div v-if="loading" class="flex justify-center py-12">
      <div class="spinner border-blue-500"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg">
      Failed to load dashboard: {{ error }}
    </div>

    <div v-else>
      <!-- Stats Grid -->
      <div class="bento-grid grid-cols-1 md:grid-cols-2 lg:grid-cols-6 mb-8">
        <StatCard
          title="Total Jobs"
          :value="dashboard.total_jobs"
          icon="📊"
          color="blue"
          :clickable="true"
          @click="navigateToJobs('all')"
        />
        <StatCard
          title="Running"
          :value="dashboard.running_jobs"
          icon="▶️"
          color="yellow"
          :clickable="true"
          @click="navigateToJobs('running')"
        />
        <StatCard
          title="Completed"
          :value="dashboard.completed_jobs"
          icon="✅"
          color="green"
          :clickable="true"
          @click="navigateToJobs('completed')"
        />
        <StatCard
          title="Failed"
          :value="dashboard.failed_jobs"
          icon="❌"
          color="red"
          :clickable="true"
          @click="navigateToJobs('failed')"
        />
        <StatCard
          title="Pending"
          :value="dashboard.pending_jobs || 0"
          icon="⏳"
          color="purple"
          :clickable="true"
          @click="navigateToJobs('pending')"
        />
        <StatCard
          title="Runner Failed"
          :value="dashboard.runner_failed_jobs || 0"
          icon="💥"
          color="red"
          :clickable="true"
          @click="navigateToJobs('runner_failed')"
        />
      </div>

      <!-- System Monitor and Recent Jobs -->
      <div class="bento-grid grid-cols-1 lg:grid-cols-3 gap-6">
        <!-- Runner Monitor -->
        <div class="lg:col-span-1">
          <RunnerMonitorPanel
            :runners="runnerOverview"
            :loading="runnerLoading"
            :error="runnerError"
          />
        </div>

        <!-- Recent Jobs -->
        <div class="lg:col-span-2">
          <div class="bento-card">
            <h3 class="text-lg font-semibold text-gray-900 mb-4">Recent Jobs</h3>

            <div v-if="dashboard.recent_jobs.length === 0" class="text-center py-8 text-gray-500">
              No jobs yet. Create your first job!
            </div>

            <div v-else class="overflow-x-auto">
              <table class="min-w-full divide-y divide-gray-200">
                <thead>
                  <tr>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Crate
                    </th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Task
                    </th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Status
                    </th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Runtime
                    </th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-200">
                  <tr
                    v-for="job in dashboard.recent_jobs"
                    :key="job.id"
                    @click="viewJob(job.id)"
                    class="hover:bg-gray-50 cursor-pointer transition-colors"
                  >
                    <td class="px-4 py-3 whitespace-nowrap">
                      <div class="text-sm font-medium text-gray-900">{{ job.crate_name }}</div>
                      <div class="text-sm text-gray-500">{{ job.version }}</div>
                    </td>
                    <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                      {{ job.task_name || '-' }}
                    </td>
                    <td class="px-4 py-3 whitespace-nowrap">
                      <span :class="['status-badge', `status-${job.status}`]">
                        {{ job.status }}
                      </span>
                    </td>
                    <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                      {{ formatDuration(job.started_at, job.finished_at) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div class="mt-4 text-center">
              <router-link
                to="/jobs"
                class="text-sm font-medium text-blue-600 hover:text-blue-500"
              >
                View all jobs →
              </router-link>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
