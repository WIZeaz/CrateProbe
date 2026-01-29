<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'

const router = useRouter()
const route = useRoute()
const tasks = ref([])
const loading = ref(true)
const error = ref(null)
const filterStatus = ref('all')
const sortColumn = ref('created_at')
const sortDirection = ref('desc')

const statusOptions = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' }
]

const filteredAndSortedTasks = computed(() => {
  let result = tasks.value

  // Filter
  if (filterStatus.value !== 'all') {
    result = result.filter(task => task.status === filterStatus.value)
  }

  // Sort
  result = [...result].sort((a, b) => {
    let aVal = a[sortColumn.value]
    let bVal = b[sortColumn.value]

    // Handle null values
    if (aVal === null) return 1
    if (bVal === null) return -1

    // Compare
    if (aVal < bVal) return sortDirection.value === 'asc' ? -1 : 1
    if (aVal > bVal) return sortDirection.value === 'asc' ? 1 : -1
    return 0
  })

  return result
})

async function fetchTasks() {
  try {
    tasks.value = await api.getAllTasks()
    loading.value = false
  } catch (err) {
    error.value = err.message
    loading.value = false
  }
}

function handleTaskUpdate() {
  fetchTasks()
}

function viewTask(taskId) {
  router.push(`/tasks/${taskId}`)
}

function sortBy(column) {
  if (sortColumn.value === column) {
    sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortColumn.value = column
    sortDirection.value = 'desc'
  }
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
  const diff = Math.floor((end - start) / 1000)

  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`
  const hours = Math.floor(diff / 3600)
  const minutes = Math.floor((diff % 3600) / 60)
  return `${hours}h ${minutes}m`
}

async function handleDelete(task) {
  if (!confirm(`Are you sure you want to delete task #${task.id} (${task.crate_name})?`)) {
    return
  }

  try {
    await api.deleteTask(task.id)
    // Remove from local list immediately for better UX
    tasks.value = tasks.value.filter(t => t.id !== task.id)
  } catch (err) {
    alert(`Failed to delete task: ${err.message}`)
    // Refresh to get accurate state
    fetchTasks()
  }
}

async function handleRetry(task) {
  if (!confirm(`重试任务 #${task.id} (${task.crate_name} ${task.version})?\n\n这将重置任务并重新执行。`)) {
    return
  }

  try {
    await api.retryTask(task.id)
    // Refresh task list to show updated status
    fetchTasks()
  } catch (err) {
    alert(`重试任务失败: ${err.message}`)
  }
}

onMounted(() => {
  // Parse URL query parameter for status filter
  const statusParam = route.query.status
  if (statusParam && statusOptions.find(opt => opt.value === statusParam)) {
    filterStatus.value = statusParam
  }

  fetchTasks()
  websocket.on('task_update', handleTaskUpdate)
  websocket.on('task_created', handleTaskUpdate)
  websocket.on('task_completed', handleTaskUpdate)
})

onUnmounted(() => {
  websocket.off('task_update', handleTaskUpdate)
  websocket.off('task_created', handleTaskUpdate)
  websocket.off('task_completed', handleTaskUpdate)
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Tasks</h1>
      <div class="flex gap-3">
        <router-link
          to="/tasks/batch"
          class="px-4 py-2 text-sm font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
        >
          📋 Batch Create
        </router-link>
        <router-link
          to="/tasks/new"
          class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors"
        >
          + New Task
        </router-link>
      </div>
    </div>

    <!-- Filter -->
    <div class="mb-6">
      <div class="flex items-center gap-2">
        <label for="filter" class="text-sm font-medium text-gray-700">Filter by status:</label>
        <select
          id="filter"
          v-model="filterStatus"
          class="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        >
          <option v-for="option in statusOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </div>
    </div>

    <div v-if="loading" class="flex justify-center py-12">
      <div class="spinner border-blue-500"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg">
      Failed to load tasks: {{ error }}
    </div>

    <div v-else-if="filteredAndSortedTasks.length === 0" class="bento-card text-center py-12">
      <p class="text-gray-500">No tasks found. Create your first task to get started!</p>
      <router-link
        to="/tasks/new"
        class="mt-4 inline-block px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors"
      >
        Create Task
      </router-link>
    </div>

    <div v-else class="bento-card overflow-x-auto">
      <table class="min-w-full divide-y divide-gray-200">
        <thead>
          <tr>
            <th
              @click="sortBy('id')"
              class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-50"
            >
              ID
              <span v-if="sortColumn === 'id'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            </th>
            <th
              @click="sortBy('crate_name')"
              class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-50"
            >
              Crate
              <span v-if="sortColumn === 'crate_name'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            </th>
            <th
              @click="sortBy('version')"
              class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-50"
            >
              Version
              <span v-if="sortColumn === 'version'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            </th>
            <th
              @click="sortBy('status')"
              class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-50"
            >
              Status
              <span v-if="sortColumn === 'status'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            </th>
            <th
              @click="sortBy('case_count')"
              class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-50"
            >
              Cases
              <span v-if="sortColumn === 'case_count'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            </th>
            <th
              @click="sortBy('poc_count')"
              class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-50"
            >
              POCs
              <span v-if="sortColumn === 'poc_count'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            </th>
            <th
              @click="sortBy('created_at')"
              class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-50"
            >
              Created
              <span v-if="sortColumn === 'created_at'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            </th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
              Runtime
            </th>
            <th class="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
              Actions
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-200">
          <tr
            v-for="task in filteredAndSortedTasks"
            :key="task.id"
            class="hover:bg-gray-50 transition-colors"
          >
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 cursor-pointer" @click="viewTask(task.id)">
              #{{ task.id }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900 cursor-pointer" @click="viewTask(task.id)">
              {{ task.crate_name }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 cursor-pointer" @click="viewTask(task.id)">
              {{ task.version }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap cursor-pointer" @click="viewTask(task.id)">
              <span :class="['status-badge', `status-${task.status}`]">
                {{ task.status }}
              </span>
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 cursor-pointer" @click="viewTask(task.id)">
              {{ task.case_count }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 cursor-pointer" @click="viewTask(task.id)">
              {{ task.poc_count }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 cursor-pointer" @click="viewTask(task.id)">
              {{ formatDate(task.created_at) }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 cursor-pointer" @click="viewTask(task.id)">
              {{ formatDuration(task.started_at, task.finished_at) }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-right text-sm font-medium">
              <div class="flex items-center justify-end gap-2">
                <button
                  v-if="task.status !== 'running'"
                  @click.stop="handleRetry(task)"
                  class="text-green-600 hover:text-green-900 transition-colors"
                  title="重试任务"
                >
                  🔄 Retry
                </button>
                <button
                  v-if="task.status !== 'running'"
                  @click.stop="handleDelete(task)"
                  class="text-red-600 hover:text-red-900 transition-colors"
                  title="删除任务"
                >
                  🗑️ Delete
                </button>
                <span v-if="task.status === 'running'" class="text-gray-400" title="任务运行中">
                  —
                </span>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
