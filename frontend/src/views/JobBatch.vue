<script setup>
import { ref, computed, nextTick, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'

const router = useRouter()
const textarea = ref('')
const loading = ref(false)
const progress = ref({ current: 0, total: 0 })
const logs = ref([])
const logContainer = ref(null)

// Task selector state
const tasks = ref([])
const tasksLoading = ref(true)
const tasksError = ref(null)
const selectedTaskId = ref(null)

async function fetchTasks() {
  tasksLoading.value = true
  tasksError.value = null
  try {
    tasks.value = await api.getTasks()
    if (tasks.value.length > 0) {
      selectedTaskId.value = tasks.value[0].id
    }
  } catch (err) {
    tasksError.value = err.response?.data?.detail || err.message
  } finally {
    tasksLoading.value = false
  }
}

// Parse input text into crate list
function parseInput(text) {
  const lines = text.split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)

  const parsed = []
  const errors = []

  lines.forEach((line, index) => {
    // Try comma-separated format first: crate_name, version
    let parts = line.split(',').map(p => p.trim())

    // If no comma found, try space-separated format: crate_name version
    if (parts.length === 1) {
      // Split by whitespace (handles multiple spaces)
      parts = line.split(/\s+/).filter(p => p.length > 0)
    }

    if (parts.length === 1) {
      // Format: crate_name
      parsed.push({ crate_name: parts[0], version: null })
    } else if (parts.length === 2) {
      // Format: crate_name, version or crate_name version
      parsed.push({ crate_name: parts[0], version: parts[1] })
    } else {
      // Invalid format
      errors.push({ line: index + 1, text: line, reason: 'Invalid format (expected: crate_name or crate_name, version or crate_name version)' })
    }
  })

  return { parsed, errors }
}

// Computed properties
const inputStats = computed(() => {
  const lines = textarea.value.split('\n').filter(line => line.trim().length > 0)
  return {
    lines: lines.length,
    hasContent: lines.length > 0
  }
})

const validationResult = computed(() => {
  if (!textarea.value.trim()) {
    return { valid: false, errors: [] }
  }
  const { parsed, errors } = parseInput(textarea.value)
  return { valid: errors.length === 0, errors, parsed }
})

const progressPercentage = computed(() => {
  if (progress.value.total === 0) return 0
  return Math.round((progress.value.current / progress.value.total) * 100)
})

const failedCount = computed(() => logs.value.filter(entry => !entry.ok).length)

