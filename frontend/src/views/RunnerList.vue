<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import { getAdminToken } from '../services/adminAuth'
import RunnerMetricsChart from '../components/RunnerMetricsChart.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import StatusMark from '../components/ui/StatusMark.vue'
import {
  formatDateTime,
  formatRelativeTime,
} from '../utils/format.js'
import { healthLabel, healthTone } from '../utils/status.js'

const router = useRouter()

const runners = ref([])
const loading = ref(true)
const creating = ref(false)
const deletingRunnerId = ref(null)
const disablingRunnerId = ref(null)
const enablingRunnerId = ref(null)
const error = ref('')
const createError = ref('')
const copySuccess = ref(false)
const copyError = ref('')
const createdToken = ref(null)
const selectedRunnerId = ref('')
const metricsWindow = ref('1h')
const metricsLoading = ref(false)
const metricsError = ref('')
const metricsData = ref(null)
let metricsInterval = null

const form = ref({
  runner_id: '',
  description: '',
  tags: '',
  capacity_total: '',
})

const hasAdminToken = computed(() => Boolean(getAdminToken()))
const selectedRunner = computed(() => {
  if (!selectedRunnerId.value) return null
  return runners.value.find(r => r.runner_id === selectedRunnerId.value) || null
})
const metricsSeries = computed(() => metricsData.value?.series || [])
const activeTasksMaxY = computed(() => {
  const values = metricsSeries.value.map(item => Number(item.active_tasks || 0))
  const maxValue = values.length ? Math.max(...values) : 0
  return Math.max(1, maxValue)
})

async function fetchRunners() {
  loading.value = true
  error.value = ''

  if (!hasAdminToken.value) {
    loading.value = false
    router.replace('/settings')
    return
  }

  try {
    runners.value = await api.getRunners()
    if (selectedRunnerId.value && !runners.value.some(r => r.runner_id === selectedRunnerId.value)) {
      selectedRunnerId.value = ''
      metricsData.value = null
      metricsError.value = ''
    }
  } catch (err) {
    if (err.response?.status === 403) {
      error.value = 'Admin token is invalid or expired. Update the token and try again.'
    } else {
      error.value = err.response?.data?.detail || err.message || 'Failed to load runners.'
    }
  } finally {
    loading.value = false
  }
}

function lastSeenValue(runner) {
  return runner.last_seen_at || runner.last_seen || null
}

function lastSeenLabel(runner) {
  const value = lastSeenValue(runner)
  return value ? formatRelativeTime(value) : 'Never'
}

function parseTags(raw) {
  if (Array.isArray(raw)) return raw
  if (typeof raw === 'string') {
    return raw
      .split(',')
      .map(tag => tag.trim())
      .filter(Boolean)
  }
  return []
}

function runnerStatus(runner) {
  if (runner.health_status === 'online' || runner.health_status === 'offline' || runner.health_status === 'disabled') {
    return runner.health_status
  }

  if (!runner.enabled) return 'disabled'
  const lastSeen = runner.last_seen_at || runner.last_seen
  if (!lastSeen) return 'offline'

  const seen = new Date(lastSeen)
  if (Number.isNaN(seen.getTime())) return 'offline'

  const secondsAgo = (Date.now() - seen.getTime()) / 1000
  if (secondsAgo <= 120) return 'online'
  return 'offline'
}

function selectedRunnerHealth() {
  return metricsData.value?.runner?.health_status || (selectedRunner.value ? runnerStatus(selectedRunner.value) : 'offline')
}

function normalizeCapacity(value) {
  if (value === null || value === undefined || value === '') return '—'
  return value
}

function closeDetails() {
  selectedRunnerId.value = ''
  metricsData.value = null
  metricsError.value = ''
}

function mapCreatePayload() {
  const payload = {
    runner_id: form.value.runner_id.trim(),
  }

  if (form.value.description.trim()) {
    payload.description = form.value.description.trim()
  }

  const tags = parseTags(form.value.tags)
  if (tags.length > 0) {
    payload.tags = tags
  }

  if (form.value.capacity_total !== '') {
    const capacity = Number(form.value.capacity_total)
    if (Number.isFinite(capacity) && capacity > 0) {
      payload.capacity_total = capacity
    }
  }

  return payload
}

