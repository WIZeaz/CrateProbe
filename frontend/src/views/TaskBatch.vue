<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'

const router = useRouter()
const textarea = ref('')
const loading = ref(false)
const progress = ref({ current: 0, total: 0 })

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

// Batch create tasks
async function createBatchTasks() {
  if (!validationResult.value.valid) {
    return
  }

  const { parsed } = validationResult.value

  loading.value = true
  progress.value = { current: 0, total: parsed.length }

  // Create tasks sequentially
  for (let i = 0; i < parsed.length; i++) {
    const { crate_name, version } = parsed[i]
    try {
      await api.createTask(crate_name, version)
    } catch (err) {
      // Log error but continue with remaining tasks
      console.error(`Failed to create task for ${crate_name}:`, err)
    }
    progress.value.current = i + 1
  }

  // Navigate to task list after completion
  router.push('/tasks')
}
</script>

<template>
  <div class="max-w-4xl mx-auto">
    <h1 class="text-3xl font-bold text-gray-900 mb-8">Batch Create Tasks</h1>

    <!-- Instructions Card -->
    <div class="mb-6 bg-blue-50 border border-blue-200 rounded-lg p-4">
      <h3 class="text-sm font-medium text-blue-900 mb-2">How to use batch create</h3>
      <ul class="text-sm text-blue-800 space-y-1 list-disc list-inside mb-3">
        <li>Enter one crate per line</li>
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

      <!-- Action Buttons -->
      <div class="flex items-center justify-between">
        <button
          type="button"
          @click="router.push('/tasks')"
          class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
          :disabled="loading"
        >
          Cancel
        </button>
        <button
          type="button"
          @click="createBatchTasks"
          class="px-6 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed"
          :disabled="loading || !validationResult.valid || !inputStats.hasContent"
        >
          Create {{ validationResult.parsed?.length || 0 }} {{ validationResult.parsed?.length === 1 ? 'Task' : 'Tasks' }}
        </button>
      </div>
    </div>

    <!-- Progress Overlay -->
    <div v-if="loading" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div class="bg-white rounded-lg p-8 max-w-md w-full mx-4">
        <h3 class="text-lg font-semibold text-gray-900 mb-4">Creating Tasks...</h3>

        <!-- Progress Bar -->
        <div class="mb-4">
          <div class="flex items-center justify-between text-sm text-gray-600 mb-2">
            <span>Progress</span>
            <span>{{ progress.current }} / {{ progress.total }}</span>
          </div>
          <div class="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
            <div
              class="bg-blue-600 h-full transition-all duration-300 ease-out"
              :style="{ width: `${progressPercentage}%` }"
            ></div>
          </div>
        </div>

        <!-- Progress Text -->
        <p class="text-center text-gray-600">
          {{ progressPercentage }}% complete
        </p>
      </div>
    </div>
  </div>
</template>
