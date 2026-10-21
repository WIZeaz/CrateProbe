<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'
import RunnerIdBadge from '../components/RunnerIdBadge.vue'
import { exportJobsToXlsx } from '../utils/exportXlsx'

const router = useRouter()
const route = useRoute()

const PAGE_SIZE = 20

const jobs = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(true)
const error = ref(null)

const statusFilter = ref('all')
const crateSearch = ref('')
const taskSearch = ref('')
const sortOption = ref('created_at:desc')

const selectedIds = ref(new Set())
const batchLoading = ref(false)
const exporting = ref(false)

let searchTimer = null

const statusOptions = [
  { value: 'all', label: 'All statuses' },
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'timeout', label: 'Timeout' },
  { value: 'oom', label: 'OOM' },
  { value: 'runner_failed', label: 'Runner failed' },
]

const sortOptions = [
  { value: 'created_at:desc', label: 'Newest first' },
  { value: 'created_at:asc', label: 'Oldest first' },
  { value: 'crate_name:asc', label: 'Crate A → Z' },
  { value: 'crate_name:desc', label: 'Crate Z → A' },
  { value: 'runtime:desc', label: 'Longest runtime' },
  { value: 'runtime:asc', label: 'Shortest runtime' },
]

// Seed the status filter from the URL (e.g. /jobs?status=failed).
const statusParam = route.query.status
if (statusParam && statusOptions.find(opt => opt.value === statusParam)) {
  statusFilter.value = statusParam
}

const hasFilters = computed(
  () =>
    statusFilter.value !== 'all' ||
    crateSearch.value.trim() !== '' ||
    taskSearch.value.trim() !== ''
)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const rangeStart = computed(() =>
  total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1
)
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))

const selectableJobs = computed(() =>
  jobs.value.filter(job => job.status !== 'running')
)

const allSelected = computed(
  () =>
    selectableJobs.value.length > 0 &&
    selectableJobs.value.every(job => selectedIds.value.has(job.id))
)

const someSelected = computed(
  () => selectedIds.value.size > 0 && !allSelected.value
)

function queryParams() {
  const [sortBy, sortDir] = sortOption.value.split(':')
  return {
    page: page.value,
    pageSize: PAGE_SIZE,
    status: statusFilter.value === 'all' ? null : statusFilter.value,
    crateName: crateSearch.value.trim() || null,
    taskName: taskSearch.value.trim() || null,
    sortBy,
    sortDir,
  }
}

async function fetchJobs({ silent = false } = {}) {
  if (!silent) loading.value = true
  error.value = null
  try {
    const data = await api.getJobs(queryParams())
    jobs.value = data.items
    total.value = data.total

    // If the current page fell out of range (e.g. rows were deleted),
    // step back to the last page and refetch.
    if (jobs.value.length === 0 && total.value > 0 && page.value > 1) {
      page.value = Math.max(1, Math.ceil(total.value / PAGE_SIZE))
      return fetchJobs({ silent })
    }

    const existingIds = new Set(jobs.value.map(job => job.id))
    selectedIds.value = new Set(
      [...selectedIds.value].filter(id => existingIds.has(id))
    )
  } catch (err) {
    if (!silent) {
      error.value = err.response?.data?.detail || err.message
    }
  } finally {
    if (!silent) loading.value = false
  }
}

function resetAndFetch() {
  selectedIds.value = new Set()
  page.value = 1
  fetchJobs()
}

function scheduleSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(resetAndFetch, 300)
}

function clearFilters() {
  statusFilter.value = 'all'
  crateSearch.value = ''
  taskSearch.value = ''
  router.replace({ query: {} })
  resetAndFetch()
}

function goToPage(nextPage) {
  const clamped = Math.min(Math.max(1, nextPage), totalPages.value)
  if (clamped === page.value) return
  selectedIds.value = new Set()
  page.value = clamped
  fetchJobs()
}