async function createRunner() {
  createError.value = ''

  if (!form.value.runner_id.trim()) {
    createError.value = 'Runner ID is required.'
    return
  }

  if (form.value.capacity_total !== '' && Number(form.value.capacity_total) <= 0) {
    createError.value = 'Capacity must be a positive number.'
    return
  }

  if (form.value.capacity_total !== '' && !Number.isFinite(Number(form.value.capacity_total))) {
    createError.value = 'Capacity must be a positive number.'
    return
  }

  creating.value = true
  try {
    const response = await api.createRunner(mapCreatePayload())
    createdToken.value = response.token || null
    copySuccess.value = false
    copyError.value = ''
    form.value = {
      runner_id: '',
      description: '',
      tags: '',
      capacity_total: '',
    }
    await fetchRunners()
  } catch (err) {
    if (err.response?.status === 403) {
      createError.value = 'Admin token is invalid or missing. Update token and retry.'
    } else {
      createError.value = err.response?.data?.detail || err.message || 'Failed to create runner.'
    }
  } finally {
    creating.value = false
  }
}

async function copyToken() {
  if (!createdToken.value) return

  try {
    await navigator.clipboard.writeText(createdToken.value)
    copySuccess.value = true
    copyError.value = ''
  } catch {
    copySuccess.value = false
    copyError.value = 'Copy failed. Please copy the token manually.'
  }
}

async function deleteRunner(runner) {
  if (!confirm(`Permanently delete runner "${runner.runner_id}"? This action cannot be undone.`)) {
    return
  }

  deletingRunnerId.value = runner.runner_id
  try {
    await api.deleteRunner(runner.runner_id)
    await fetchRunners()
  } catch (err) {
    const message = err.response?.data?.detail || err.message || 'Failed to delete runner.'
    alert(message)
  } finally {
    deletingRunnerId.value = null
  }
}

async function disableRunner(runner) {
  if (!confirm(`Disable runner "${runner.runner_id}"? It will stop claiming new tasks until re-enabled.`)) {
    return
  }

  disablingRunnerId.value = runner.runner_id
  try {
    await api.disableRunner(runner.runner_id)
    await fetchRunners()
  } catch (err) {
    const message = err.response?.data?.detail || err.message || 'Failed to disable runner.'
    alert(message)
  } finally {
    disablingRunnerId.value = null
  }
}

async function enableRunner(runner) {
  enablingRunnerId.value = runner.runner_id
  try {
    await api.enableRunner(runner.runner_id)
    await fetchRunners()
  } catch (err) {
    const message = err.response?.data?.detail || err.message || 'Failed to enable runner.'
    alert(message)
  } finally {
    enablingRunnerId.value = null
  }
}

async function fetchRunnerMetrics(isRefresh = false) {
  if (!selectedRunnerId.value) return

  const requestRunnerId = selectedRunnerId.value
  const requestWindow = metricsWindow.value

  if (!isRefresh) {
    metricsLoading.value = true
  }
  metricsError.value = ''

  try {
    const response = await api.getRunnerMetrics(requestRunnerId, requestWindow)
    if (selectedRunnerId.value === requestRunnerId && metricsWindow.value === requestWindow) {
      metricsData.value = response
    }
  } catch (err) {
    if (selectedRunnerId.value === requestRunnerId && metricsWindow.value === requestWindow) {
      if (err.response?.status === 403) {
        metricsError.value = 'Admin token is invalid or missing. Update token and retry.'
      } else {
        metricsError.value = err.response?.data?.detail || err.message || 'Failed to load runner metrics.'
      }
    }
  } finally {
    if (!isRefresh) {
      metricsLoading.value = false
    }
  }
}

function selectRunner(runner) {
  if (selectedRunnerId.value === runner.runner_id) {
    selectedRunnerId.value = ''
    metricsData.value = null
    metricsError.value = ''
    return
  }

  selectedRunnerId.value = runner.runner_id
  metricsWindow.value = '1h'
  fetchRunnerMetrics()
}

function setMetricsWindow(windowValue) {
  metricsWindow.value = windowValue
  fetchRunnerMetrics()
}

function startMetricsRefresh() {
  stopMetricsRefresh()
  metricsInterval = setInterval(() => {
    fetchRunnerMetrics(true)
  }, 10000)
}

function stopMetricsRefresh() {
  if (metricsInterval) {
    clearInterval(metricsInterval)
    metricsInterval = null
  }
}

onMounted(() => {
  fetchRunners()
  startMetricsRefresh()
})

onUnmounted(() => {
  stopMetricsRefresh()
})
</script>

