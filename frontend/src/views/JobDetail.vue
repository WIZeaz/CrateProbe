<script setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'
import LogViewer from '../components/LogViewer.vue'
import RunnerIdBadge from '../components/RunnerIdBadge.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import StatusMark from '../components/ui/StatusMark.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import {
  formatDateTime,
  formatDurationMs,
  formatTime,
} from '../utils/format.js'
import { isTerminalStatus, statusTone } from '../utils/status.js'
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

// --- Status timeline ----------------------------------------------------
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
      tone: 'neutral',
      hollow: false,
    },
    {
      label: 'Started',
      time: j.started_at,
      state: running ? 'active' : terminal ? 'done' : 'pending',
      tone: running ? 'signal' : 'neutral',
      hollow: !running && !terminal,
    },
    {
      label: 'Finished',
      time: j.finished_at,
      state: terminal ? 'done' : 'pending',
      tone: terminal ? statusTone(j.status) : 'neutral',
      hollow: !terminal,
    },
  ]
})

function connectorClass(index) {
  const next = timelineSteps.value[index + 1]
  return next && next.state !== 'pending'
    ? 'timeline-connector--done'
    : 'timeline-connector'
}

// --- Failure banner -----------------------------------------------------
const failureBanner = computed(() => {
  const j = job.value
  if (!j) return null
  const detail = j.error_message || j.message || ''
  switch (j.status) {
    case 'failed':
      return { tone: 'alarm', noticeClass: 'notice--error', title: 'Job failed', detail }
    case 'runner_failed':
      return { tone: 'alarm', noticeClass: 'notice--error', title: 'Runner failed', detail }
    case 'timeout':
      return { tone: 'warn', noticeClass: 'notice--warn', title: 'Job timed out', detail }
    case 'oom':
      return { tone: 'violet', noticeClass: 'notice--violet', title: 'Job ran out of memory', detail }
    case 'cancelled':
      return { tone: 'warn', noticeClass: 'notice--warn', title: 'Job cancelled', detail }
    default:
      return j.error_message
        ? {
            tone: 'alarm',
            noticeClass: 'notice--error',
            title: 'Error',
            detail: j.error_message,
          }
        : null
  }
})

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
    <div
      v-if="loading"
      class="space-y-5"
      aria-busy="true"
      aria-label="Loading job"
    >
      <div class="skeleton h-8 w-72"></div>
      <div class="skeleton h-24 w-full"></div>
      <div class="skeleton h-40 w-full"></div>
    </div>

    <div v-else-if="error" class="instrument-panel">
      <div class="notice notice--error" role="alert">
        <div class="min-w-0 flex-1">
          <p class="notice__title">Couldn't load this job.</p>
          <p class="notice__detail">{{ error }}</p>
        </div>
      </div>
      <div class="mt-4 flex flex-wrap gap-2">
        <button class="btn btn--ghost" @click="fetchJob">Try again</button>
        <button class="btn btn--ghost" @click="router.push('/jobs')">
          Back to jobs
        </button>
      </div>
    </div>

    <div v-else>
      <PageHeader>
        <template #title>
          <span class="flex flex-wrap items-center gap-2">
            <span>{{ job.crate_name }}</span>
            <span
              class="font-mono text-[13px] font-normal tracking-normal text-muted"
            >
              v{{ job.version }}
            </span>
            <StatusMark :status="job.status" size="md" />
          </span>
        </template>
        <template #description>
          Job #{{ job.id }}, task {{ job.task_name || '—' }}
        </template>
        <template #actions>
          <button class="btn btn--ghost" @click="router.push('/jobs')">
            Back to jobs
          </button>
          <button
            v-if="job.status === 'running'"
            class="btn btn--danger"
            :disabled="cancelling"
            @click="cancelJob"
          >
            <span v-if="cancelling" class="spinner border-white"></span>
            {{ cancelling ? 'Cancelling...' : 'Cancel job' }}
          </button>
          <button
            v-if="job.status !== 'running' && job.status !== 'pending'"
            class="btn btn--primary"
            @click="retryJob"
          >
            Retry job
          </button>
        </template>
      </PageHeader>

      <div
        v-if="failureBanner"
        class="notice mb-5"
        :class="failureBanner.noticeClass"
        role="alert"
      >
        <div class="min-w-0 flex-1">
          <p class="notice__title flex items-center gap-2">
            <span
              :class="['status-dot', `tone-${failureBanner.tone}`]"
              aria-hidden="true"
            ></span>
            {{ failureBanner.title }}
          </p>
          <p
            v-if="failureBanner.detail"
            class="notice__detail break-words"
          >
            {{ failureBanner.detail }}
          </p>
        </div>
      </div>

      <section class="instrument-panel mb-5" aria-label="Job timeline">
        <div
          class="flex flex-col gap-6 md:flex-row md:items-center md:justify-between"
        >
          <div class="min-w-0 flex-1">
            <div class="flex items-start">
              <template
                v-for="(step, index) in timelineSteps"
                :key="step.label"
              >
                <div class="flex w-20 shrink-0 flex-col items-center">
                  <span
                    :class="[
                      'timeline-dot',
                      `tone-${step.tone}`,
                      {
                        'timeline-dot--hollow': step.hollow,
                        'timeline-dot--active': step.state === 'active',
                      },
                    ]"
                  ></span>
                  <span class="mt-2 text-[11px] font-medium text-ink">
                    {{ step.label }}
                  </span>
                  <span
                    class="mt-0.5 font-mono text-[10px] text-muted"
                    :title="formatDateTime(step.time)"
                  >
                    {{ formatTime(step.time) }}
                  </span>
                </div>
                <div
                  v-if="index < timelineSteps.length - 1"
                  :class="[
                    'mt-[5px] h-px flex-1',
                    connectorClass(index),
                  ]"
                ></div>
              </template>
            </div>
          </div>

          <div
            class="flex items-center gap-8 md:border-l md:border-rule md:pl-8"
          >
            <div>
              <p class="text-[11px] text-muted">Runtime</p>
              <p
                class="mt-1 font-mono text-[18px] font-semibold tabular-nums text-ink"
              >
                {{ runtimeText }}
              </p>
            </div>
            <div>
              <p class="flex items-center gap-1.5 text-[11px] text-muted">
                <span
                  v-if="job.exit_code !== null && job.exit_code !== 0"
                  class="status-dot tone-alarm"
                  aria-hidden="true"
                ></span>
                Exit code
              </p>
              <p
                class="mt-1 font-mono text-[18px] font-semibold tabular-nums text-ink"
              >
                {{ job.exit_code !== null ? job.exit_code : '—' }}
              </p>
            </div>
          </div>
        </div>
      </section>

      <div class="mb-5 grid grid-cols-1 gap-5 lg:grid-cols-2">
        <section class="instrument-panel" aria-label="Job details">
          <div class="panel-header">
            <h2 class="panel-title">Job</h2>
          </div>
          <dl class="mt-4 space-y-4">
            <div>
              <dt class="text-[11px] text-muted">Task</dt>
              <dd class="mt-1 text-[12px] text-ink">
                {{ job.task_name || '—' }}
                <span class="font-mono text-[11px] text-muted">
                  #{{ job.task_id }}
                </span>
              </dd>
            </div>
            <div>
              <dt class="text-[11px] text-muted">Runner</dt>
              <dd class="mt-1">
                <RunnerIdBadge :runner-id="job.runner_id || ''" />
              </dd>
            </div>
            <div v-if="job.message && !failureBanner">
              <dt class="text-[11px] text-muted">Execution message</dt>
              <dd class="mt-1 break-words text-[12px] text-ink">
                {{ job.message }}
              </dd>
            </div>
          </dl>
        </section>

        <section class="instrument-panel" aria-label="Task specification">
          <div class="panel-header">
            <h2 class="panel-title">Task spec</h2>
          </div>

          <div v-if="taskSpec" class="mt-4 space-y-4">
            <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <p class="text-[11px] text-muted">Name</p>
                <p class="mt-1 break-words text-[12px] text-ink">
                  {{ taskSpec.name || '—' }}
                </p>
              </div>
              <div>
                <p class="text-[11px] text-muted">Image</p>
                <p class="mt-1 break-all font-mono text-[11px] text-ink">
                  {{ taskSpec.image || '—' }}
                </p>
              </div>
            </div>

            <div>
              <p class="text-[11px] text-muted">Command</p>
              <p class="mt-1 break-all font-mono text-[11px] text-ink">
                {{ taskSpec.cmd || '—' }}
              </p>
            </div>

            <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <p class="text-[11px] text-muted">Workspace</p>
                <p class="mt-1 break-all font-mono text-[11px] text-ink">
                  {{ taskSpec.workspace || '—' }}
                </p>
              </div>
              <div>
                <p class="text-[11px] text-muted">Limits</p>
                <p class="mt-1 text-[12px] text-ink">
                  <span v-if="specLimits.cpu">CPU {{ specLimits.cpu }}</span>
                  <span
                    v-if="
                      specLimits.cpu &&
                      (specLimits.mem || specLimits.timeout_seconds)
                    "
                  >
                    ,
                  </span>
                  <span v-if="specLimits.mem">memory {{ specLimits.mem }}</span>
                  <span
                    v-if="specLimits.mem && specLimits.timeout_seconds"
                  >
                    ,
                  </span>
                  <span v-if="specLimits.timeout_seconds">
                    timeout {{ specLimits.timeout_seconds }}s
                  </span>
                  <span
                    v-if="
                      !specLimits.cpu &&
                      !specLimits.mem &&
                      !specLimits.timeout_seconds
                    "
                  >
                    —
                  </span>
                </p>
              </div>
            </div>

            <div v-if="specEnvEntries.length > 0">
              <button
                class="btn btn--ghost btn--sm"
                @click="showEnv = !showEnv"
              >
                Environment ({{ specEnvEntries.length }})
                <svg
                  :class="[
                    'h-3.5 w-3.5 transition-transform',
                    showEnv ? 'rotate-90' : '',
                  ]"
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
              </button>
              <ul v-if="showEnv" class="code-block mt-2 space-y-1">
                <li
                  v-for="[key, value] in specEnvEntries"
                  :key="key"
                  class="break-all"
                >
                  {{ key }}={{ value }}
                </li>
              </ul>
            </div>
          </div>

          <p v-else class="mt-4 text-[12px] text-muted">
            Task spec unavailable or could not be parsed.
          </p>
        </section>
      </div>

      <LogViewer
        ref="logViewerRef"
        :key="`log-viewer-${jobId}`"
        :job-id="jobId"
        :auto-scroll="job.status === 'running'"
        :watch-files="watchFiles"
        class="mb-5"
      />

      <section class="instrument-panel" aria-label="Job artifacts">
        <div class="panel-header">
          <h2 class="panel-title">Artifacts</h2>
          <span
            v-if="!artifactsError"
            class="font-mono text-[11px] text-muted"
          >
            {{ artifacts.length }}
          </span>
        </div>

        <div
          v-if="artifactsError"
          class="notice notice--error mt-4"
          role="alert"
        >
          Failed to load artifacts: {{ artifactsError }}
        </div>

        <EmptyState
          v-else-if="artifacts.length === 0"
          title="No artifacts uploaded yet"
          description="Files listed in the task's artifacts config appear here after the job finishes."
        />

        <table v-else class="data-table mt-2">
          <thead>
            <tr>
              <th>Path</th>
              <th class="w-24 text-right">Size</th>
              <th class="w-28 text-right">Download</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="artifact in artifacts" :key="artifact.path">
              <td class="break-all font-mono text-[11px]">
                {{ artifact.path }}
              </td>
              <td class="numeric text-right text-muted">
                {{ formatSize(artifact.size) }}
              </td>
              <td class="text-right">
                <a
                  :href="artifactHref(artifact.path)"
                  :download="artifact.path.split('/').pop()"
                  class="text-link"
                >
                  Download
                </a>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </div>
</template>
