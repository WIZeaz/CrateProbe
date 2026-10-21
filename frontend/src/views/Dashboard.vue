<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import api from '../services/api'
import websocket from '../services/websocket'
import PipelinePanel from '../components/dashboard/PipelinePanel.vue'
import RecentJobsPanel from '../components/dashboard/RecentJobsPanel.vue'
import RunnerFleetPanel from '../components/dashboard/RunnerFleetPanel.vue'
import PageHeader from '../components/ui/PageHeader.vue'

function emptyStats() {
  return {
    total: 0,
    pending: 0,
    running: 0,
    completed: 0,
    failed: 0,
    cancelled: 0,
    timeout: 0,
    oom: 0,
    runner_failed: 0,
  }
}

function describeError(error, fallback) {
  return error.response?.data?.detail || error.message || fallback
}

function initialConnectionState() {
  if (websocket.isOpen) {
    return 'live'
  }
  if (websocket.isConnecting) {
    return 'connecting'
  }
  return 'polling'
}

const stats = ref(emptyStats())
const recentJobs = ref([])
const runnerOverview = ref([])

const statsLoading = ref(true)
const jobsLoading = ref(true)
const runnerLoading = ref(true)

const statsError = ref('')
const jobsError = ref('')
const runnerError = ref('')
const runnerAuthError = ref(false)

const lastUpdatedAt = ref(null)
const connectionState = ref(initialConnectionState())

let refreshInterval = null
let refreshTimer = null
let connectionTimer = null

const connection = computed(() => {
  const active =
    Number(stats.value.running || 0) + Number(stats.value.pending || 0) > 0
  if (connectionState.value === 'live') {
    return { label: 'Live', tone: 'signal', pulse: active }
  }
  if (connectionState.value === 'polling') {
    return { label: 'Polling', tone: 'warn', pulse: false }
  }
  return { label: 'Connecting', tone: 'neutral', pulse: false }
})

const updatedLabel = computed(() => {
  if (!lastUpdatedAt.value) {
    return 'Waiting for data'
  }
  return `Updated ${lastUpdatedAt.value.toLocaleTimeString('en-GB', {
    hour12: false,
  })}`
})

async function fetchStats({ silent = false } = {}) {
  if (!silent) {
    statsLoading.value = true
  }
  statsError.value = ''
  try {
    stats.value = { ...emptyStats(), ...(await api.getDashboardStats()) }
    lastUpdatedAt.value = new Date()
  } catch (error) {
    statsError.value = describeError(error, 'Failed to load dashboard stats.')
  } finally {
    if (!silent) {
      statsLoading.value = false
    }
  }
}

async function fetchRecentJobs({ silent = false } = {}) {
  if (!silent) {
    jobsLoading.value = true
  }
  jobsError.value = ''
  try {
    const data = await api.getJobs({ pageSize: 10 })
    recentJobs.value = data.items
  } catch (error) {
    jobsError.value = describeError(error, 'Failed to load recent jobs.')
  } finally {
    if (!silent) {
      jobsLoading.value = false
    }
  }
}

async function fetchRunnerOverview({ silent = false } = {}) {
  if (!silent) {
    runnerLoading.value = true
  }
  runnerError.value = ''
  runnerAuthError.value = false
  try {
    runnerOverview.value = await api.getRunnerOverview()
  } catch (error) {
    runnerAuthError.value = error.response?.status === 403
    runnerError.value = runnerAuthError.value
      ? describeError(error, 'Admin token is missing or invalid.')
      : describeError(error, 'Failed to load runner status.')
  } finally {
    if (!silent) {
      runnerLoading.value = false
    }
  }
}

function scheduleRefresh() {
  if (refreshTimer) {
    clearTimeout(refreshTimer)
  }
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    fetchStats({ silent: true })
    fetchRecentJobs({ silent: true })
  }, 400)
}

function handleJobUpdate() {
  scheduleRefresh()
}

function clearConnectionTimer() {
  if (connectionTimer) {
    clearTimeout(connectionTimer)
    connectionTimer = null
  }
}

function handleConnected() {
  clearConnectionTimer()
  connectionState.value = 'live'
}

function handleDisconnected() {
  clearConnectionTimer()
  connectionState.value = 'polling'
}

onMounted(() => {
  fetchStats()
  fetchRecentJobs()
  fetchRunnerOverview()

  websocket.connect('/ws/dashboard')
  websocket.on('job_update', handleJobUpdate)
  websocket.on('job_created', handleJobUpdate)
  websocket.on('job_completed', handleJobUpdate)
  websocket.on('connected', handleConnected)
  websocket.on('disconnected', handleDisconnected)

  connectionState.value = initialConnectionState()
  if (connectionState.value === 'connecting') {
    connectionTimer = setTimeout(() => {
      connectionTimer = null
      if (connectionState.value === 'connecting') {
        connectionState.value = 'polling'
      }
    }, 3000)
  }

  refreshInterval = setInterval(() => {
    fetchStats({ silent: true })
    fetchRecentJobs({ silent: true })
    fetchRunnerOverview({ silent: true })
  }, 10000)
})

onUnmounted(() => {
  websocket.off('job_update', handleJobUpdate)
  websocket.off('job_created', handleJobUpdate)
  websocket.off('job_completed', handleJobUpdate)
  websocket.off('connected', handleConnected)
  websocket.off('disconnected', handleDisconnected)

  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
  if (refreshTimer) {
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
  clearConnectionTimer()
})
</script>

<template>
  <div class="dashboard-view">
    <PageHeader title="Dashboard">
      <template #actions>
        <div class="flex items-center gap-3">
          <span :class="['status-mark', `tone-${connection.tone}`]">
            <span
              :class="[
                'signal-lamp',
                { 'signal-lamp--pulse': connection.pulse },
              ]"
              aria-hidden="true"
            ></span>
            <span>{{ connection.label }}</span>
          </span>
          <span class="font-mono text-[11px] text-muted">{{ updatedLabel }}</span>
        </div>
      </template>
    </PageHeader>

    <div class="dashboard-stack">
      <PipelinePanel
        :stats="stats"
        :loading="statsLoading"
        :error="statsError"
        @retry="fetchStats"
      />

      <div class="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div class="min-w-0 lg:col-span-1">
          <RunnerFleetPanel
            :runners="runnerOverview"
            :loading="runnerLoading"
            :error="runnerError"
            :auth-error="runnerAuthError"
            @retry="fetchRunnerOverview"
          />
        </div>
        <div class="min-w-0 lg:col-span-2">
          <RecentJobsPanel
            :jobs="recentJobs"
            :loading="jobsLoading"
            :error="jobsError"
            @retry="fetchRecentJobs"
          />
        </div>
      </div>
    </div>
  </div>
</template>
