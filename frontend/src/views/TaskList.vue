<script setup>
import { ref, computed, watch, onMounted, onUnmounted, shallowRef } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { RecycleScroller } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import api from '../services/api'
import websocket from '../services/websocket'
import RunnerIdBadge from '../components/RunnerIdBadge.vue'
import { filterTasksByCrateName } from './taskListFilters'

const router = useRouter()
const route = useRoute()
const tasks = ref([])
const loading = ref(true)
const error = ref(null)
const filterStatus = ref('all')
const searchCrateName = ref('')
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

  result = filterTasksByCrateName(result, searchCrateName.value)

  // Sort
  result = [...result].sort((a, b) => {
    let aVal, bVal

    // Handle runtime and compile_failed columns specially
    if (sortColumn.value === 'runtime') {
      aVal = getRuntimeSeconds(a.started_at, a.finished_at)
      bVal = getRuntimeSeconds(b.started_at, b.finished_at)
    } else if (sortColumn.value === 'compile_failed') {
      aVal = normalizeCompileFailed(a.compile_failed)
      bVal = normalizeCompileFailed(b.compile_failed)

      const aUnknown = aVal === null
      const bUnknown = bVal === null
      if (aUnknown && bUnknown) return 0
      if (aUnknown) return 1
      if (bUnknown) return -1

      if (aVal < bVal) return sortDirection.value === 'asc' ? -1 : 1
      if (aVal > bVal) return sortDirection.value === 'asc' ? 1 : -1
      return 0
    } else {
      aVal = a[sortColumn.value]
      bVal = b[sortColumn.value]
    }

    // Handle null values
    if (aVal === null || aVal === undefined) return 1
    if (bVal === null || bVal === undefined) return -1

    // Compare
    if (aVal < bVal) return sortDirection.value === 'asc' ? -1 : 1
    if (aVal > bVal) return sortDirection.value === 'asc' ? 1 : -1
    return 0
  })

  // Add index for stable keys in virtual scroller
  return result.map((task, index) => ({ ...task, _index: index }))
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

// Update URL when filter changes
watch(filterStatus, (newValue) => {
  selectedIds.value = new Set()
  router.replace({
    query: newValue === 'all' ? {} : { status: newValue }
  })
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

function getRuntimeSeconds(startStr, endStr) {
  if (!startStr) return null
  const start = new Date(startStr)
  const end = endStr ? new Date(endStr) : new Date()
  return Math.floor((end - start) / 1000)
}

function normalizeCompileFailed(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  if (typeof value === 'string' && /^-?\d+$/.test(value.trim())) {
    return Number(value)
  }

  return null
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
  websocket.connect('/ws/dashboard')
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
      <div class="flex items-center gap-4">
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

        <label for="crate-search" class="text-sm font-medium text-gray-700">Search crate:</label>
        <input
          id="crate-search"
          v-model="searchCrateName"
          type="text"
          placeholder="e.g. serde"
          class="w-56 px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
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
      <div class="table-header bg-gray-50 sticky top-0 z-10 border-b border-gray-200">
        <div class="header-row flex items-center">
          <div class="px-4 py-3 w-12">
            <input
              type="checkbox"
              :checked="allSelected"
              :indeterminate="someSelected"
              @change="toggleSelectAll"
              class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            />
          </div>
          <div
            @click="sortBy('id')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 w-16"
          >
            ID
            <span v-if="sortColumn === 'id'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
          </div>
          <div
            @click="sortBy('crate_name')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 flex-1 min-w-0"
          >
            Crate
            <span v-if="sortColumn === 'crate_name'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
          </div>
          <div
            @click="sortBy('version')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 w-24"
          >
            Version
            <span v-if="sortColumn === 'version'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
          </div>
          <div
            @click="sortBy('status')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 w-24"
          >
            Status
            <span v-if="sortColumn === 'status'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
          </div>
          <div
            @click="sortBy('case_count')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 w-20"
          >
            Cases
            <span v-if="sortColumn === 'case_count'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
          </div>
          <div
            @click="sortBy('poc_count')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 w-20"
          >
            POCs
            <span v-if="sortColumn === 'poc_count'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
          </div>
          <div
            @click="sortBy('compile_failed')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 w-28"
          >
            Compile Failed
            <span v-if="sortColumn === 'compile_failed'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
          </div>
          <div
            @click="sortBy('runtime')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 w-28"
          >
            Runtime
            <span v-if="sortColumn === 'runtime'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
          </div>
          <div
            @click="sortBy('runner_id')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 w-40"
          >
            Runner
            <span v-if="sortColumn === 'runner_id'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
          </div>
        </div>
      </div>
      <RecycleScroller
        class="scroller"
        :items="filteredAndSortedTasks"
        :item-size="53"
        key-field="id"
        v-slot="{ item: task }"
      >
        <div
          class="task-row flex items-center hover:bg-gray-50 transition-colors border-b border-gray-200"
          :class="selectedIds.has(task.id) ? 'bg-blue-50' : ''"
          :style="{ height: '53px' }"
        >
          <div class="px-4 py-3 whitespace-nowrap w-12">
            <input
              v-if="task.status !== 'running'"
              type="checkbox"
              :checked="selectedIds.has(task.id)"
              @change="toggleSelect(task.id)"
              @click.stop
              class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            />
            <span v-else class="inline-block h-4 w-4"></span>
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 cursor-pointer w-16" @click="viewTask(task.id)">
            #{{ task.id }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900 cursor-pointer flex-1 min-w-0 truncate" @click="viewTask(task.id)">
            {{ task.crate_name }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 cursor-pointer w-24" @click="viewTask(task.id)">
            {{ task.version }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap cursor-pointer w-24" @click="viewTask(task.id)">
            <span :class="['status-badge', `status-${task.status}`]">
              {{ task.status }}
            </span>
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 cursor-pointer w-20" @click="viewTask(task.id)">
            {{ task.case_count }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 cursor-pointer w-20" @click="viewTask(task.id)">
            {{ task.poc_count }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 cursor-pointer w-28" @click="viewTask(task.id)">
            {{ task.compile_failed ?? '-' }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 cursor-pointer w-28" @click="viewTask(task.id)">
            {{ formatDuration(task.started_at, task.finished_at) }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap cursor-pointer w-40" @click="viewTask(task.id)">
            <RunnerIdBadge :runner-id="task.runner_id || ''" />
          </div>
        </div>
      </RecycleScroller>
    </div>
  </div>
</template>

<style scoped>
.bento-card {
  width: 100%;
}

.table-header {
  width: 100%;
}

.header-row {
  display: flex;
  align-items: center;
  width: 100%;
}

.scroller {
  height: 600px;
  width: 100%;
}

.task-row {
  display: flex;
  align-items: center;
  width: 100%;
}

:deep(.vue-recycle-scroller__item-wrapper) {
  overflow-x: hidden;
  width: 100%;
}

:deep(.vue-recycle-scroller) {
  width: 100%;
}
</style>
