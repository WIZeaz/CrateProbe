<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
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

const watchFiles = computed(() => {
  const watch = taskSpec.value?.watch
  return Array.isArray(watch) ? watch : []
})

// --- Watched files viewer ----------------------------------------------
const activeWatchFile = ref(null)
const watchContent = ref('')
const watchLoading = ref(false)
const watchError = ref(null)

async function loadWatchFile(path) {
  activeWatchFile.value = path
  watchLoading.value = true
  watchError.value = null
  watchContent.value = ''

  try {
    const data = await api.getJobFile(jobId.value, path, { lines: 1000 })
    watchContent.value =
      data.lines && Array.isArray(data.lines) && data.lines.length > 0
        ? data.lines.join('\n')
        : 'No content available'
  } catch (err) {
    watchError.value =
      err.response?.status === 404
        ? 'File not available yet.'
        : err.response?.data?.detail || err.message
  } finally {
    watchLoading.value = false
  }
}

function refreshWatchFile() {
  if (activeWatchFile.value) {
    loadWatchFile(activeWatchFile.value)
  }
}

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

onMounted(() => {
  fetchJob()
  fetchArtifacts()
  websocket.connectJob(jobId.value)
  websocket.on('job_update', handleJobUpdate)
  websocket.on('job_completed', handleJobUpdate)
})

onUnmounted(() => {
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

      <!-- Stats Grid -->
      <div class="bento-grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        <div class="bento-card">
          <p class="text-sm font-medium text-gray-600">Status</p>
          <p class="mt-2 text-2xl font-bold text-gray-900">{{ job.status }}</p>
        </div>
        <div class="bento-card">
          <p class="text-sm font-medium text-gray-600">Runtime</p>
          <p class="mt-2 text-2xl font-bold text-gray-900">
            {{ formatDuration(job.started_at, job.finished_at) }}
          </p>
        </div>
        <div class="bento-card">
          <p class="text-sm font-medium text-gray-600">Exit Code</p>
          <p class="mt-2 text-2xl font-bold text-gray-900">
            {{ job.exit_code !== null ? job.exit_code : 'N/A' }}
          </p>
        </div>
      </div>

      <!-- Job Details -->
      <div class="bento-card mb-8">
        <h3 class="text-lg font-semibold text-gray-900 mb-4">Details</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
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
          <div>
            <p class="text-sm font-medium text-gray-600">Created</p>
            <p class="mt-1 text-sm text-gray-900">{{ formatDate(job.created_at) }}</p>
          </div>
          <div>
            <p class="text-sm font-medium text-gray-600">Started</p>
            <p class="mt-1 text-sm text-gray-900">{{ formatDate(job.started_at) }}</p>
          </div>
          <div>
            <p class="text-sm font-medium text-gray-600">Finished</p>
            <p class="mt-1 text-sm text-gray-900">{{ formatDate(job.finished_at) }}</p>
          </div>
          <div v-if="job.message" class="md:col-span-2">
            <p class="text-sm font-medium text-gray-600">Execution Message</p>
            <p class="mt-1 text-sm text-gray-900 break-words">{{ job.message }}</p>
          </div>
          <div v-if="job.error_message">
            <p class="text-sm font-medium text-gray-600">Error</p>
            <p class="mt-1 text-sm text-red-600">{{ job.error_message }}</p>
          </div>
        </div>
      </div>

      <!-- Task Spec Summary -->
      <div class="bento-card mb-8">
        <h3 class="text-lg font-semibold text-gray-900 mb-4">Task Spec</h3>
        <div v-if="taskSpec" class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <p class="text-sm font-medium text-gray-600">Name</p>
            <p class="mt-1 text-sm text-gray-900">{{ taskSpec.name || 'N/A' }}</p>
          </div>
          <div>
            <p class="text-sm font-medium text-gray-600">Image</p>
            <p class="mt-1 text-sm text-gray-900 font-mono break-all">{{ taskSpec.image || 'N/A' }}</p>
          </div>
          <div class="md:col-span-2">
            <p class="text-sm font-medium text-gray-600">Command</p>
            <p class="mt-1 text-sm text-gray-900 font-mono break-all">{{ taskSpec.cmd || 'N/A' }}</p>
          </div>
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
          <div v-if="specEnvEntries.length > 0" class="md:col-span-2">
            <p class="text-sm font-medium text-gray-600">Environment</p>
            <ul class="mt-1 text-sm text-gray-900 font-mono space-y-1">
              <li v-for="[key, value] in specEnvEntries" :key="key" class="break-all">
                {{ key }}={{ value }}
              </li>
            </ul>
          </div>
        </div>
        <p v-else class="text-sm text-gray-500">Task spec unavailable or could not be parsed.</p>
      </div>

      <!-- Watched Files -->
      <div v-if="watchFiles.length > 0" class="bento-card mb-8">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-semibold text-gray-900">Watched Files</h3>
          <button
            v-if="activeWatchFile"
            @click="refreshWatchFile"
            :disabled="watchLoading"
            class="px-3 py-1.5 text-sm font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors disabled:opacity-50"
          >
            {{ watchLoading ? 'Refreshing...' : '↻ Refresh' }}
          </button>
        </div>
        <div class="flex" style="min-height: 200px;">
          <!-- Left: watched file list -->
          <div
            class="flex flex-col flex-shrink-0 border-r border-gray-200"
            style="width: 220px;"
          >
            <div class="flex flex-col gap-1 px-2 flex-1">
              <button
                v-for="path in watchFiles"
                :key="path"
                @click="loadWatchFile(path)"
                :class="[
                  'flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-left transition-colors w-full',
                  activeWatchFile === path
                    ? 'bg-blue-50 border border-blue-200 text-blue-700'
                    : 'text-gray-600 hover:bg-gray-100'
                ]"
              >
                <span>📄</span>
                <span class="truncate font-mono text-xs">{{ path }}</span>
              </button>
            </div>
          </div>

          <!-- Right: watched file content -->
          <div class="flex-1 min-w-0 px-4">
            <div v-if="!activeWatchFile" class="flex items-center justify-center h-full text-sm text-gray-400">
              Select a file to view its content
            </div>
            <div v-else-if="watchLoading" class="flex justify-center py-8">
              <div class="spinner"></div>
            </div>
            <div v-else-if="watchError" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg text-sm">
              {{ watchError }}
            </div>
            <pre v-else class="text-sm text-gray-900 whitespace-pre-wrap break-all" style="max-height: 400px; overflow-y: auto;">{{ watchContent }}</pre>
          </div>
        </div>
      </div>

      <!-- Artifacts -->
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

      <!-- Log Viewer -->
      <LogViewer ref="logViewerRef" :key="`log-viewer-${jobId}`" :job-id="jobId" :auto-scroll="job.status === 'running'" />
    </div>
  </div>
</template>
