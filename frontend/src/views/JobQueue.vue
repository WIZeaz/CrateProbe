<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'
import RunnerIdBadge from '../components/RunnerIdBadge.vue'

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

async function fetchQueue() {
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

function getOrdinalSuffix(n) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return s[(v - 20) % 10] || s[v] || s[0]
}

function getQueuePosition(job, index) {
  if (job.priority > 0) return '⭐ Pinned'
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
    <div class="flex items-center justify-between mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Job Queue</h1>
      <div class="flex gap-3">
        <button
          v-if="pendingSelectedCount > 0"
          @click="handlePinSelected"
          class="px-4 py-2 text-sm font-medium text-white bg-orange-500 rounded-lg hover:bg-orange-600 transition-colors"
        >
          Pin Selected ({{ pendingSelectedCount }})
        </button>
        <button
          v-if="runningSelectedCount > 0"
          @click="handleCancelSelected"
          class="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors"
        >
          Cancel Selected ({{ runningSelectedCount }})
        </button>
      </div>
    </div>

    <div class="mb-4 flex items-center justify-between text-sm text-gray-600">
      <span>{{ runningJobs.length }} Running | {{ pendingJobs.length }} Pending</span>
    </div>

    <div v-if="loading" class="flex justify-center py-12">
      <div class="spinner border-blue-500"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg">
      Failed to load queue: {{ error }}
    </div>

    <div v-else-if="runningJobs.length === 0 && pendingJobs.length === 0" class="bento-card text-center py-12">
      <p class="text-gray-500">No jobs in queue.</p>
    </div>

    <div v-else class="bento-card">
      <table class="w-full table-fixed divide-y divide-gray-200">
        <thead>
          <tr>
            <th class="px-4 py-3 text-left w-12">
              <input
                v-if="runningJobs.length > 0"
                type="checkbox"
                :checked="allRunningSelected"
                :indeterminate="someRunningSelected"
                @change="toggleSelectAllRunning"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap w-16">ID</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">Crate</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap w-24">Version</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap w-32">Task</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap w-24">Status</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap w-32">Runner ID</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap w-28">Queue Pos</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap w-24">Runtime</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-200">
          <tr v-if="runningJobs.length > 0" class="bg-green-50">
            <td colspan="9" class="px-4 py-2 text-sm font-semibold text-green-800">
              ▶ Running Jobs ({{ runningJobs.length }})
            </td>
          </tr>
          <tr v-for="job in runningJobs" :key="job.id" class="hover:bg-gray-50 transition-colors cursor-pointer" @click="viewJob(job.id)">
            <td class="px-4 py-3 whitespace-nowrap" @click.stop>
              <input
                type="checkbox"
                :checked="selectedRunningIds.has(job.id)"
                @change="toggleRunningSelect(job.id)"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">#{{ job.id }}</td>
            <td class="px-4 py-3 text-sm font-medium text-gray-900 truncate">{{ job.crate_name }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ job.version }}</td>
            <td class="px-4 py-3 text-sm text-gray-500 truncate">{{ job.task_name || '-' }}</td>
            <td class="px-4 py-3 whitespace-nowrap">
              <span class="status-badge status-running">{{ job.status }}</span>
            </td>
            <td class="px-4 py-3 text-sm text-gray-500 truncate"><RunnerIdBadge :runner-id="job.runner_id || ''" /></td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">-</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ formatDuration(job.started_at, job.finished_at) }}</td>
          </tr>

          <tr v-if="pendingJobs.length > 0" class="bg-orange-50">
            <td colspan="9" class="px-4 py-2 text-sm font-semibold text-orange-800">
              ⏳ Pending Queue ({{ pendingJobs.length }})
            </td>
          </tr>
          <tr v-for="(job, idx) in pendingJobs" :key="job.id" class="hover:bg-gray-50 transition-colors cursor-pointer" :class="{ 'bg-yellow-50': job.priority > 0 }" @click="viewJob(job.id)">
            <td class="px-4 py-3 whitespace-nowrap" @click.stop>
              <input
                type="checkbox"
                :checked="selectedPendingIds.has(job.id)"
                @change="togglePendingSelect(job.id)"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">#{{ job.id }}</td>
            <td class="px-4 py-3 text-sm font-medium text-gray-900 truncate">{{ job.crate_name }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ job.version }}</td>
            <td class="px-4 py-3 text-sm text-gray-500 truncate">{{ job.task_name || '-' }}</td>
            <td class="px-4 py-3 whitespace-nowrap">
              <span class="status-badge status-pending">{{ job.status }}</span>
            </td>
            <td class="px-4 py-3 text-sm text-gray-500 truncate"><RunnerIdBadge :runner-id="job.runner_id || ''" /></td>
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium" :class="job.priority > 0 ? 'text-orange-600' : 'text-gray-500'">
              {{ getQueuePosition(job, idx) }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">-</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
