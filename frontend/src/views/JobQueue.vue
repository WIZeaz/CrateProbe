<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'
import RunnerIdBadge from '../components/RunnerIdBadge.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import StatusMark from '../components/ui/StatusMark.vue'
import { formatDuration } from '../utils/format.js'

const router = useRouter()
const runningJobs = ref([])
const pendingJobs = ref([])
const selectedRunningIds = ref(new Set())
const selectedPendingIds = ref(new Set())
const loading = ref(true)
const error = ref(null)

const runningSelectedCount = computed(() => selectedRunningIds.value.size)
const pendingSelectedCount = computed(() => selectedPendingIds.value.size)

const allRunningSelected = computed(() => {
  return runningJobs.value.length > 0 && runningJobs.value.every(j => selectedRunningIds.value.has(j.id))
})

const someRunningSelected = computed(() => {
  return selectedRunningIds.value.size > 0 && !allRunningSelected.value
})

const allPendingSelected = computed(() => {
  return pendingJobs.value.length > 0 && pendingJobs.value.every(j => selectedPendingIds.value.has(j.id))
})

const somePendingSelected = computed(() => {
  return selectedPendingIds.value.size > 0 && !allPendingSelected.value
})

function toggleSelectAllRunning() {
  if (allRunningSelected.value) {
    selectedRunningIds.value = new Set()
  } else {
    selectedRunningIds.value = new Set(runningJobs.value.map(j => j.id))
  }
}

function toggleSelectAllPending() {
  if (allPendingSelected.value) {
    selectedPendingIds.value = new Set()
  } else {
    selectedPendingIds.value = new Set(pendingJobs.value.map(j => j.id))
  }
}

function toggleRunningSelect(jobId) {
  const next = new Set(selectedRunningIds.value)
  if (next.has(jobId)) {
    next.delete(jobId)
  } else {
    next.add(jobId)
  }
  selectedRunningIds.value = next
}

function togglePendingSelect(jobId) {
  const next = new Set(selectedPendingIds.value)
  if (next.has(jobId)) {
    next.delete(jobId)
  } else {
    next.add(jobId)
  }
  selectedPendingIds.value = next
}

async function fetchQueue({ showLoading = false } = {}) {
  if (showLoading) {
    loading.value = true
  }
  error.value = ''
  try {
    const data = await api.getQueue()
    runningJobs.value = data.running
    pendingJobs.value = data.pending
    loading.value = false
    const runningIds = new Set(runningJobs.value.map(j => j.id))
    const pendingIds = new Set(pendingJobs.value.map(j => j.id))
    selectedRunningIds.value = new Set([...selectedRunningIds.value].filter(id => runningIds.has(id)))
    selectedPendingIds.value = new Set([...selectedPendingIds.value].filter(id => pendingIds.has(id)))
  } catch (err) {
    error.value = err.message
    loading.value = false
  }
}

async function handlePinSelected() {
  const ids = [...selectedPendingIds.value]
  if (ids.length === 0) return
  try {
    const result = await api.batchSetPriority(ids, 100)
    selectedPendingIds.value = new Set()
    await fetchQueue()
    if (result.skipped?.length > 0) {
      alert(`Pinned ${result.updated.length} job(s). Skipped ${result.skipped.length} non-pending job(s).`)
    }
  } catch (err) {
    alert(`Failed to pin jobs: ${err.message}`)
  }
}

async function handleCancelSelected() {
  const ids = [...selectedRunningIds.value]
  if (ids.length === 0) return
  if (!confirm(`Cancel ${ids.length} running job(s)?`)) return
  try {
    const result = await api.batchCancel(ids)
    selectedRunningIds.value = new Set()
    await fetchQueue()
    if (result.skipped?.length > 0) {
      alert(`Cancelled ${result.cancelled.length} job(s). Skipped ${result.skipped.length} non-running job(s).`)
    }
  } catch (err) {
    alert(`Failed to cancel jobs: ${err.message}`)
  }
}