function toggleSelectAll() {
  selectedIds.value = allSelected.value
    ? new Set()
    : new Set(selectableJobs.value.map(job => job.id))
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

function viewJob(jobId) {
  router.push(`/jobs/${jobId}`)
}

function formatDate(dateStr) {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleString()
}

function formatDuration(startStr, endStr) {
  if (!startStr) return '—'
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
  if (!confirm(`Retry ${ids.length} selected job(s)?`)) return

  batchLoading.value = true
  try {
    const result = await api.batchRetry(ids)
    selectedIds.value = new Set()
    fetchJobs({ silent: true })
    if (result.skipped?.length > 0) {
      alert(
        `Retried ${result.retried.length} job(s). Skipped ${result.skipped.length} job(s) (running or task deleted).`
      )
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
  if (!confirm(`Delete ${ids.length} selected job(s)? This cannot be undone.`))
    return

  batchLoading.value = true
  try {
    const result = await api.batchDelete(ids)
    selectedIds.value = new Set()
    fetchJobs()
    if (result.skipped?.length > 0) {
      alert(
        `Deleted ${result.deleted.length} job(s). Skipped ${result.skipped.length} running job(s).`
      )
    }
  } catch (err) {
    alert(`Batch delete failed: ${err.message}`)
  } finally {
    batchLoading.value = false
  }
}

async function handleExportXlsx() {
  exporting.value = true
  try {
    const [sortBy, sortDir] = sortOption.value.split(':')
    const data = await api.getJobs({
      ...queryParams(),
      page: 1,
      pageSize: 1000,
      sortBy,
      sortDir,
    })
    if (data.items.length === 0) {
      alert('No jobs to export')
      return
    }
    exportJobsToXlsx(data.items)
  } catch (err) {
    alert(`Export failed: ${err.message}`)
  } finally {
    exporting.value = false
  }
}

function handleJobUpdate() {
  fetchJobs({ silent: true })
}

function onStatusChange() {
  router.replace({
    query: statusFilter.value === 'all' ? {} : { status: statusFilter.value },
  })
  resetAndFetch()
}

onMounted(() => {
  fetchJobs()
  websocket.connect('/ws/dashboard')
  websocket.on('job_update', handleJobUpdate)
  websocket.on('job_created', handleJobUpdate)
  websocket.on('job_completed', handleJobUpdate)
})

onUnmounted(() => {
  clearTimeout(searchTimer)
  websocket.off('job_update', handleJobUpdate)
  websocket.off('job_created', handleJobUpdate)
  websocket.off('job_completed', handleJobUpdate)
})
</script>

<template>
  <div>
    <div class="flex flex-wrap items-center justify-between gap-3 mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Jobs</h1>
      <div class="flex gap-3">
        <button
          @click="handleExportXlsx"
          :disabled="exporting || loading || total === 0"
          class="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {{ exporting ? 'Exporting...' : 'Export XLSX' }}
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

    <!-- Compound filters -->
    <div class="mb-4 flex flex-wrap items-end gap-3">
      <div>
        <label for="status-filter" class="block text-xs font-medium text-gray-500 mb-1">
          Status
        </label>
        <select
          id="status-filter"
          v-model="statusFilter"
          @change="onStatusChange"
          class="w-40 px-3 py-1.5 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        >
          <option v-for="option in statusOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </div>

      <div>
        <label for="crate-search" class="block text-xs font-medium text-gray-500 mb-1">
          Crate
        </label>
        <input
          id="crate-search"
          v-model="crateSearch"
          @input="scheduleSearch"
          type="text"
          placeholder="Search crate name"
          class="w-52 px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      </div>

      <div>
        <label for="task-search" class="block text-xs font-medium text-gray-500 mb-1">
          Task
        </label>
        <input
          id="task-search"
          v-model="taskSearch"
          @input="scheduleSearch"
          type="text"
          placeholder="Search task name"
          class="w-44 px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      </div>

      <div>
        <label for="sort-select" class="block text-xs font-medium text-gray-500 mb-1">
          Sort
        </label>
        <select
          id="sort-select"
          v-model="sortOption"
          @change="resetAndFetch"
          class="w-44 px-3 py-1.5 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        >
          <option v-for="option in sortOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </div>

      <button
        v-if="hasFilters"
        @click="clearFilters"
        class="px-3 py-1.5 text-sm font-medium text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
      >
        Clear filters
      </button>
    </div>

    <!-- Selection + batch actions -->
    <div
      v-if="!loading && !error && jobs.length > 0"
      class="mb-3 flex flex-wrap items-center justify-between gap-3"
    >
      <label class="flex items-center gap-2 text-sm text-gray-600">
        <input
          type="checkbox"
          :checked="allSelected"
          :indeterminate="someSelected"
          @change="toggleSelectAll"
          class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
        />
        Select all on this page
      </label>
      <div v-if="selectedIds.size > 0" class="flex flex-wrap items-center gap-2">
        <span class="text-sm text-gray-600">{{ selectedIds.size }} selected</span>
        <button
          @click="handleBatchRetry"
          :disabled="batchLoading"
          class="px-3 py-1.5 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
        >
          Retry selected
        </button>
        <button
          @click="handleBatchDelete"
          :disabled="batchLoading"
          class="px-3 py-1.5 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
        >
          Delete selected
        </button>
      </div>
    </div>

    <div v-if="loading" class="flex justify-center py-12">
      <div class="spinner border-blue-500"></div>
    </div>

    <div
      v-else-if="error"
      class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg"
    >
      Failed to load jobs: {{ error }}
    </div>

    <div v-else-if="jobs.length === 0" class="bento-card text-center py-12">
      <p class="text-gray-900 font-medium">
        {{ hasFilters ? 'No jobs match these filters' : 'No jobs yet' }}
      </p>
      <p class="mt-1 text-sm text-gray-500">
        {{
          hasFilters
            ? 'Try a different crate name, task name or status.'
            : 'Create your first job to get started.'
        }}
      </p>
      <button
        v-if="hasFilters"
        @click="clearFilters"
        class="mt-4 px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
      >
        Clear filters
      </button>
      <router-link
        v-else
        to="/jobs/new"
        class="mt-4 inline-block px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors"
      >
        Create Job
      </router-link>
    </div>

    <template v-else>
      <div class="space-y-2">
        <article
          v-for="job in jobs"
          :key="job.id"
          :class="[
            'flex items-center gap-3 rounded-xl border bg-white px-4 py-3 cursor-pointer transition',
            selectedIds.has(job.id)
              ? 'border-blue-300 ring-1 ring-blue-200'
              : 'border-gray-200 hover:border-blue-300 hover:shadow-sm'
          ]"
          @click="viewJob(job.id)"
        >
          <input
            v-if="job.status !== 'running'"
            type="checkbox"
            :checked="selectedIds.has(job.id)"
            @change="toggleSelect(job.id)"
            @click.stop
            class="h-4 w-4 shrink-0 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          <span v-else class="inline-block h-4 w-4 shrink-0"></span>

          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span class="text-sm font-semibold text-gray-900 truncate">
                {{ job.crate_name }}
              </span>
              <span class="text-xs text-gray-500">v{{ job.version }}</span>
              <span :class="['status-badge', `status-${job.status}`]">
                {{ job.status }}
              </span>
            </div>
            <div class="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
              <span>#{{ job.id }}</span>
              <span class="truncate">Task: {{ job.task_name || '—' }}</span>
              <span class="flex items-center gap-1.5">
                Runner: <RunnerIdBadge :runner-id="job.runner_id || ''" />
              </span>
              <span>Started: {{ formatDate(job.started_at) }}</span>
              <span>Runtime: {{ formatDuration(job.started_at, job.finished_at) }}</span>
            </div>
          </div>

          <svg
            class="w-4 h-4 shrink-0 text-gray-300"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
            aria-hidden="true"
          >
            <path d="m6 3.5 4.5 4.5L6 12.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </article>
      </div>

      <!-- Pagination -->
      <div class="mt-4 flex flex-wrap items-center justify-between gap-3">
        <p class="text-sm text-gray-500">
          Showing {{ rangeStart }}–{{ rangeEnd }} of {{ total }} jobs
        </p>
        <div class="flex items-center gap-2">
          <button
            @click="goToPage(page - 1)"
            :disabled="page <= 1"
            class="px-3 py-1.5 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Previous
          </button>
          <span class="px-2 text-sm text-gray-600 tabular-nums">
            Page {{ page }} of {{ totalPages }}
          </span>
          <button
            @click="goToPage(page + 1)"
            :disabled="page >= totalPages"
            class="px-3 py-1.5 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Next
          </button>
        </div>
      </div>
    </template>
  </div>
</template>
