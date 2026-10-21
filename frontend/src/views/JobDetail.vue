<script setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'
import LogViewer from '../components/LogViewer.vue'
import RunnerIdBadge from '../components/RunnerIdBadge.vue'
import { shouldResetLogViewer } from '../utils/logAttemptReset'

const route = useRoute()
const router = useRouter()
const job = ref(null)
const loading = ref(true)
const error = ref(null)
const cancelling = ref(false)
const logViewerRef = ref(null)

const jobId = computed(() => route.params.id)

async function fetchJob() {
  try {
    job.value = await api.getJob(jobId.value)
    loading.value = false
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
    loading.value = false
  }
}

async function cancelJob() {
  if (!confirm('Are you sure you want to cancel this job?')) {
    return
  }

  cancelling.value = true

  try {
    await api.cancelJob(jobId.value)
    await fetchJob()
  } catch (err) {
    alert('Failed to cancel job: ' + (err.response?.data?.detail || err.message))
  } finally {
    cancelling.value = false
  }
}

async function retryJob() {
  if (!confirm(`Retry job #${jobId.value} (${job.value.crate_name} ${job.value.version})?\n\nThis will reset the job and re-execute it.`)) {
    return
  }

  try {
    await api.retryJob(jobId.value)
    // Refresh job data to show reset state
    await fetchJob()
  } catch (err) {
    alert('Failed to retry job: ' + (err.response?.data?.detail || err.message))
  }
}

// --- Task spec summary -------------------------------------------------
// job.task_spec is a JSON string snapshot; the API may also return it
// already parsed. Tolerate both, and tolerate parse failures.
function parseTaskSpec(spec) {
  if (!spec) return null
  if (typeof spec === 'string') {
    try {
      const parsed = JSON.parse(spec)
      return parsed && typeof parsed === 'object' ? parsed : null
    } catch {
      return null
    }
  }
  return typeof spec === 'object' ? spec : null
}

const taskSpec = computed(() => parseTaskSpec(job.value?.task_spec))

const specLimits = computed(() => {
  const limits = taskSpec.value?.limits
  return limits && typeof limits === 'object' ? limits : {}
})

const specEnvEntries = computed(() => {
  const env = taskSpec.value?.env
  if (!env || typeof env !== 'object') return []
  return Object.entries(env)
})

const showEnv = ref(false)

// --- Runtime ticker (live while the job is running) ---------------------
const nowMs = ref(Date.now())
let runtimeTicker = null

function startRuntimeTicker() {
  if (runtimeTicker) return
  nowMs.value = Date.now()
  runtimeTicker = setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)
}

function stopRuntimeTicker() {
  if (runtimeTicker) {
    clearInterval(runtimeTicker)
    runtimeTicker = null
  }
}

watch(
  () => job.value?.status,
  (status) => {
    if (status === 'running') {
      startRuntimeTicker()
    } else {
      stopRuntimeTicker()
    }
  }
)

const runtimeText = computed(() => {
  const started = job.value?.started_at
  if (!started) return '—'
  const startMs = new Date(started).getTime()
  const endMs = job.value?.finished_at
    ? new Date(job.value.finished_at).getTime()
    : nowMs.value
  return formatDurationMs(endMs - startMs)
})