function getOrdinalSuffix(n) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return s[(v - 20) % 10] || s[v] || s[0]
}

function getQueuePosition(index) {
  return `${index + 1}${getOrdinalSuffix(index + 1)}`
}

function viewJob(jobId) {
  router.push(`/jobs/${jobId}`)
}

onMounted(() => {
  fetchQueue()
  websocket.connect('/ws/dashboard')
  websocket.on('job_update', fetchQueue)
  websocket.on('job_created', fetchQueue)
  websocket.on('job_completed', fetchQueue)
})

onUnmounted(() => {
  websocket.off('job_update', fetchQueue)
  websocket.off('job_created', fetchQueue)
  websocket.off('job_completed', fetchQueue)
})
</script>

<template>
  <div>
    <PageHeader
      title="Job queue"
      description="Running jobs and the pending queue."
    >
      <template #actions>
        <button
          v-if="pendingSelectedCount > 0"
          class="btn btn--warn-ghost btn--sm action-in"
          @click="handlePinSelected"
        >
          Pin selected ({{ pendingSelectedCount }})
        </button>
        <button
          v-if="runningSelectedCount > 0"
          class="btn btn--danger-ghost btn--sm action-in"
          @click="handleCancelSelected"
        >
          Cancel selected ({{ runningSelectedCount }})
        </button>
      </template>
    </PageHeader>

    <section class="instrument-panel">
      <div class="panel-header">
        <h2 class="panel-title">Queue</h2>
        <div class="flex items-center gap-4 font-mono text-[11px] text-muted">
          <span class="inline-flex items-center gap-1.5">
            <StatusMark status="running" />
            {{ runningJobs.length }}
          </span>
          <span class="inline-flex items-center gap-1.5">
            <StatusMark status="pending" />
            {{ pendingJobs.length }}
          </span>
        </div>
      </div>

      <div
        v-if="loading"
        class="mt-4 space-y-3"
        aria-busy="true"
        aria-label="Loading job queue"
      >
        <div v-for="index in 4" :key="index" class="flex items-center gap-4">
          <div class="skeleton h-3 w-6"></div>
          <div class="skeleton h-3 w-14"></div>
          <div class="skeleton h-3 flex-1"></div>
          <div class="skeleton h-3 w-20"></div>
        </div>
      </div>

      <div v-else-if="error" class="notice notice--error mt-4" role="alert">
        <div class="min-w-0 flex-1">
          <p class="notice__title">Couldn't load the queue.</p>
          <p class="notice__detail">{{ error }}</p>
        </div>
        <button
          class="btn btn--ghost btn--sm"
          @click="fetchQueue({ showLoading: true })"
        >
          Try again
        </button>
      </div>

      <EmptyState
        v-else-if="runningJobs.length === 0 && pendingJobs.length === 0"
        title="Queue is clear"
        description="No jobs are running or pending."
      >
        <router-link to="/jobs" class="btn btn--ghost btn--sm">
          View all jobs
        </router-link>
      </EmptyState>

      <template v-else>
        <div class="mt-2 hidden overflow-x-auto lg:block">
          <table class="data-table">
            <thead>
              <tr>
                <th class="w-10"></th>
                <th class="w-20">Job ID</th>
                <th>Crate</th>
                <th class="w-24">Version</th>
                <th class="w-32">Task</th>
                <th class="w-28">Status</th>
                <th class="w-32">Runner</th>
                <th class="w-28">Queue position</th>
                <th class="w-24 text-right">Runtime</th>
              </tr>
            </thead>
            <tbody>
              <template v-if="runningJobs.length > 0">
                <tr class="group-row group-row--running">
                  <td @click.stop>
                    <input
                      type="checkbox"
                      :checked="allRunningSelected"
                      :indeterminate="someRunningSelected"
                      aria-label="Select all running jobs"
                      @change="toggleSelectAllRunning"
                    />
                  </td>
                  <td colspan="8">
                    <div class="flex items-center gap-2">
                      <StatusMark status="running" />
                      <span class="text-[12px] font-semibold text-ink">
                        Running jobs
                      </span>
                      <span class="font-mono text-[11px] text-muted">
                        {{ runningJobs.length }}
                      </span>
                    </div>
                  </td>
                </tr>
                <tr
                  v-for="job in runningJobs"
                  :key="job.id"
                  class="cursor-pointer"
                  :class="{ 'is-selected': selectedRunningIds.has(job.id) }"
                  @click="viewJob(job.id)"
                >
                  <td @click.stop>
                    <input
                      type="checkbox"
                      :checked="selectedRunningIds.has(job.id)"
                      :aria-label="`Select job ${job.id}`"
                      @change="toggleRunningSelect(job.id)"
                    />
                  </td>
                  <td class="numeric text-muted">#{{ job.id }}</td>
                  <td class="min-w-0">
                    <router-link
                      :to="`/jobs/${job.id}`"
                      class="block truncate text-[13px] font-medium text-ink hover:text-signal"
                      @click.stop
                    >
                      {{ job.crate_name }}
                    </router-link>
                  </td>
                  <td class="numeric text-muted">{{ job.version }}</td>
                  <td>
                    <span
                      class="block max-w-[140px] truncate text-muted"
                      :title="job.task_name || ''"
                    >
                      {{ job.task_name || '—' }}
                    </span>
                  </td>
                  <td><StatusMark :status="job.status" /></td>
                  <td class="whitespace-nowrap">
                    <RunnerIdBadge :runner-id="job.runner_id || ''" />
                  </td>
                  <td class="numeric text-muted">—</td>
                  <td class="numeric text-right">
                    {{ formatDuration(job.started_at, job.finished_at) }}
                  </td>
                </tr>
              </template>

              <template v-if="pendingJobs.length > 0">
                <tr class="group-row group-row--pending">
                  <td @click.stop>
                    <input
                      type="checkbox"
                      :checked="allPendingSelected"
                      :indeterminate="somePendingSelected"
                      aria-label="Select all pending jobs"
                      @change="toggleSelectAllPending"
                    />
                  </td>
                  <td colspan="8">
                    <div class="flex items-center gap-2">
                      <StatusMark status="pending" />
                      <span class="text-[12px] font-semibold text-ink">
                        Pending queue
                      </span>
                      <span class="font-mono text-[11px] text-muted">
                        {{ pendingJobs.length }}
                      </span>
                    </div>
                  </td>
                </tr>
                <tr
                  v-for="(job, idx) in pendingJobs"
                  :key="job.id"
                  class="cursor-pointer"
                  :class="{ 'is-selected': selectedPendingIds.has(job.id) }"
                  @click="viewJob(job.id)"
                >
                  <td @click.stop>
                    <input
                      type="checkbox"
                      :checked="selectedPendingIds.has(job.id)"
                      :aria-label="`Select job ${job.id}`"
                      @change="togglePendingSelect(job.id)"
                    />
                  </td>
                  <td class="numeric text-muted">#{{ job.id }}</td>
                  <td class="min-w-0">
                    <router-link
                      :to="`/jobs/${job.id}`"
                      class="block truncate text-[13px] font-medium text-ink hover:text-signal"
                      @click.stop
                    >
                      {{ job.crate_name }}
                    </router-link>
                  </td>
                  <td class="numeric text-muted">{{ job.version }}</td>
                  <td>
                    <span
                      class="block max-w-[140px] truncate text-muted"
                      :title="job.task_name || ''"
                    >
                      {{ job.task_name || '—' }}
                    </span>
                  </td>
                  <td><StatusMark :status="job.status" /></td>
                  <td class="whitespace-nowrap">
                    <RunnerIdBadge :runner-id="job.runner_id || ''" />
                  </td>
                  <td>
                    <StatusMark
                      v-if="job.priority > 0"
                      tone="warn"
                      label="Pinned"
                    />
                    <span v-else class="numeric text-muted">
                      {{ getQueuePosition(idx) }}
                    </span>
                  </td>
                  <td class="numeric text-right text-muted">—</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>

        <div class="mt-2 lg:hidden">
          <template v-if="runningJobs.length > 0">
            <div class="mobile-group-header mobile-group-header--running">
              <label class="flex items-center gap-2 text-[12px] font-semibold text-ink">
                <input
                  type="checkbox"
                  :checked="allRunningSelected"
                  :indeterminate="someRunningSelected"
                  @change="toggleSelectAllRunning"
                />
                Running jobs
              </label>
              <span class="font-mono text-[11px] text-muted">
                {{ runningJobs.length }}
              </span>
            </div>
            <ul>
              <li
                v-for="job in runningJobs"
                :key="job.id"
                class="cursor-pointer border-b border-rule py-3 transition-colors last:border-0"
                :class="{ 'bg-black/[0.02]': selectedRunningIds.has(job.id) }"
                @click="viewJob(job.id)"
              >
                <div class="flex items-start gap-3">
                  <input
                    type="checkbox"
                    class="mt-0.5"
                    :checked="selectedRunningIds.has(job.id)"
                    :aria-label="`Select job ${job.id}`"
                    @click.stop
                    @change="toggleRunningSelect(job.id)"
                  />
                  <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <StatusMark :status="job.status" />
                      <span class="font-mono text-[11px] text-muted">#{{ job.id }}</span>
                    </div>
                    <router-link
                      :to="`/jobs/${job.id}`"
                      class="mt-1 block text-[13px] font-medium text-ink"
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
                      <span>Runtime {{ formatDuration(job.started_at, job.finished_at) }}</span>
                    </div>
                  </div>
                </div>
              </li>
            </ul>
          </template>

          <template v-if="pendingJobs.length > 0">
            <div class="mobile-group-header mobile-group-header--pending">
              <label class="flex items-center gap-2 text-[12px] font-semibold text-ink">
                <input
                  type="checkbox"
                  :checked="allPendingSelected"
                  :indeterminate="somePendingSelected"
                  @change="toggleSelectAllPending"
                />
                Pending queue
              </label>
              <span class="font-mono text-[11px] text-muted">
                {{ pendingJobs.length }}
              </span>
            </div>
            <ul>
              <li
                v-for="(job, idx) in pendingJobs"
                :key="job.id"
                class="cursor-pointer border-b border-rule py-3 transition-colors last:border-0"
                :class="{ 'bg-black/[0.02]': selectedPendingIds.has(job.id) }"
                @click="viewJob(job.id)"
              >
                <div class="flex items-start gap-3">
                  <input
                    type="checkbox"
                    class="mt-0.5"
                    :checked="selectedPendingIds.has(job.id)"
                    :aria-label="`Select job ${job.id}`"
                    @click.stop
                    @change="togglePendingSelect(job.id)"
                  />
                  <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <StatusMark :status="job.status" />
                      <span class="font-mono text-[11px] text-muted">#{{ job.id }}</span>
                      <StatusMark
                        v-if="job.priority > 0"
                        tone="warn"
                        label="Pinned"
                      />
                    </div>
                    <router-link
                      :to="`/jobs/${job.id}`"
                      class="mt-1 block text-[13px] font-medium text-ink"
                      @click.stop
                    >
                      {{ job.crate_name }}
                      <span class="font-mono text-[11px] font-normal text-muted">
                        {{ job.version }}
                      </span>
                    </router-link>
                    <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
                      <span>Task {{ job.task_name || '—' }}</span>
                      <span v-if="job.priority === 0">Queue {{ getQueuePosition(idx) }}</span>
                    </div>
                  </div>
                </div>
              </li>
            </ul>
          </template>
        </div>
      </template>
    </section>
  </div>
</template>
