<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import { getAdminToken } from '../services/adminAuth'
import RunnerMetricsChart from '../components/RunnerMetricsChart.vue'

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

function formatDate(value) {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Unknown'
  return date.toLocaleString()
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
  if (value === null || value === undefined || value === '') return '-'
  return value
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
    <div class="flex items-center justify-between mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Runners</h1>
    </div>

    <div class="grid grid-cols-1 xl:grid-cols-3 gap-6">
      <!-- Left: Create Runner -->
      <section class="bento-card xl:col-span-1 flex flex-col">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Create Runner</h2>
        <form @submit.prevent="createRunner" class="space-y-4 flex-1">
          <div>
            <label for="runner_id" class="block text-sm font-medium text-gray-700 mb-1">Runner ID *</label>
            <input
              id="runner_id"
              v-model="form.runner_id"
              type="text"
              placeholder="edge-runner-01"
              class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              :disabled="creating"
            />
          </div>

          <div>
            <label for="description" class="block text-sm font-medium text-gray-700 mb-1">Description (optional)</label>
            <input
              id="description"
              v-model="form.description"
              type="text"
              placeholder="Staging runner on node-a"
              class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              :disabled="creating"
            />
          </div>

          <div>
            <label for="tags" class="block text-sm font-medium text-gray-700 mb-1">Tags (optional)</label>
            <input
              id="tags"
              v-model="form.tags"
              type="text"
              placeholder="linux, x86_64, us-east"
              class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              :disabled="creating"
            />
          </div>

          <div>
            <label for="capacity_total" class="block text-sm font-medium text-gray-700 mb-1">Capacity (optional)</label>
            <input
              id="capacity_total"
              v-model.number="form.capacity_total"
              type="number"
              min="1"
              step="1"
              placeholder="4"
              class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              :disabled="creating"
            />
          </div>

          <div v-if="createError" class="bg-red-50 border border-red-200 text-red-800 px-3 py-2 rounded-lg text-sm">
            {{ createError }}
          </div>

          <button
            type="submit"
            class="w-full px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            :disabled="creating"
          >
            <span v-if="creating" class="spinner border-white"></span>
            {{ creating ? 'Creating...' : 'Create Runner' }}
          </button>
        </form>

        <section class="mt-4 pt-4 border-t border-gray-200" v-if="createdToken">
          <div class="flex items-start justify-between gap-4">
            <div>
              <h3 class="text-sm font-semibold text-gray-900">Runner token</h3>
              <p class="text-xs text-gray-600 mt-0.5">Copy and save this now.</p>
            </div>
            <button
              @click="createdToken = null"
              type="button"
              class="px-2 py-1 text-xs font-medium text-gray-700 bg-gray-100 rounded hover:bg-gray-200 transition-colors"
            >
              Dismiss
            </button>
          </div>

          <div class="mt-2 p-2 rounded-lg border border-gray-200 bg-gray-50 font-mono text-xs break-all">
            {{ createdToken }}
          </div>

          <div class="mt-2 flex items-center gap-2">
            <button
              @click="copyToken"
              type="button"
              class="px-3 py-1 text-xs font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 transition-colors"
            >
              Copy Token
            </button>
            <span v-if="copySuccess" class="text-xs text-green-700">Copied</span>
            <span v-else-if="copyError" class="text-xs text-red-700">{{ copyError }}</span>
          </div>
        </section>
      </section>

      <!-- Right: Runner List -->
      <section class="bento-card overflow-x-auto xl:col-span-2">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Runner List</h2>

        <div v-if="loading" class="flex justify-center py-10">
          <div class="spinner border-blue-500"></div>
        </div>

        <div v-else-if="error" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg text-sm">
          {{ error }}
        </div>

        <div v-else-if="runners.length === 0" class="text-center py-8 text-gray-500">
          No runners yet. Create your first runner.
        </div>

        <table v-else class="min-w-full divide-y divide-gray-200">
          <thead class="bg-gray-50">
            <tr>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Runner ID</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Last Seen</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Capacity</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Tags</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Enabled</th>
              <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-200">
            <tr
              v-for="runner in runners"
              :key="runner.runner_id"
              class="hover:bg-gray-50 transition-colors cursor-pointer"
              @click="selectRunner(runner)"
            >
              <td class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">{{ runner.runner_id }}</td>
              <td class="px-4 py-3 whitespace-nowrap">
                <span
                  class="status-badge"
                  :class="{
                    'status-running': runnerStatus(runner) === 'online',
                    'status-failed': runnerStatus(runner) === 'offline',
                    'status-cancelled': runnerStatus(runner) === 'disabled'
                  }"
                >
                  {{ runnerStatus(runner) }}
                </span>
              </td>
              <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-600">{{ formatDate(runner.last_seen_at || runner.last_seen) }}</td>
              <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">{{ normalizeCapacity(runner.capacity_total) }}</td>
              <td class="px-4 py-3 text-sm text-gray-700">
                <div v-if="parseTags(runner.tags).length > 0" class="flex flex-wrap gap-1">
                  <span
                    v-for="tag in parseTags(runner.tags)"
                    :key="`${runner.runner_id}-${tag}`"
                    class="inline-flex items-center px-2 py-0.5 text-xs bg-blue-50 text-blue-700 rounded-full"
                  >
                    {{ tag }}
                  </span>
                </div>
                <span v-else class="text-gray-400">-</span>
              </td>
              <td class="px-4 py-3 whitespace-nowrap text-sm">
                <span :class="runner.enabled ? 'text-green-700' : 'text-gray-500'">
                  {{ runner.enabled ? 'yes' : 'no' }}
                </span>
              </td>
              <td class="px-4 py-3 whitespace-nowrap text-sm">
                <div class="flex items-center gap-2">
                  <button
                    v-if="runner.enabled"
                    @click.stop="disableRunner(runner)"
                    :disabled="disablingRunnerId === runner.runner_id"
                    class="px-3 py-1.5 text-sm font-medium text-white bg-amber-600 rounded-lg hover:bg-amber-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
                  >
                    {{ disablingRunnerId === runner.runner_id ? 'Disabling...' : 'Disable' }}
                  </button>
                  <button
                    v-else
                    @click.stop="enableRunner(runner)"
                    :disabled="enablingRunnerId === runner.runner_id"
                    class="px-3 py-1.5 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
                  >
                    {{ enablingRunnerId === runner.runner_id ? 'Enabling...' : 'Enable' }}
                  </button>
                  <button
                    @click.stop="deleteRunner(runner)"
                    :disabled="deletingRunnerId === runner.runner_id"
                    class="px-3 py-1.5 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
                  >
                    {{ deletingRunnerId === runner.runner_id ? 'Deleting...' : 'Delete' }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <section v-if="selectedRunner" class="bento-card mt-6">
      <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
        <div>
          <h2 class="text-lg font-semibold text-gray-900">Runner Details</h2>
          <p class="text-sm text-gray-600 mt-1">{{ selectedRunner.runner_id }}</p>
        </div>
        <div class="flex items-center gap-2">
          <button
            v-for="windowValue in ['1h', '6h', '24h']"
            :key="windowValue"
            @click="setMetricsWindow(windowValue)"
            class="px-3 py-1.5 text-xs font-medium rounded-lg border"
            :class="metricsWindow === windowValue ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'"
          >
            {{ windowValue }}
          </button>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4 text-sm">
        <div>
          <p class="text-gray-500">Runner ID</p>
          <p class="font-medium text-gray-900">{{ selectedRunner.runner_id }}</p>
        </div>
        <div>
          <p class="text-gray-500">Health</p>
          <span
            class="status-badge"
            :class="{
              'status-running': selectedRunnerHealth() === 'online',
              'status-failed': selectedRunnerHealth() === 'offline',
              'status-cancelled': selectedRunnerHealth() === 'disabled'
            }"
          >
            {{ selectedRunnerHealth() }}
          </span>
        </div>
        <div>
          <p class="text-gray-500">Enabled</p>
          <p class="font-medium text-gray-900">{{ selectedRunner.enabled ? 'yes' : 'no' }}</p>
        </div>
        <div>
          <p class="text-gray-500">Last Seen</p>
          <p class="font-medium text-gray-900">{{ formatDate(selectedRunner.last_seen_at || selectedRunner.last_seen) }}</p>
        </div>
      </div>

      <div v-if="metricsLoading" class="flex justify-center py-8">
        <div class="spinner border-blue-500"></div>
      </div>

      <div v-else-if="metricsError" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg text-sm">
        {{ metricsError }}
      </div>

      <div v-else-if="metricsSeries.length === 0" class="text-sm text-gray-500 py-6 text-center">
        No monitoring data yet
      </div>

      <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="bg-gray-50 rounded-lg overflow-hidden">
          <div class="px-3 pt-2">
            <p class="text-xs font-medium text-gray-500 uppercase tracking-wider">CPU %</p>
          </div>
          <RunnerMetricsChart :points="metricsSeries" field="cpu_percent" :max-y="100" stroke="#2563eb" />
        </div>
        <div class="bg-gray-50 rounded-lg overflow-hidden">
          <div class="px-3 pt-2">
            <p class="text-xs font-medium text-gray-500 uppercase tracking-wider">Memory %</p>
          </div>
          <RunnerMetricsChart :points="metricsSeries" field="memory_percent" :max-y="100" stroke="#16a34a" />
        </div>
        <div class="bg-gray-50 rounded-lg overflow-hidden">
          <div class="px-3 pt-2">
            <p class="text-xs font-medium text-gray-500 uppercase tracking-wider">Disk %</p>
          </div>
          <RunnerMetricsChart :points="metricsSeries" field="disk_percent" :max-y="100" stroke="#d97706" />
        </div>
        <div class="bg-gray-50 rounded-lg overflow-hidden">
          <div class="px-3 pt-2">
            <p class="text-xs font-medium text-gray-500 uppercase tracking-wider">Active Tasks</p>
          </div>
          <RunnerMetricsChart :points="metricsSeries" field="active_tasks" :max-y="activeTasksMaxY" stroke="#7c3aed" />
        </div>
      </div>
    </section>
  </div>
</template>
