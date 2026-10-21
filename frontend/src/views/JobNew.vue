<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'

const router = useRouter()
const form = ref({
  crate_name: '',
  version: '',
  task_id: null
})
const tasks = ref([])
const tasksLoading = ref(true)
const tasksError = ref(null)
const loading = ref(false)
const error = ref(null)

async function fetchTasks() {
  try {
    tasks.value = await api.getTasks()
    if (tasks.value.length > 0) {
      form.value.task_id = tasks.value[0].id
    }
  } catch (err) {
    tasksError.value = err.response?.data?.detail || err.message
  } finally {
    tasksLoading.value = false
  }
}

async function createJob() {
  if (!form.value.crate_name) {
    error.value = 'Crate name is required'
    return
  }

  if (!form.value.task_id) {
    error.value = 'Please select a task'
    return
  }

  loading.value = true
  error.value = null

  try {
    const version = form.value.version.trim() || null
    const result = await api.createJob(form.value.crate_name, version, form.value.task_id)

    // Redirect to job detail page
    router.push(`/jobs/${result.job_id}`)
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
    loading.value = false
  }
}

onMounted(() => {
  fetchTasks()
})
</script>

<template>
  <div class="max-w-2xl mx-auto">
    <h1 class="text-3xl font-bold text-gray-900 mb-8">Create New Job</h1>

    <div class="bento-card">
      <form @submit.prevent="createJob" class="space-y-6">
        <!-- Task Selector -->
        <div>
          <label for="task_id" class="block text-sm font-medium text-gray-700 mb-2">
            Task *
          </label>
          <div v-if="tasksLoading" class="flex items-center gap-2 text-sm text-gray-500">
            <span class="spinner border-blue-500"></span> Loading tasks...
          </div>
          <div
            v-else-if="tasksError"
            class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg text-sm"
          >
            Failed to load tasks: {{ tasksError }}
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
            v-model="form.task_id"
            required
            class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            :disabled="loading"
          >
            <option v-for="task in tasks" :key="task.id" :value="task.id">
              {{ task.name }} (#{{ task.id }})
            </option>
          </select>
          <p class="mt-1 text-sm text-gray-500">
            The task definition that will execute this job
          </p>
        </div>

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
            @click="router.push('/jobs')"
            class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
            :disabled="loading"
          >
            Cancel
          </button>
          <button
            type="submit"
            class="px-6 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center gap-2"
            :disabled="loading || tasks.length === 0"
          >
            <span v-if="loading" class="spinner border-white"></span>
            {{ loading ? 'Creating...' : 'Create Job' }}
          </button>
        </div>
      </form>
    </div>

    <!-- Info Card -->
    <div class="mt-6 bg-blue-50 border border-blue-200 rounded-lg p-4">
      <h3 class="text-sm font-medium text-blue-900 mb-2">What happens next?</h3>
      <ul class="text-sm text-blue-800 space-y-1 list-disc list-inside">
        <li>The job will be queued for an available runner</li>
        <li>A runner will execute the selected task against the crate</li>
        <li>Logs, watched files, and artifacts will be available on the job detail page</li>
        <li>You can monitor progress from the dashboard</li>
      </ul>
    </div>
  </div>
</template>