// Auto-scroll the log window to the newest entry.
watch(
  () => logs.value.length,
  async () => {
    await nextTick()
    if (logContainer.value) {
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  }
)

// Batch create jobs
async function createBatchJobs() {
  if (!validationResult.value.valid || !selectedTaskId.value) {
    return
  }

  const { parsed } = validationResult.value

  loading.value = true
  progress.value = { current: 0, total: parsed.length }
  logs.value = []

  const CONCURRENCY = 16

  async function createOne({ crate_name, version }) {
    const label = `${crate_name}${version ? `@${version}` : ''}`
    try {
      const result = await api.createJob(crate_name, version, selectedTaskId.value)
      logs.value.push({ ok: true, text: `[OK] ${label} -> job #${result.job_id}` })
    } catch (err) {
      const message = err.response?.data?.detail || err.message
      logs.value.push({ ok: false, text: `[FAIL] ${label}: ${message}` })
    } finally {
      progress.value.current += 1
    }
  }

  for (let i = 0; i < parsed.length; i += CONCURRENCY) {
    const batch = parsed.slice(i, i + CONCURRENCY)
    await Promise.all(batch.map(createOne))
  }

  loading.value = false
  // Stay on the page so the user can read the creation log.
}

onMounted(() => {
  fetchTasks()
})
</script>

<template>
  <div class="max-w-4xl mx-auto">
    <h1 class="text-3xl font-bold text-gray-900 mb-8">Batch Create Jobs</h1>

    <!-- Instructions Card -->
    <div class="mb-6 bg-blue-50 border border-blue-200 rounded-lg p-4">
      <h3 class="text-sm font-medium text-blue-900 mb-2">How to use batch create</h3>
      <ul class="text-sm text-blue-800 space-y-1 list-disc list-inside mb-3">
        <li>Select the task to run, then enter one crate per line</li>
        <li>Format 1: <code class="bg-blue-100 px-1 rounded">crate_name</code> (uses latest version)</li>
        <li>Format 2: <code class="bg-blue-100 px-1 rounded">crate_name, version</code> (specific version)</li>
        <li>Format 3: <code class="bg-blue-100 px-1 rounded">crate_name version</code> (specific version, space-separated)</li>
        <li>Empty lines are ignored</li>
      </ul>
      <div class="text-xs text-blue-700 bg-blue-100 rounded p-2 font-mono">
        Example:<br>
        serde<br>
        tokio, 1.0.0<br>
        regex<br>
        actix-web 4.4.0<br>
        rayon, 1.5.0
      </div>
    </div>

    <!-- Input Area -->
    <div class="bento-card">
      <!-- Task Selector -->
      <div class="mb-4">
        <label for="task_id" class="block text-sm font-medium text-gray-700 mb-2">
          Task *
        </label>
        <div v-if="tasksLoading" class="flex items-center gap-2 text-sm text-gray-500">
          <span class="spinner border-blue-500"></span> Loading tasks...
        </div>
        <div
          v-else-if="tasksError"
          class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg text-sm flex items-center justify-between"
        >
          <span>Failed to load tasks: {{ tasksError }}</span>
          <button
            type="button"
            @click="fetchTasks"
            class="ml-4 px-3 py-1 text-sm font-medium text-red-700 bg-red-100 rounded-lg hover:bg-red-200 transition-colors"
          >
            Retry
          </button>
        </div>
        <div
          v-else-if="tasks.length === 0"
          class="bg-yellow-50 border border-yellow-200 text-yellow-800 px-4 py-3 rounded-lg text-sm"
        >
          No task definitions found.
          <router-link to="/tasks" class="font-medium underline hover:text-yellow-900">
            Create one on the task management page
          </router-link>
          first.
        </div>
        <select
          v-else
          id="task_id"
          v-model="selectedTaskId"
          required
          class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          :disabled="loading"
        >
          <option v-for="task in tasks" :key="task.id" :value="task.id">
            {{ task.name }} (#{{ task.id }})
          </option>
        </select>
      </div>

      <div class="mb-4">
        <div class="flex items-center justify-between mb-2">
          <label for="crate_list" class="block text-sm font-medium text-gray-700">
            Crate List
          </label>
          <span class="text-sm text-gray-500">
            {{ inputStats.lines }} {{ inputStats.lines === 1 ? 'line' : 'lines' }} entered
          </span>
        </div>
        <textarea
          id="crate_list"
          v-model="textarea"
          rows="12"
          placeholder="serde&#10;tokio, 1.0.0&#10;regex&#10;actix-web 4.4.0"
          class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono text-sm"
          :disabled="loading"
        ></textarea>
      </div>

      <!-- Format Errors -->
      <div v-if="inputStats.hasContent && !validationResult.valid && validationResult.errors.length > 0"
           class="mb-4 bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg">
        <p class="font-medium mb-2">Format errors found:</p>
        <ul class="text-sm space-y-1">
          <li v-for="error in validationResult.errors" :key="error.line">
            Line {{ error.line }}: "{{ error.text }}" - {{ error.reason }}
          </li>
        </ul>
      </div>

      <!-- Progress Bar + Creation Log -->
      <div v-if="progress.total > 0" class="mb-4">
        <div class="flex items-center justify-between text-sm text-gray-600 mb-2">
          <span>
            {{ loading ? 'Creating jobs...' : 'Done' }}
          </span>
          <span>
            {{ progress.current }} / {{ progress.total }}
            <span v-if="failedCount > 0" class="text-red-600 font-medium">
              ({{ failedCount }} failed)
            </span>
          </span>
        </div>
        <div class="w-full bg-gray-200 rounded-full h-3 overflow-hidden mb-3">
          <div
            class="h-full transition-all duration-300 ease-out"
            :class="loading ? 'bg-blue-600' : (failedCount > 0 ? 'bg-yellow-500' : 'bg-green-600')"
            :style="{ width: `${progressPercentage}%` }"
          ></div>
        </div>
        <div
          ref="logContainer"
          class="bg-gray-900 rounded-lg p-3 max-h-64 overflow-y-auto font-mono text-xs leading-relaxed"
        >
          <div
            v-for="(entry, index) in logs"
            :key="index"
            :class="entry.ok ? 'text-green-400' : 'text-red-400'"
          >{{ entry.text }}</div>
          <div v-if="logs.length === 0" class="text-gray-500">Waiting...</div>
        </div>
      </div>

      <!-- Action Buttons -->
      <div class="flex items-center justify-between">
        <button
          type="button"
          @click="router.push('/jobs')"
          class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
          :disabled="loading"
        >
          Cancel
        </button>
        <button
          type="button"
          @click="createBatchJobs"
          class="px-6 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed"
          :disabled="loading || !validationResult.valid || !inputStats.hasContent || !selectedTaskId"
        >
          Create {{ validationResult.parsed?.length || 0 }} {{ validationResult.parsed?.length === 1 ? 'Job' : 'Jobs' }}
        </button>
      </div>
    </div>
  </div>
</template>