function formatDurationMs(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const diff = Math.floor(ms / 1000)
  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`
  const hours = Math.floor(diff / 3600)
  const minutes = Math.floor((diff % 3600) / 60)
  return `${hours}h ${minutes}m`
}

// --- Status timeline ----------------------------------------------------
const STATUS_DOT = {
  completed: 'bg-green-600 border-green-600',
  failed: 'bg-red-600 border-red-600',
  cancelled: 'bg-yellow-500 border-yellow-500',
  timeout: 'bg-orange-500 border-orange-500',
  oom: 'bg-purple-600 border-purple-600',
  runner_failed: 'bg-red-800 border-red-800',
}

const timelineSteps = computed(() => {
  const j = job.value
  if (!j) return []
  const terminal = isTerminalStatus(j.status)
  const running = j.status === 'running'
  return [
    {
      label: 'Created',
      time: j.created_at,
      state: 'done',
      dotClass: 'bg-gray-900 border-gray-900',
    },
    {
      label: 'Started',
      time: j.started_at,
      state: running ? 'active' : terminal ? 'done' : 'pending',
      dotClass:
        running || terminal
          ? 'bg-gray-900 border-gray-900'
          : 'bg-white border-gray-300',
    },
    {
      label: 'Finished',
      time: j.finished_at,
      state: terminal ? 'done' : 'pending',
      dotClass: terminal
        ? STATUS_DOT[j.status] || 'bg-gray-900 border-gray-900'
        : 'bg-white border-gray-300',
    },
  ]
})

function connectorClass(index) {
  const next = timelineSteps.value[index + 1]
  return next && next.state !== 'pending' ? 'bg-gray-900' : 'bg-gray-200'
}

function formatTime(dateStr) {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

// --- Failure banner -----------------------------------------------------
const failureBanner = computed(() => {
  const j = job.value
  if (!j) return null
  const detail = j.error_message || j.message || ''
  switch (j.status) {
    case 'failed':
      return { tone: 'red', title: 'Job failed', detail }
    case 'runner_failed':
      return { tone: 'red', title: 'Runner failed', detail }
    case 'timeout':
      return { tone: 'amber', title: 'Job timed out', detail }
    case 'oom':
      return { tone: 'purple', title: 'Job ran out of memory', detail }
    case 'cancelled':
      return { tone: 'amber', title: 'Job cancelled', detail }
    default:
      return j.error_message
        ? { tone: 'red', title: 'Error', detail: j.error_message }
        : null
  }
})

const BANNER_TONES = {
  red: 'bg-red-50 border-red-200 text-red-800',
  amber: 'bg-amber-50 border-amber-200 text-amber-800',
  purple: 'bg-purple-50 border-purple-200 text-purple-800',
}

const watchFiles = computed(() => {
  const watch = taskSpec.value?.watch
  return Array.isArray(watch) ? watch : []
})

// --- Artifacts -----------------------------------------------------------
const artifacts = ref([])
const artifactsError = ref(null)

async function fetchArtifacts() {
  try {
    artifacts.value = await api.getJobArtifacts(jobId.value)
  } catch (err) {
    artifactsError.value = err.response?.data?.detail || err.message
  }
}

function artifactHref(path) {
  return api.artifactUrl(jobId.value, path)
}

function formatSize(bytes) {
  if (bytes === null || bytes === undefined) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function isTerminalStatus(status) {
  return ['completed', 'failed', 'cancelled', 'timeout', 'oom', 'runner_failed'].includes(status)
}

async function handleJobUpdate(data) {
  if (data.id === parseInt(jobId.value)) {
    // Only update job metadata (status, timestamps, etc.)
    // LogViewer component handles its own log updates independently via auto-refresh
    try {
      const updatedJob = await api.getJob(jobId.value)
      // Only update if job object is loaded to avoid race conditions
      if (job.value) {
        const shouldResetLogs = shouldResetLogViewer(job.value, updatedJob)
        // Update job data without replacing the entire object
        // This minimizes re-renders of child components
        Object.assign(job.value, updatedJob)
        if (shouldResetLogs) {
          logViewerRef.value?.resetForNewAttempt()
        }
        if (isTerminalStatus(updatedJob.status)) {
          fetchArtifacts()
        }
      }
    } catch (err) {
      // Silently fail on update errors to avoid disrupting user experience
      console.error('Failed to update job:', err)
    }
  }
}

function formatDate(dateStr) {
  if (!dateStr) return 'N/A'
  const date = new Date(dateStr)
  return date.toLocaleString()
}

onMounted(() => {
  fetchJob()
  fetchArtifacts()
  websocket.connectJob(jobId.value)
  websocket.on('job_update', handleJobUpdate)
  websocket.on('job_completed', handleJobUpdate)
})

onUnmounted(() => {
  stopRuntimeTicker()
  websocket.off('job_update', handleJobUpdate)
  websocket.off('job_completed', handleJobUpdate)
})
</script>

<template>
  <div>
    <div v-if="loading" class="flex justify-center py-12">
      <div class="spinner border-blue-500"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg">
      {{ error }}
      <button
        @click="router.push('/jobs')"
        class="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700"
      >
        Back to Jobs
      </button>
    </div>

    <div v-else>
      <!-- Header -->
      <div class="flex items-center justify-between mb-8">
        <div>
          <div class="flex items-center gap-3">
            <h1 class="text-3xl font-bold text-gray-900">
              {{ job.crate_name }} <span class="text-gray-500">v{{ job.version }}</span>
            </h1>
            <span :class="['status-badge text-sm', `status-${job.status}`]">
              {{ job.status }}
            </span>
          </div>
          <p class="mt-2 text-sm text-gray-500">Job #{{ job.id }}</p>
        </div>
        <div class="flex items-center gap-3">
          <button
            @click="router.push('/jobs')"
            class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
          >
            Back to Jobs
          </button>
          <button
            v-if="job.status === 'running'"
            @click="cancelJob"
            :disabled="cancelling"
            class="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors disabled:bg-gray-400 flex items-center gap-2"
          >
            <span v-if="cancelling" class="spinner border-white"></span>
            {{ cancelling ? 'Cancelling...' : 'Cancel Job' }}
          </button>
          <button
            v-if="job.status !== 'running' && job.status !== 'pending'"
            @click="retryJob"
            class="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 transition-colors flex items-center gap-2"
            title="Re-execute this job with the same configuration"
          >
            🔄 Retry Job
          </button>
        </div>
      </div>

      <!-- Failure banner -->
      <div
        v-if="failureBanner"
        :class="[
          'mb-6 border rounded-lg px-4 py-3 flex items-start gap-3',
          BANNER_TONES[failureBanner.tone]
        ]"
      >
        <svg class="w-5 h-5 shrink-0 mt-0.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <circle cx="8" cy="8" r="6.25" />
          <path d="M8 4.75v3.5M8 11h.01" stroke-linecap="round" />
        </svg>
        <div class="min-w-0">
          <p class="font-medium">{{ failureBanner.title }}</p>
          <p v-if="failureBanner.detail" class="mt-0.5 text-sm break-words">{{ failureBanner.detail }}</p>
        </div>
      </div>

      <!-- Status timeline -->
      <div class="bento-card mb-8">
        <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-6">
          <div class="flex-1 min-w-0">
            <div class="flex items-start">
              <template v-for="(step, index) in timelineSteps" :key="step.label">
                <div class="flex flex-col items-center w-24 shrink-0">
                  <span
                    :class="[
                      'w-3 h-3 rounded-full border-2 mt-0.5',
                      step.dotClass,
                      step.state === 'active' ? 'animate-pulse' : ''
                    ]"
                  ></span>
                  <span class="mt-2 text-xs font-medium text-gray-700">{{ step.label }}</span>
                  <span class="mt-0.5 text-xs text-gray-500 tabular-nums" :title="formatDate(step.time)">
                    {{ formatTime(step.time) }}
                  </span>
                </div>
                <div
                  v-if="index < timelineSteps.length - 1"
                  :class="['flex-1 h-0.5 mt-1.5 rounded', connectorClass(index)]"
                ></div>
              </template>
            </div>
          </div>
          <div class="flex items-center gap-8 md:border-l md:border-gray-200 md:pl-8">
            <div>
              <p class="text-xs font-medium text-gray-500">Runtime</p>
              <p class="mt-1 text-xl font-semibold text-gray-900 tabular-nums">{{ runtimeText }}</p>
            </div>
            <div>
              <p class="text-xs font-medium text-gray-500">Exit code</p>
              <p
                :class="[
                  'mt-1 text-xl font-semibold tabular-nums',
                  job.exit_code !== null && job.exit_code !== 0 ? 'text-red-600' : 'text-gray-900'
                ]"
              >
                {{ job.exit_code !== null ? job.exit_code : '—' }}
              </p>
            </div>
          </div>
        </div>
      </div>

      <!-- Job & Task overview -->
      <div class="bento-card mb-8">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
          <section>
            <h3 class="text-sm font-semibold text-gray-900 mb-4">Job</h3>
            <div class="space-y-4">
              <div>
                <p class="text-sm font-medium text-gray-600">Task</p>
                <p class="mt-1 text-sm text-gray-900">
                  {{ job.task_name || 'N/A' }} <span class="text-gray-500">(#{{ job.task_id }})</span>
                </p>
              </div>
              <div>
                <p class="text-sm font-medium text-gray-600">Runner ID</p>
                <div class="mt-1"><RunnerIdBadge :runner-id="job.runner_id || ''" /></div>
              </div>
              <div v-if="job.message && !failureBanner">
                <p class="text-sm font-medium text-gray-600">Execution message</p>
                <p class="mt-1 text-sm text-gray-900 break-words">{{ job.message }}</p>
              </div>
            </div>
          </section>

          <section class="md:border-l md:border-gray-200 md:pl-8">
            <h3 class="text-sm font-semibold text-gray-900 mb-4">Task spec</h3>
            <div v-if="taskSpec" class="space-y-4">
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <p class="text-sm font-medium text-gray-600">Name</p>
                  <p class="mt-1 text-sm text-gray-900 break-words">{{ taskSpec.name || 'N/A' }}</p>
                </div>
                <div>
                  <p class="text-sm font-medium text-gray-600">Image</p>
                  <p class="mt-1 text-sm text-gray-900 font-mono break-all">{{ taskSpec.image || 'N/A' }}</p>
                </div>
              </div>
              <div>
                <p class="text-sm font-medium text-gray-600">Command</p>
                <p class="mt-1 text-sm text-gray-900 font-mono break-all">{{ taskSpec.cmd || 'N/A' }}</p>
              </div>
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <p class="text-sm font-medium text-gray-600">Workspace</p>
                  <p class="mt-1 text-sm text-gray-900 font-mono break-all">{{ taskSpec.workspace || 'N/A' }}</p>
                </div>
                <div>
                  <p class="text-sm font-medium text-gray-600">Limits</p>
                  <p class="mt-1 text-sm text-gray-900">
                    <span v-if="specLimits.cpu">CPU: {{ specLimits.cpu }} </span>
                    <span v-if="specLimits.mem">Mem: {{ specLimits.mem }} </span>
                    <span v-if="specLimits.timeout_seconds">Timeout: {{ specLimits.timeout_seconds }}s</span>
                    <span v-if="!specLimits.cpu && !specLimits.mem && !specLimits.timeout_seconds">N/A</span>
                  </p>
                </div>
              </div>
              <div v-if="specEnvEntries.length > 0">
                <button
                  @click="showEnv = !showEnv"
                  class="inline-flex items-center gap-1 text-sm font-medium text-blue-600 hover:text-blue-500"
                >
                  Environment ({{ specEnvEntries.length }})
                  <svg
                    :class="['w-3.5 h-3.5 transition-transform', showEnv ? 'rotate-90' : '']"
                    viewBox="0 0 16 16"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                    aria-hidden="true"
                  >
                    <path d="m6 3.5 4.5 4.5L6 12.5" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>
                <ul v-if="showEnv" class="mt-2 text-xs text-gray-900 font-mono space-y-1 bg-gray-50 rounded-lg p-3">
                  <li v-for="[key, value] in specEnvEntries" :key="key" class="break-all">
                    {{ key }}={{ value }}
                  </li>
                </ul>
              </div>
            </div>
            <p v-else class="text-sm text-gray-500">Task spec unavailable or could not be parsed.</p>
          </section>
        </div>
      </div>

      <!-- Watches & Logs Viewer -->
      <LogViewer
        ref="logViewerRef"
        :key="`log-viewer-${jobId}`"
        :job-id="jobId"
        :auto-scroll="job.status === 'running'"
        :watch-files="watchFiles"
        class="mb-8"
      />

      <!-- Artifacts (last) -->
      <div class="bento-card mb-8">
        <h3 class="text-lg font-semibold text-gray-900 mb-4">Artifacts</h3>
        <div v-if="artifactsError" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg text-sm">
          Failed to load artifacts: {{ artifactsError }}
        </div>
        <p v-else-if="artifacts.length === 0" class="text-sm text-gray-500">
          No artifacts uploaded yet.
        </p>
        <table v-else class="min-w-full divide-y divide-gray-200">
          <thead class="bg-gray-50">
            <tr>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Path</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-28">Size</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-28">Download</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-200">
            <tr v-for="artifact in artifacts" :key="artifact.path" class="hover:bg-gray-50 transition-colors">
              <td class="px-4 py-3 text-sm text-gray-900 font-mono break-all">{{ artifact.path }}</td>
              <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ formatSize(artifact.size) }}</td>
              <td class="px-4 py-3 whitespace-nowrap text-sm">
                <a
                  :href="artifactHref(artifact.path)"
                  :download="artifact.path.split('/').pop()"
                  class="font-medium text-blue-600 hover:text-blue-500"
                >
                  Download
                </a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
