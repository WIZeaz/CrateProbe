<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'
import StatCard from '../components/StatCard.vue'
import SystemMonitor from '../components/SystemMonitor.vue'

const router = useRouter()
const dashboard = ref({
  total_tasks: 0,
  running_tasks: 0,
  completed_tasks: 0,
  failed_tasks: 0,
  recent_tasks: []
})
const loading = ref(true)
const error = ref(null)
let refreshInterval = null

async function fetchDashboard(isRefresh = false) {
  try {
    // Fetch dashboard stats and tasks
    const [stats, tasks] = await Promise.all([
      api.getDashboardStats(),
      api.getAllTasks()
    ])

    // Map backend field names to frontend expected format
    dashboard.value = {
      total_tasks: stats.total,
      running_tasks: stats.running,
      completed_tasks: stats.completed,
      failed_tasks: stats.failed,
      pending_tasks: stats.pending,
      cancelled_tasks: stats.cancelled,
      timeout_tasks: stats.timeout,
      oom_tasks: stats.oom,
      recent_tasks: tasks.slice(0, 10) // Get 10 most recent tasks
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

function handleTaskUpdate(data) {
  // Refresh dashboard on task updates (non-intrusive)
  fetchDashboard(true)
}

function viewTask(taskId) {
  router.push(`/tasks/${taskId}`)
}

function formatDate(dateStr) {
  if (!dateStr) return 'N/A'
  const date = new Date(dateStr)
  return date.toLocaleString()
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

onMounted(() => {
  fetchDashboard()
  websocket.on('task_update', handleTaskUpdate)
  websocket.on('task_created', handleTaskUpdate)
  websocket.on('task_completed', handleTaskUpdate)

  // Auto-refresh every 5 seconds (non-intrusive)
  refreshInterval = setInterval(() => {
    fetchDashboard(true)
  }, 5000)
})

onUnmounted(() => {
  websocket.off('task_update', handleTaskUpdate)
  websocket.off('task_created', handleTaskUpdate)
  websocket.off('task_completed', handleTaskUpdate)

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
      <div class="bento-grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 mb-8">
        <StatCard
          title="Total Tasks"
          :value="dashboard.total_tasks"
          icon="📊"
          color="blue"
        />
        <StatCard
          title="Running"
          :value="dashboard.running_tasks"
          icon="▶️"
          color="yellow"
        />
        <StatCard
          title="Completed"
          :value="dashboard.completed_tasks"
          icon="✅"
          color="green"
        />
        <StatCard
          title="Failed"
          :value="dashboard.failed_tasks"
          icon="❌"
          color="red"
        />
      </div>

      <!-- System Monitor and Recent Tasks -->
      <div class="bento-grid grid-cols-1 lg:grid-cols-3 gap-6">
        <!-- System Monitor -->
        <div class="lg:col-span-1">
          <SystemMonitor />
        </div>

        <!-- Recent Tasks -->
        <div class="lg:col-span-2">
          <div class="bento-card">
            <h3 class="text-lg font-semibold text-gray-900 mb-4">Recent Tasks</h3>

            <div v-if="dashboard.recent_tasks.length === 0" class="text-center py-8 text-gray-500">
              No tasks yet. Create your first task!
            </div>

            <div v-else class="overflow-x-auto">
              <table class="min-w-full divide-y divide-gray-200">
                <thead>
                  <tr>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Crate
                    </th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Status
                    </th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Cases
                    </th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      POCs
                    </th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Runtime
                    </th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-200">
                  <tr
                    v-for="task in dashboard.recent_tasks"
                    :key="task.id"
                    @click="viewTask(task.id)"
                    class="hover:bg-gray-50 cursor-pointer transition-colors"
                  >
                    <td class="px-4 py-3 whitespace-nowrap">
                      <div class="text-sm font-medium text-gray-900">{{ task.crate_name }}</div>
                      <div class="text-sm text-gray-500">{{ task.version }}</div>
                    </td>
                    <td class="px-4 py-3 whitespace-nowrap">
                      <span :class="['status-badge', `status-${task.status}`]">
                        {{ task.status }}
                      </span>
                    </td>
                    <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">
                      {{ task.case_count }}
                    </td>
                    <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">
                      {{ task.poc_count }}
                    </td>
                    <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                      {{ formatDuration(task.started_at, task.finished_at) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div class="mt-4 text-center">
              <router-link
                to="/tasks"
                class="text-sm font-medium text-blue-600 hover:text-blue-500"
              >
                View all tasks →
              </router-link>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
