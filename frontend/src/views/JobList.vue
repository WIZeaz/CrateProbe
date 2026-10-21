<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'
import RunnerIdBadge from '../components/RunnerIdBadge.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import StatusMark from '../components/ui/StatusMark.vue'
import {
  formatCount,
  formatDateTime,
  formatDuration,
  formatRelativeTime,
} from '../utils/format.js'
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
  { value: 'crate_name:asc', label: 'Crate, A to Z' },
  { value: 'crate_name:desc', label: 'Crate, Z to A' },
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
    <PageHeader
      title="Jobs"
      description="Every run, filterable by crate and task."
    >
      <template #actions>
        <button
          class="btn btn--ghost"
          :disabled="exporting || loading || total === 0"
          @click="handleExportXlsx"
        >
          {{ exporting ? 'Exporting...' : 'Export XLSX' }}
        </button>
        <router-link to="/jobs/batch" class="btn btn--ghost">
          Batch create
        </router-link>
        <router-link to="/jobs/new" class="btn btn--primary">
          New job
        </router-link>
      </template>
    </PageHeader>

    <section class="instrument-panel p-3">
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <label for="status-filter" class="ui-label">Status</label>
          <select
            id="status-filter"
            v-model="statusFilter"
            class="ui-select ui-select--sm w-40"
            @change="onStatusChange"
          >
            <option
              v-for="option in statusOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </div>

        <div>
          <label for="crate-search" class="ui-label">Crate</label>
          <input
            id="crate-search"
            v-model="crateSearch"
            type="text"
            placeholder="Search crate name"
            class="ui-input ui-input--sm w-52"
            @input="scheduleSearch"
          />
        </div>

        <div>
          <label for="task-search" class="ui-label">Task</label>
          <input
            id="task-search"
            v-model="taskSearch"
            type="text"
            placeholder="Search task name"
            class="ui-input ui-input--sm w-44"
            @input="scheduleSearch"
          />
        </div>

        <div>
          <label for="sort-select" class="ui-label">Sort</label>
          <select
            id="sort-select"
            v-model="sortOption"
            class="ui-select ui-select--sm w-44"
            @change="resetAndFetch"
          >
            <option
              v-for="option in sortOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </div>

        <button
          v-if="hasFilters"
          class="btn btn--ghost btn--sm"
          @click="clearFilters"
        >
          Clear filters
        </button>
      </div>
    </section>

    <section class="instrument-panel mt-5">
      <div class="panel-header">
        <h2 class="panel-title">Jobs</h2>
        <span v-if="!loading && !error" class="font-mono text-[11px] text-muted">
          {{ formatCount(total) }}
        </span>
      </div>

      <Transition name="fade-slide">
        <div
          v-if="selectedIds.size > 0"
          class="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-[3px] border border-rule bg-black/[0.03] px-3 py-2"
        >
          <span class="font-mono text-[11px] text-muted">
            {{ selectedIds.size }} selected
          </span>
          <div class="flex flex-wrap items-center gap-2">
            <button
              class="btn btn--ghost btn--sm"
              :disabled="batchLoading"
              @click="handleBatchRetry"
            >
              Retry selected
            </button>
            <button
              class="btn btn--danger-ghost btn--sm"
              :disabled="batchLoading"
              @click="handleBatchDelete"
            >
              Delete selected
            </button>
          </div>
        </div>
      </Transition>

      <div
        v-if="loading"
        class="mt-4 space-y-3"
        aria-busy="true"
        aria-label="Loading jobs"
      >
        <div v-for="index in 5" :key="index" class="flex items-center gap-4">
          <div class="skeleton h-3 w-5"></div>
          <div class="skeleton h-3 w-20"></div>
          <div class="skeleton h-3 flex-1"></div>
          <div class="skeleton h-3 w-24"></div>
          <div class="skeleton h-3 w-12"></div>
        </div>
      </div>

      <div v-else-if="error" class="notice notice--error mt-4" role="alert">
        <div class="min-w-0 flex-1">
          <p class="notice__title">Couldn't load jobs.</p>
          <p class="notice__detail">{{ error }}</p>
        </div>
        <button class="btn btn--ghost btn--sm" @click="fetchJobs()">
          Try again
        </button>
      </div>

      <EmptyState
        v-else-if="jobs.length === 0"
        :title="hasFilters ? 'No jobs match these filters' : 'No jobs yet'"
        :description="
          hasFilters
            ? 'Try a different crate name, task name or status.'
            : 'Create your first job to start testing crates.'
        "
      >
        <button
          v-if="hasFilters"
          class="btn btn--ghost btn--sm"
          @click="clearFilters"
        >
          Clear filters
        </button>
        <router-link v-else to="/jobs/new" class="btn btn--primary">
          Create job
        </router-link>
      </EmptyState>

      <template v-else>
        <div class="mt-2 hidden overflow-x-auto lg:block">
          <table class="data-table">
            <thead>
              <tr>
                <th class="w-9">
                  <input
                    type="checkbox"
                    :checked="allSelected"
                    :indeterminate="someSelected"
                    :disabled="selectableJobs.length === 0"
                    aria-label="Select all selectable jobs"
                    @change="toggleSelectAll"
                  />
                </th>
                <th class="w-20">Job ID</th>
                <th class="w-28">Status</th>
                <th>Crate</th>
                <th class="w-36">Task</th>
                <th class="w-28">Runner</th>
                <th class="w-24 text-right">Started</th>
                <th class="w-20 text-right">Runtime</th>
                <th class="w-6"></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="job in jobs"
                :key="job.id"
                class="cursor-pointer"
                :class="{ 'is-selected': selectedIds.has(job.id) }"
                @click="viewJob(job.id)"
              >
                <td @click.stop>
                  <input
                    v-if="job.status !== 'running'"
                    type="checkbox"
                    :checked="selectedIds.has(job.id)"
                    :aria-label="`Select job ${job.id}`"
                    @change="toggleSelect(job.id)"
                  />
                  <span v-else class="inline-block h-4 w-4"></span>
                </td>
                <td class="numeric text-muted">#{{ job.id }}</td>
                <td><StatusMark :status="job.status" /></td>
                <td class="min-w-0">
                  <router-link
                    :to="`/jobs/${job.id}`"
                    class="flex min-w-0 items-baseline gap-1.5 text-[13px] font-medium text-ink hover:text-signal"
                    :title="`${job.crate_name} ${job.version}`"
                    @click.stop
                  >
                    <span class="truncate">{{ job.crate_name }}</span>
                    <span class="flex-none font-mono text-[11px] font-normal text-muted">
                      {{ job.version }}
                    </span>
                  </router-link>
                </td>
                <td>
                  <span
                    class="block max-w-[140px] truncate text-muted"
                    :title="job.task_name || ''"
                  >
                    {{ job.task_name || '—' }}
                  </span>
                </td>
                <td class="whitespace-nowrap">
                  <RunnerIdBadge :runner-id="job.runner_id || ''" />
                </td>
                <td
                  class="numeric text-right text-muted"
                  :title="formatDateTime(job.started_at)"
                >
                  {{ formatRelativeTime(job.started_at) }}
                </td>
                <td class="numeric text-right">
                  {{ formatDuration(job.started_at, job.finished_at) }}
                </td>
                <td class="text-muted">
                  <svg
                    class="h-3.5 w-3.5"
                    viewBox="0 0 16 16"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                    aria-hidden="true"
                  >
                    <path
                      d="m6 3.5 4.5 4.5L6 12.5"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="lg:hidden">
          <div
            v-if="selectableJobs.length > 0"
            class="flex items-center justify-between border-t border-rule py-2"
          >
            <label class="flex items-center gap-2 text-[11px] font-medium text-ink">
              <input
                type="checkbox"
                :checked="allSelected"
                :indeterminate="someSelected"
                @change="toggleSelectAll"
              />
              Select all on this page
            </label>
            <span class="font-mono text-[11px] text-muted">
              {{ selectedIds.size }} selected
            </span>
          </div>

          <ul>
            <li
              v-for="job in jobs"
              :key="job.id"
              class="cursor-pointer border-b border-rule py-3 last:border-0 transition-colors"
              :class="{ 'bg-black/[0.02]': selectedIds.has(job.id) }"
              @click="viewJob(job.id)"
            >
              <div class="flex items-start gap-3">
                <span class="mt-0.5 flex h-4 w-4 flex-none items-center justify-center">
                  <input
                    v-if="job.status !== 'running'"
                    type="checkbox"
                    :checked="selectedIds.has(job.id)"
                    :aria-label="`Select job ${job.id}`"
                    @click.stop
                    @change="toggleSelect(job.id)"
                  />
                </span>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <StatusMark :status="job.status" />
                    <span class="font-mono text-[11px] text-muted">
                      #{{ job.id }}
                    </span>
                  </div>
                  <router-link
                    :to="`/jobs/${job.id}`"
                    class="mt-1 block truncate text-[13px] font-medium text-ink"
                    @click.stop
                  >
                    {{ job.crate_name }}
                    <span class="font-mono text-[11px] font-normal text-muted">
                      {{ job.version }}
                    </span>
                  </router-link>
                  <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
                    <span>Task {{ job.task_name || '—' }}</span>
                    <span class="inline-flex items-center gap-1">
                      Runner <RunnerIdBadge :runner-id="job.runner_id || ''" />
                    </span>
                    <span>Started {{ formatRelativeTime(job.started_at) }}</span>
                    <span>
                      Runtime
                      {{ formatDuration(job.started_at, job.finished_at) }}
                    </span>
                  </div>
                </div>
              </div>
            </li>
          </ul>
        </div>

        <div
          class="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-3"
        >
          <p class="text-[11px] text-muted">
            Showing {{ formatCount(rangeStart) }}–{{ formatCount(rangeEnd) }} of
            {{ formatCount(total) }} jobs
          </p>
          <div class="flex items-center gap-2">
            <button
              class="btn btn--ghost btn--sm"
              :disabled="page <= 1"
              @click="goToPage(page - 1)"
            >
              Previous
            </button>
            <span class="font-mono text-[11px] text-muted">
              Page {{ page }} of {{ totalPages }}
            </span>
            <button
              class="btn btn--ghost btn--sm"
              :disabled="page >= totalPages"
              @click="goToPage(page + 1)"
            >
              Next
            </button>
          </div>
        </div>
      </template>
    </section>
  </div>
</template>
