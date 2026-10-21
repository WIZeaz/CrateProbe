<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { RecycleScroller } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import api from '../services/api'
import websocket from '../services/websocket'
import RunnerIdBadge from '../components/RunnerIdBadge.vue'
import { filterTasksByCrateName } from './taskListFilters'
import { exportJobsToXlsx } from '../utils/exportXlsx'
import { useResizableColumns } from '../composables/useResizableColumns'

const router = useRouter()
const route = useRoute()
const jobs = ref([])
const loading = ref(true)
const error = ref(null)
const filterStatus = ref('all')
const searchCrateName = ref('')
const sortColumn = ref('created_at')
const sortDirection = ref('desc')
const selectedIds = ref(new Set())
const batchLoading = ref(false)

const columns = [
  { key: 'checkbox', width: 48, resizable: false },
  { key: 'id', width: 64, minWidth: 50 },
  { key: 'crate_name', width: 260, minWidth: 120 },
  { key: 'version', width: 96, minWidth: 70 },
  { key: 'task_name', width: 160, minWidth: 100 },
  { key: 'status', width: 96, minWidth: 80 },
  { key: 'started_at', width: 176, minWidth: 120 },
  { key: 'runtime', width: 112, minWidth: 80 },
  { key: 'runner_id', width: 160, minWidth: 100 },
]

const { columnWidths, startResize } = useResizableColumns(
  columns,
  'job-list-column-widths'
)

const totalWidth = computed(() => {
  return Object.values(columnWidths.value).reduce((sum, width) => sum + width, 0)
})

const statusOptions = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'timeout', label: 'Timeout' },
  { value: 'oom', label: 'OOM' },
  { value: 'runner_failed', label: 'Runner Failed' }
]