<template>
  <div>
    <PageHeader
      title="Runners"
      description="Manage runner capacity and inspect live metrics."
    />

    <div class="grid grid-cols-1 gap-5 xl:grid-cols-3">
      <section class="instrument-panel flex flex-col xl:col-span-1">
        <div class="panel-header">
          <h2 class="panel-title">Create runner</h2>
        </div>

        <form class="mt-4 flex-1 space-y-4" @submit.prevent="createRunner">
          <div>
            <label for="runner_id" class="ui-label">Runner ID *</label>
            <input
              id="runner_id"
              v-model="form.runner_id"
              type="text"
              placeholder="edge-runner-01"
              class="ui-input"
              :disabled="creating"
            />
          </div>

          <div>
            <label for="description" class="ui-label">
              Description (optional)
            </label>
            <input
              id="description"
              v-model="form.description"
              type="text"
              placeholder="Staging runner on node-a"
              class="ui-input"
              :disabled="creating"
            />
          </div>

          <div>
            <label for="tags" class="ui-label">Tags (optional)</label>
            <input
              id="tags"
              v-model="form.tags"
              type="text"
              placeholder="linux, x86_64, us-east"
              class="ui-input"
              :disabled="creating"
            />
          </div>

          <div>
            <label for="capacity_total" class="ui-label">
              Capacity (optional)
            </label>
            <input
              id="capacity_total"
              v-model.number="form.capacity_total"
              type="number"
              min="1"
              step="1"
              placeholder="4"
              class="ui-input"
              :disabled="creating"
            />
          </div>

          <div v-if="createError" class="notice notice--error" role="alert">
            {{ createError }}
          </div>

          <button
            type="submit"
            class="btn btn--primary w-full"
            :disabled="creating"
          >
            <span v-if="creating" class="spinner border-white"></span>
            {{ creating ? 'Creating...' : 'Create runner' }}
          </button>
        </form>

        <section v-if="createdToken" class="mt-5 border-t border-rule pt-4">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 class="text-[12px] font-semibold text-ink">Runner token</h3>
              <p class="mt-0.5 text-[11px] text-muted">
                Copy and save this now.
              </p>
            </div>
            <button
              type="button"
              class="btn btn--ghost btn--sm"
              @click="createdToken = null"
            >
              Dismiss
            </button>
          </div>

          <div
            class="mt-2 break-all rounded-[3px] border border-rule bg-bench p-2 font-mono text-[11px] leading-relaxed text-ink"
          >
            {{ createdToken }}
          </div>

          <div class="mt-2 flex flex-wrap items-center gap-2">
            <button type="button" class="btn btn--primary btn--sm" @click="copyToken">
              Copy token
            </button>
            <span
              v-if="copySuccess"
              class="inline-flex items-center gap-1.5 text-[11px] text-muted"
            >
              <span class="status-dot tone-good" aria-hidden="true"></span>
              Copied
            </span>
            <span
              v-else-if="copyError"
              class="inline-flex items-center gap-1.5 text-[11px] text-muted"
            >
              <span class="status-dot tone-alarm" aria-hidden="true"></span>
              {{ copyError }}
            </span>
          </div>
        </section>
      </section>

      <section class="instrument-panel xl:col-span-2">
        <div class="panel-header">
          <h2 class="panel-title">Runner list</h2>
          <span
            v-if="!loading && !error"
            class="font-mono text-[11px] text-muted"
          >
            {{ runners.length }}
          </span>
        </div>

        <div
          v-if="loading"
          class="mt-4 space-y-3"
          aria-busy="true"
          aria-label="Loading runners"
        >
          <div v-for="index in 4" :key="index" class="flex items-center gap-4">
            <div class="skeleton h-3 w-28"></div>
            <div class="skeleton h-3 flex-1"></div>
            <div class="skeleton h-3 w-20"></div>
            <div class="skeleton h-3 w-16"></div>
          </div>
        </div>

        <div v-else-if="error" class="notice notice--error mt-4" role="alert">
          <div class="min-w-0 flex-1">
            <p class="notice__title">Couldn't load runners.</p>
            <p class="notice__detail">{{ error }}</p>
          </div>
          <button class="btn btn--ghost btn--sm" @click="fetchRunners">
            Try again
          </button>
        </div>

        <EmptyState
          v-else-if="runners.length === 0"
          title="No runners yet"
          description="Create a runner to start processing jobs."
        />

        <template v-else>
          <div class="mt-2 hidden overflow-x-auto lg:block">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Runner ID</th>
                  <th class="w-28">Status</th>
                  <th class="w-24">Last seen</th>
                  <th class="w-20">Capacity</th>
                  <th class="w-24">Enabled</th>
                  <th class="w-52 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="runner in runners"
                  :key="runner.runner_id"
                  class="cursor-pointer"
                  :class="{
                    'is-selected': selectedRunnerId === runner.runner_id,
                  }"
                  @click="selectRunner(runner)"
                >
                  <td class="min-w-0">
                    <p
                      class="truncate font-mono text-[12px] font-medium text-ink"
                      :title="runner.runner_id"
                    >
                      {{ runner.runner_id }}
                    </p>
                    <p
                      v-if="runner.description"
                      class="mt-0.5 truncate text-[11px] text-muted"
                    >
                      {{ runner.description }}
                    </p>
                    <div
                      v-if="parseTags(runner.tags).length > 0"
                      class="mt-1 flex flex-wrap gap-1"
                    >
                      <span
                        v-for="tag in parseTags(runner.tags)"
                        :key="`${runner.runner_id}-${tag}`"
                        class="ui-tag"
                      >
                        {{ tag }}
                      </span>
                    </div>
                  </td>
                  <td class="whitespace-nowrap">
                    <StatusMark
                      :tone="healthTone(runnerStatus(runner))"
                      :label="healthLabel(runnerStatus(runner))"
                    />
                  </td>
                  <td
                    class="numeric whitespace-nowrap text-muted"
                    :title="formatDateTime(lastSeenValue(runner))"
                  >
                    {{ lastSeenLabel(runner) }}
                  </td>
                  <td class="numeric whitespace-nowrap text-muted">
                    {{ normalizeCapacity(runner.capacity_total) }}
                  </td>
                  <td>
                    <StatusMark
                      :tone="runner.enabled ? 'good' : 'neutral'"
                      :label="runner.enabled ? 'Enabled' : 'Disabled'"
                    />
                  </td>
                  <td class="whitespace-nowrap text-right" @click.stop>
                    <div class="flex items-center justify-end gap-2">
                      <button
                        v-if="runner.enabled"
                        class="btn btn--warn-ghost btn--sm"
                        :disabled="disablingRunnerId === runner.runner_id"
                        @click="disableRunner(runner)"
                      >
                        {{
                          disablingRunnerId === runner.runner_id
                            ? 'Disabling...'
                            : 'Disable'
                        }}
                      </button>
                      <button
                        v-else
                        class="btn btn--ghost btn--sm"
                        :disabled="enablingRunnerId === runner.runner_id"
                        @click="enableRunner(runner)"
                      >
                        {{
                          enablingRunnerId === runner.runner_id
                            ? 'Enabling...'
                            : 'Enable'
                        }}
                      </button>
                      <button
                        class="btn btn--danger-ghost btn--sm"
                        :disabled="deletingRunnerId === runner.runner_id"
                        @click="deleteRunner(runner)"
                      >
                        {{
                          deletingRunnerId === runner.runner_id
                            ? 'Deleting...'
                            : 'Delete'
                        }}
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <ul class="lg:hidden">
            <li
              v-for="runner in runners"
              :key="runner.runner_id"
              class="cursor-pointer border-b border-l-2 border-rule py-3 pl-3 transition-colors last:border-b-0"
              :class="{
                'bg-black/[0.02]': selectedRunnerId === runner.runner_id,
              }"
              :style="{
                borderLeftColor:
                  selectedRunnerId === runner.runner_id
                    ? 'var(--color-signal)'
                    : 'transparent',
              }"
              @click="selectRunner(runner)"
            >
              <div class="flex flex-wrap items-start justify-between gap-2">
                <div class="min-w-0">
                  <p class="truncate text-[13px] font-medium text-ink">
                    {{ runner.runner_id }}
                  </p>
                  <p
                    v-if="runner.description"
                    class="mt-0.5 text-[11px] text-muted"
                  >
                    {{ runner.description }}
                  </p>
                </div>
                <StatusMark
                  :tone="healthTone(runnerStatus(runner))"
                  :label="healthLabel(runnerStatus(runner))"
                />
              </div>

              <div class="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
                <span>Seen {{ lastSeenLabel(runner) }}</span>
                <span>Capacity {{ normalizeCapacity(runner.capacity_total) }}</span>
                <StatusMark
                  :tone="runner.enabled ? 'good' : 'neutral'"
                  :label="runner.enabled ? 'Enabled' : 'Disabled'"
                />
              </div>

              <div
                v-if="parseTags(runner.tags).length > 0"
                class="mt-1.5 flex flex-wrap gap-1"
              >
                <span
                  v-for="tag in parseTags(runner.tags)"
                  :key="`${runner.runner_id}-${tag}`"
                  class="ui-tag"
                >
                  {{ tag }}
                </span>
              </div>

              <div class="mt-2 flex flex-wrap gap-2" @click.stop>
                <button
                  v-if="runner.enabled"
                  class="btn btn--warn-ghost btn--sm"
                  :disabled="disablingRunnerId === runner.runner_id"
                  @click="disableRunner(runner)"
                >
                  {{
                    disablingRunnerId === runner.runner_id
                      ? 'Disabling...'
                      : 'Disable'
                  }}
                </button>
                <button
                  v-else
                  class="btn btn--ghost btn--sm"
                  :disabled="enablingRunnerId === runner.runner_id"
                  @click="enableRunner(runner)"
                >
                  {{
                    enablingRunnerId === runner.runner_id
                      ? 'Enabling...'
                      : 'Enable'
                  }}
                </button>
                <button
                  class="btn btn--danger-ghost btn--sm"
                  :disabled="deletingRunnerId === runner.runner_id"
                  @click="deleteRunner(runner)"
                >
                  {{
                    deletingRunnerId === runner.runner_id
                      ? 'Deleting...'
                      : 'Delete'
                  }}
                </button>
              </div>
            </li>
          </ul>
        </template>
      </section>
    </div>

    <section v-if="selectedRunner" class="instrument-panel panel-in mt-5" aria-label="Runner details">
      <div class="panel-header flex-wrap">
        <div>
          <h2 class="panel-title">Runner details</h2>
          <p class="mt-1 font-mono text-[11px] text-muted">
            {{ selectedRunner.runner_id }}
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button
            v-for="windowValue in ['1h', '6h', '24h']"
            :key="windowValue"
            class="btn btn--sm"
            :class="
              metricsWindow === windowValue ? 'btn--selected' : 'btn--ghost'
            "
            @click="setMetricsWindow(windowValue)"
          >
            {{ windowValue }}
          </button>
          <button class="btn btn--ghost btn--sm" @click="closeDetails">
            Close
          </button>
        </div>
      </div>

      <dl class="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <dt class="text-[11px] text-muted">Runner ID</dt>
          <dd class="mt-1 font-mono text-[12px] text-ink">
            {{ selectedRunner.runner_id }}
          </dd>
        </div>
        <div>
          <dt class="text-[11px] text-muted">Health</dt>
          <dd class="mt-1">
            <StatusMark
              :tone="healthTone(selectedRunnerHealth())"
              :label="healthLabel(selectedRunnerHealth())"
              size="md"
            />
          </dd>
        </div>
        <div>
          <dt class="text-[11px] text-muted">Enabled</dt>
          <dd class="mt-1">
            <StatusMark
              :tone="selectedRunner.enabled ? 'good' : 'neutral'"
              :label="selectedRunner.enabled ? 'Enabled' : 'Disabled'"
              size="md"
            />
          </dd>
        </div>
        <div>
          <dt class="text-[11px] text-muted">Last seen</dt>
          <dd
            class="mt-1 text-[12px] text-ink"
            :title="formatDateTime(lastSeenValue(selectedRunner))"
          >
            {{ lastSeenLabel(selectedRunner) }}
          </dd>
        </div>
      </dl>

      <div
        v-if="metricsLoading"
        class="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2"
        aria-busy="true"
        aria-label="Loading runner metrics"
      >
        <div v-for="index in 4" :key="index" class="chart-panel">
          <div class="skeleton h-3 w-16"></div>
          <div class="skeleton mt-3 h-28 w-full"></div>
        </div>
      </div>

      <div v-else-if="metricsError" class="notice notice--error mt-4" role="alert">
        {{ metricsError }}
      </div>

      <EmptyState
        v-else-if="metricsSeries.length === 0"
        title="No monitoring data yet"
        description="Metrics appear after the runner reports its first sample."
      />

      <div v-else class="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div class="chart-panel">
          <p class="mb-1 text-[11px] font-medium text-muted">CPU</p>
          <RunnerMetricsChart
            :points="metricsSeries"
            field="cpu_percent"
            :max-y="100"
            stroke="#1f55d8"
          />
        </div>
        <div class="chart-panel">
          <p class="mb-1 text-[11px] font-medium text-muted">Memory</p>
          <RunnerMetricsChart
            :points="metricsSeries"
            field="memory_percent"
            :max-y="100"
            stroke="#16794a"
          />
        </div>
        <div class="chart-panel">
          <p class="mb-1 text-[11px] font-medium text-muted">Disk</p>
          <RunnerMetricsChart
            :points="metricsSeries"
            field="disk_percent"
            :max-y="100"
            stroke="#8a5800"
          />
        </div>
        <div class="chart-panel">
          <p class="mb-1 text-[11px] font-medium text-muted">Active tasks</p>
          <RunnerMetricsChart
            :points="metricsSeries"
            field="active_tasks"
            :max-y="activeTasksMaxY"
            stroke="#6e4fa3"
          />
        </div>
      </div>
    </section>
  </div>
</template>
