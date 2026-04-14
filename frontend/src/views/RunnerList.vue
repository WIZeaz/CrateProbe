<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import { getAdminToken } from '../services/adminAuth'

const router = useRouter()

const runners = ref([])
const loading = ref(true)
const creating = ref(false)
const deletingRunnerId = ref(null)
const error = ref('')
const createError = ref('')
const copySuccess = ref(false)
const createdToken = ref(null)

const form = ref({
  runner_id: '',
  description: '',
  tags: '',
  capacity_total: '',
})

const hasAdminToken = computed(() => Boolean(getAdminToken()))

async function fetchRunners() {
  loading.value = true
  error.value = ''

  if (!hasAdminToken.value) {
    router.replace('/settings')
    return
  }

  try {
    runners.value = await api.getRunners()
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
  if (!runner.enabled) return 'disabled'
  if (!runner.last_seen_at) return 'ready'

  const seen = new Date(runner.last_seen_at)
  if (Number.isNaN(seen.getTime())) return 'ready'

  const secondsAgo = (Date.now() - seen.getTime()) / 1000
  if (secondsAgo <= 120) return 'online'
  return 'idle'
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
    payload.capacity_total = Number(form.value.capacity_total)
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

  creating.value = true
  try {
    const response = await api.createRunner(mapCreatePayload())
    createdToken.value = response.token || null
    copySuccess.value = false
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
  } catch {
    copySuccess.value = false
  }
}

async function deleteRunner(runner) {
  if (!confirm(`Delete runner "${runner.runner_id}"? This will disable it.`)) {
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

onMounted(fetchRunners)
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Runners</h1>
    </div>

    <div class="grid grid-cols-1 xl:grid-cols-3 gap-6">
      <section class="bento-card xl:col-span-1">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Create Runner</h2>
        <form @submit.prevent="createRunner" class="space-y-4">
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
      </section>

      <section class="bento-card xl:col-span-2" v-if="createdToken">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 class="text-lg font-semibold text-gray-900">Runner token (one-time view)</h2>
            <p class="text-sm text-gray-600 mt-1">
              Copy and save this now. You will not be able to view it again.
            </p>
          </div>
          <button
            @click="createdToken = null"
            type="button"
            class="px-3 py-1.5 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
          >
            Dismiss
          </button>
        </div>

        <div class="mt-4 p-3 rounded-lg border border-gray-200 bg-gray-50 font-mono text-sm break-all">
          {{ createdToken }}
        </div>

        <div class="mt-3 flex items-center gap-3">
          <button
            @click="copyToken"
            type="button"
            class="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 transition-colors"
          >
            Copy Token
          </button>
          <span v-if="copySuccess" class="text-sm text-green-700">Copied to clipboard</span>
          <span v-else class="text-sm text-gray-500">Use this token as `RUNNER_TOKEN`.</span>
        </div>
      </section>
    </div>

    <section class="bento-card overflow-x-auto mt-6">
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
          <tr v-for="runner in runners" :key="runner.runner_id" class="hover:bg-gray-50 transition-colors">
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">{{ runner.runner_id }}</td>
            <td class="px-4 py-3 whitespace-nowrap">
              <span
                class="status-badge"
                :class="{
                  'status-running': runnerStatus(runner) === 'online',
                  'status-pending': runnerStatus(runner) === 'ready' || runnerStatus(runner) === 'idle',
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
              <button
                @click="deleteRunner(runner)"
                :disabled="deletingRunnerId === runner.runner_id"
                class="px-3 py-1.5 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
              >
                {{ deletingRunnerId === runner.runner_id ? 'Deleting...' : 'Delete' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