const filteredAndSortedJobs = computed(() => {
  let result = jobs.value

  // Filter
  if (filterStatus.value !== 'all') {
    result = result.filter(job => job.status === filterStatus.value)
  }

  result = filterTasksByCrateName(result, searchCrateName.value)

  // Sort
  result = [...result].sort((a, b) => {
    let aVal, bVal

    // Handle runtime column specially
    if (sortColumn.value === 'runtime') {
      aVal = getRuntimeSeconds(a.started_at, a.finished_at)
      bVal = getRuntimeSeconds(b.started_at, b.finished_at)
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
  return result.map((job, index) => ({ ...job, _index: index }))
})

// Selectable jobs (exclude running)
const selectableJobs = computed(() => {
  return filteredAndSortedJobs.value.filter(j => j.status !== 'running')
})

const allSelected = computed(() => {
  return selectableJobs.value.length > 0 && selectableJobs.value.every(j => selectedIds.value.has(j.id))
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
    selectedIds.value = new Set(selectableJobs.value.map(j => j.id))
  }
}

function toggleSelect(jobId) {
  const next = new Set(selectedIds.value)
  if (next.has(jobId)) {
    next.delete(jobId)
  } else {
    next.add(jobId)
  }
  selectedIds.value = next
}

async function fetchJobs() {
  try {
    jobs.value = await api.getJobs()
    loading.value = false
    // Remove selected ids that no longer exist
    const existingIds = new Set(jobs.value.map(j => j.id))
    const cleaned = new Set([...selectedIds.value].filter(id => existingIds.has(id)))
    selectedIds.value = cleaned
  } catch (err) {
    error.value = err.message
    loading.value = false
  }
}

function handleJobUpdate() {
  fetchJobs()
}

function viewJob(jobId) {
  router.push(`/jobs/${jobId}`)
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

function getRuntimeSeconds(startStr, endStr) {
  if (!startStr) return null
  const start = new Date(startStr)
  const end = endStr ? new Date(endStr) : new Date()
  return Math.floor((end - start) / 1000)
}

async function handleBatchRetry() {
  const ids = [...selectedIds.value]
  if (ids.length === 0) return
  if (!confirm(`Retry ${ids.length} selected job(s)?`)) return

  batchLoading.value = true
  try {
    const result = await api.batchRetry(ids)
    selectedIds.value = new Set()
    fetchJobs()
    if (result.skipped?.length > 0) {
      alert(`Retried ${result.retried.length} job(s). Skipped ${result.skipped.length} running job(s).`)
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
  if (!confirm(`Delete ${ids.length} selected job(s)? This cannot be undone.`)) return

  batchLoading.value = true
  try {
    const result = await api.batchDelete(ids)
    selectedIds.value = new Set()
    fetchJobs()
    if (result.skipped?.length > 0) {
      alert(`Deleted ${result.deleted.length} job(s). Skipped ${result.skipped.length} running job(s).`)
    }
  } catch (err) {
    alert(`Batch delete failed: ${err.message}`)
  } finally {
    batchLoading.value = false
  }
}

function handleExportXlsx() {
  if (jobs.value.length === 0) {
    alert('No jobs to export')
    return
  }
  try {
    exportJobsToXlsx(jobs.value)
  } catch (err) {
    alert(`Export failed: ${err.message}`)
  }
}

onMounted(() => {
  // Parse URL query parameter for status filter
  const statusParam = route.query.status
  if (statusParam && statusOptions.find(opt => opt.value === statusParam)) {
    filterStatus.value = statusParam
  }

  fetchJobs()
  websocket.connect('/ws/dashboard')
  websocket.on('job_update', handleJobUpdate)
  websocket.on('job_created', handleJobUpdate)
  websocket.on('job_completed', handleJobUpdate)
})

onUnmounted(() => {
  websocket.off('job_update', handleJobUpdate)
  websocket.off('job_created', handleJobUpdate)
  websocket.off('job_completed', handleJobUpdate)
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Jobs</h1>
      <div class="flex gap-3">
        <button
          @click="handleExportXlsx"
          :disabled="loading || jobs.length === 0"
          class="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          Export XLSX
        </button>
        <router-link
          to="/jobs/batch"
          class="px-4 py-2 text-sm font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
        >
          Batch Create
        </router-link>
        <router-link
          to="/jobs/new"
          class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors"
        >
          + New Job
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
      Failed to load jobs: {{ error }}
    </div>

    <div v-else-if="filteredAndSortedJobs.length === 0" class="bento-card text-center py-12">
      <p class="text-gray-500">No jobs found. Create your first job to get started!</p>
      <router-link
        to="/jobs/new"
        class="mt-4 inline-block px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors"
      >
        Create Job
      </router-link>
    </div>

    <div v-else class="bento-card overflow-x-auto">
      <div :style="{ minWidth: totalWidth + 'px' }">
        <div class="table-header bg-gray-50 sticky top-0 z-10 border-b border-gray-200">
        <div class="header-row flex items-center">
          <div class="px-4 py-3 relative" :style="{ width: columnWidths.checkbox + 'px' }">
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
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 relative overflow-hidden whitespace-nowrap"
            :style="{ width: columnWidths.id + 'px' }"
          >
            ID
            <span v-if="sortColumn === 'id'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            <span
              class="resize-handle absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400 z-10"
              @pointerdown.stop="startResize('id', $event)"
            ></span>
          </div>
          <div
            @click="sortBy('crate_name')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 relative overflow-hidden whitespace-nowrap"
            :style="{ width: columnWidths.crate_name + 'px' }"
          >
            Crate
            <span v-if="sortColumn === 'crate_name'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            <span
              class="resize-handle absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400 z-10"
              @pointerdown.stop="startResize('crate_name', $event)"
            ></span>
          </div>
          <div
            @click="sortBy('version')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 relative overflow-hidden whitespace-nowrap"
            :style="{ width: columnWidths.version + 'px' }"
          >
            Version
            <span v-if="sortColumn === 'version'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            <span
              class="resize-handle absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400 z-10"
              @pointerdown.stop="startResize('version', $event)"
            ></span>
          </div>
          <div
            @click="sortBy('task_name')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 relative overflow-hidden whitespace-nowrap"
            :style="{ width: columnWidths.task_name + 'px' }"
          >
            Task
            <span v-if="sortColumn === 'task_name'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            <span
              class="resize-handle absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400 z-10"
              @pointerdown.stop="startResize('task_name', $event)"
            ></span>
          </div>
          <div
            @click="sortBy('status')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 relative overflow-hidden whitespace-nowrap"
            :style="{ width: columnWidths.status + 'px' }"
          >
            Status
            <span v-if="sortColumn === 'status'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            <span
              class="resize-handle absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400 z-10"
              @pointerdown.stop="startResize('status', $event)"
            ></span>
          </div>
          <div
            @click="sortBy('started_at')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 relative overflow-hidden whitespace-nowrap"
            :style="{ width: columnWidths.started_at + 'px' }"
          >
            Started At
            <span v-if="sortColumn === 'started_at'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            <span
              class="resize-handle absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400 z-10"
              @pointerdown.stop="startResize('started_at', $event)"
            ></span>
          </div>
          <div
            @click="sortBy('runtime')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 relative overflow-hidden whitespace-nowrap"
            :style="{ width: columnWidths.runtime + 'px' }"
          >
            Runtime
            <span v-if="sortColumn === 'runtime'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            <span
              class="resize-handle absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400 z-10"
              @pointerdown.stop="startResize('runtime', $event)"
            ></span>
          </div>
          <div
            @click="sortBy('runner_id')"
            class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 relative overflow-hidden whitespace-nowrap"
            :style="{ width: columnWidths.runner_id + 'px' }"
          >
            Runner
            <span v-if="sortColumn === 'runner_id'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            <span
              class="resize-handle absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400 z-10"
              @pointerdown.stop="startResize('runner_id', $event)"
            ></span>
          </div>
        </div>
      </div>
      <RecycleScroller
        class="scroller"
        :items="filteredAndSortedJobs"
        :item-size="53"
        key-field="id"
        v-slot="{ item: job }"
      >
        <div
          class="job-row flex items-center hover:bg-gray-50 transition-colors border-b border-gray-200"
          :class="selectedIds.has(job.id) ? 'bg-blue-50' : ''"
          :style="{ height: '53px' }"
        >
          <div class="px-4 py-3 whitespace-nowrap" :style="{ width: columnWidths.checkbox + 'px' }">
            <input
              v-if="job.status !== 'running'"
              type="checkbox"
              :checked="selectedIds.has(job.id)"
              @change="toggleSelect(job.id)"
              @click.stop
              class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            />
            <span v-else class="inline-block h-4 w-4"></span>
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 cursor-pointer overflow-hidden" :style="{ width: columnWidths.id + 'px' }" @click="viewJob(job.id)">
            #{{ job.id }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900 cursor-pointer truncate" :style="{ width: columnWidths.crate_name + 'px' }" @click="viewJob(job.id)">
            {{ job.crate_name }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 cursor-pointer overflow-hidden" :style="{ width: columnWidths.version + 'px' }" @click="viewJob(job.id)">
            {{ job.version }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 cursor-pointer truncate" :style="{ width: columnWidths.task_name + 'px' }" @click="viewJob(job.id)">
            {{ job.task_name || '-' }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap cursor-pointer overflow-hidden" :style="{ width: columnWidths.status + 'px' }" @click="viewJob(job.id)">
            <span :class="['status-badge', `status-${job.status}`]">
              {{ job.status }}
            </span>
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 cursor-pointer overflow-hidden" :style="{ width: columnWidths.started_at + 'px' }" @click="viewJob(job.id)">
            {{ formatDate(job.started_at) }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 cursor-pointer overflow-hidden" :style="{ width: columnWidths.runtime + 'px' }" @click="viewJob(job.id)">
            {{ formatDuration(job.started_at, job.finished_at) }}
          </div>
          <div class="px-4 py-3 whitespace-nowrap cursor-pointer overflow-hidden" :style="{ width: columnWidths.runner_id + 'px' }" @click="viewJob(job.id)">
            <RunnerIdBadge :runner-id="job.runner_id || ''" />
          </div>
        </div>
      </RecycleScroller>
      </div>
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

.job-row {
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

.resize-handle {
  transition: background-color 0.15s ease;
}
</style>
