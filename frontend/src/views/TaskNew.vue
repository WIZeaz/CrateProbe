<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'

const router = useRouter()
const form = ref({
  crate_name: '',
  version: ''
})
const loading = ref(false)
const error = ref(null)

async function createTask() {
  if (!form.value.crate_name) {
    error.value = 'Crate name is required'
    return
  }

  loading.value = true
  error.value = null

  try {
    const version = form.value.version.trim() || null
    const result = await api.createTask(form.value.crate_name, version)

    // Redirect to task detail page
    router.push(`/tasks/${result.task_id}`)
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
    loading.value = false
  }
}
</script>

<template>
  <div class="max-w-2xl mx-auto">
    <h1 class="text-3xl font-bold text-gray-900 mb-8">Create New Task</h1>

    <div class="bento-card">
      <form @submit.prevent="createTask" class="space-y-6">
        <!-- Crate Name -->
        <div>
          <label for="crate_name" class="block text-sm font-medium text-gray-700 mb-2">
            Crate Name *
          </label>
          <input
            id="crate_name"
            v-model="form.crate_name"
            type="text"
            required
            placeholder="e.g., serde, tokio, regex"
            class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            :disabled="loading"
          />
          <p class="mt-1 text-sm text-gray-500">
            Enter the name of the Rust crate from crates.io
          </p>
        </div>

        <!-- Version -->
        <div>
          <label for="version" class="block text-sm font-medium text-gray-700 mb-2">
            Version (optional)
          </label>
          <input
            id="version"
            v-model="form.version"
            type="text"
            placeholder="e.g., 1.0.0 (leave empty for latest)"
            class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            :disabled="loading"
          />
          <p class="mt-1 text-sm text-gray-500">
            Leave empty to use the latest version
          </p>
        </div>

        <!-- Error Message -->
        <div v-if="error" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg">
          {{ error }}
        </div>

        <!-- Submit Button -->
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
            type="submit"
            class="px-6 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center gap-2"
            :disabled="loading"
          >
            <span v-if="loading" class="spinner border-white"></span>
            {{ loading ? 'Creating...' : 'Create Task' }}
          </button>
        </div>
      </form>
    </div>

    <!-- Info Card -->
    <div class="mt-6 bg-blue-50 border border-blue-200 rounded-lg p-4">
      <h3 class="text-sm font-medium text-blue-900 mb-2">What happens next?</h3>
      <ul class="text-sm text-blue-800 space-y-1 list-disc list-inside">
        <li>The crate will be downloaded from crates.io</li>
        <li>Cargo RAPX will generate test cases and POCs</li>
        <li>Results will be available in real-time on the task detail page</li>
        <li>You can monitor progress from the dashboard</li>
      </ul>
    </div>
  </div>
</template>
