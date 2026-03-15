<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
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
const selectedIds = ref(new Set())
const batchLoading = ref(false)

const statusOptions = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'timeout', label: 'Timeout' },
  { value: 'oom', label: 'OOM' }
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

// Selectable tasks (exclude running)
const selectableTasks = computed(() => {
  return filteredAndSortedTasks.value.filter(t => t.status !== 'running')
})

const allSelected = computed(() => {
  return selectableTasks.value.length > 0 && selectableTasks.value.every(t => selectedIds.value.has(t.id))
})

const someSelected = computed(() => {
  return selectedIds.value.size > 0 && !allSelected.value
})

// Clear selection when filter changes
watch(filterStatus, () => {
  selectedIds.value = new Set()
})

function toggleSelectAll() {
  if (allSelected.value) {
    selectedIds.value = new Set()
  } else {
    selectedIds.value = new Set(selectableTasks.value.map(t => t.id))
  }
}

function toggleSelect(taskId) {
  const next = new Set(selectedIds.value)
  if (next.has(taskId)) {
    next.delete(taskId)
  } else {
    next.add(taskId)
  }
  selectedIds.value = next
}

async function fetchTasks() {
  try {
    tasks.value = await api.getAllTasks()
    loading.value = false
    // Remove selected ids that no longer exist
    const existingIds = new Set(tasks.value.map(t => t.id))
    const cleaned = new Set([...selectedIds.value].filter(id => existingIds.has(id)))
    selectedIds.value = cleaned
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

async function handleBatchRetry() {
  const ids = [...selectedIds.value]
  if (ids.length === 0) return
  if (!confirm(`Retry ${ids.length} selected task(s)?`)) return

  batchLoading.value = true
  try {
    const result = await api.batchRetry(ids)
    selectedIds.value = new Set()
    fetchTasks()
    if (result.skipped?.length > 0) {
      alert(`Retried ${result.retried.length} task(s). Skipped ${result.skipped.length} running task(s).`)
    }
  } catch (err) {
    alert(`Batch retry failed: ${err.message}`)
  } finally {
    batchLoading.value = false
  }
}

async function handleBatchDelete() {
  const ids = [...selectedIds.value]
  if (ids.length === 0) return
  if (!confirm(`Delete ${ids.length} selected task(s)? This cannot be undone.`)) return

  batchLoading.value = true
  try {
    const result = await api.batchDelete(ids)
    selectedIds.value = new Set()
    fetchTasks()
    if (result.skipped?.length > 0) {
      alert(`Deleted ${result.deleted.length} task(s). Skipped ${result.skipped.length} running task(s).`)
    }
  } catch (err) {
    alert(`Batch delete failed: ${err.message}`)
  } finally {
    batchLoading.value = false
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
          Batch Create
        </router-link>
        <router-link
          to="/tasks/new"
          class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors"
        >
          + New Task
        </router-link>
      </div>
    </div>

    <!-- Filter + Batch Actions -->
    <div class="mb-6 flex items-center justify-between">
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

      <!-- Batch action toolbar -->
      <div v-if="selectedIds.size > 0" class="flex items-center gap-3">
        <span class="text-sm text-gray-600">{{ selectedIds.size }} selected</span>
        <button
          @click="handleBatchRetry"
          :disabled="batchLoading"
          class="px-3 py-1.5 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
        >
          Retry Selected
        </button>
        <button
          @click="handleBatchDelete"
          :disabled="batchLoading"
          class="px-3 py-1.5 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
        >
          Delete Selected
        </button>
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
            <th class="px-4 py-3 text-left">
              <input
                type="checkbox"
                :checked="allSelected"
                :indeterminate="someSelected"
                @change="toggleSelectAll"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </th>
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
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-200">
          <tr
            v-for="task in filteredAndSortedTasks"
            :key="task.id"
            :class="['hover:bg-gray-50 transition-colors', selectedIds.has(task.id) ? 'bg-blue-50' : '']"
          >
            <td class="px-4 py-3 whitespace-nowrap">
              <input
                v-if="task.status !== 'running'"
                type="checkbox"
                :checked="selectedIds.has(task.id)"
                @change="toggleSelect(task.id)"
                @click.stop
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span v-else class="inline-block h-4 w-4"></span>
            </td>
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
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
