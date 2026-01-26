<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'
import LogViewer from '../components/LogViewer.vue'

const route = useRoute()
const router = useRouter()
const task = ref(null)
const loading = ref(true)
const error = ref(null)
const cancelling = ref(false)

const taskId = computed(() => route.params.id)

async function fetchTask() {
  try {
    task.value = await api.getTask(taskId.value)
    loading.value = false
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
    loading.value = false
  }
}

async function cancelTask() {
  if (!confirm('Are you sure you want to cancel this task?')) {
    return
  }

  cancelling.value = true

  try {
    await api.cancelTask(taskId.value)
    await fetchTask()
  } catch (err) {
    alert('Failed to cancel task: ' + (err.response?.data?.detail || err.message))
  } finally {
    cancelling.value = false
  }
}

function handleTaskUpdate(data) {
  if (data.task_id === parseInt(taskId.value)) {
    fetchTask()
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
  fetchTask()
  websocket.on('task_update', handleTaskUpdate)
  websocket.on('task_completed', handleTaskUpdate)
})

onUnmounted(() => {
  websocket.off('task_update', handleTaskUpdate)
  websocket.off('task_completed', handleTaskUpdate)
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
        @click="router.push('/tasks')"
        class="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700"
      >
        Back to Tasks
      </button>
    </div>

    <div v-else>
      <!-- Header -->
      <div class="flex items-center justify-between mb-8">
        <div>
          <div class="flex items-center gap-3">
            <h1 class="text-3xl font-bold text-gray-900">
              {{ task.crate_name }} <span class="text-gray-500">v{{ task.version }}</span>
            </h1>
            <span :class="['status-badge text-sm', `status-${task.status}`]">
              {{ task.status }}
            </span>
          </div>
          <p class="mt-2 text-sm text-gray-500">Task #{{ task.id }}</p>
        </div>
        <div class="flex items-center gap-3">
          <button
            @click="router.push('/tasks')"
            class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
          >
            Back to Tasks
          </button>
          <button
            v-if="task.status === 'running'"
            @click="cancelTask"
            :disabled="cancelling"
            class="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors disabled:bg-gray-400 flex items-center gap-2"
          >
            <span v-if="cancelling" class="spinner border-white"></span>
            {{ cancelling ? 'Cancelling...' : 'Cancel Task' }}
          </button>
        </div>
      </div>

      <!-- Stats Grid -->
      <div class="bento-grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
        <div class="bento-card">
          <p class="text-sm font-medium text-gray-600">Status</p>
          <p class="mt-2 text-2xl font-bold text-gray-900">{{ task.status }}</p>
        </div>
        <div class="bento-card">
          <p class="text-sm font-medium text-gray-600">Test Cases</p>
          <p class="mt-2 text-2xl font-bold text-gray-900">{{ task.case_count }}</p>
        </div>
        <div class="bento-card">
          <p class="text-sm font-medium text-gray-600">POCs</p>
          <p class="mt-2 text-2xl font-bold text-gray-900">{{ task.poc_count }}</p>
        </div>
        <div class="bento-card">
          <p class="text-sm font-medium text-gray-600">Runtime</p>
          <p class="mt-2 text-2xl font-bold text-gray-900">
            {{ formatDuration(task.started_at, task.finished_at) }}
          </p>
        </div>
        <div class="bento-card">
          <p class="text-sm font-medium text-gray-600">Exit Code</p>
          <p class="mt-2 text-2xl font-bold text-gray-900">
            {{ task.exit_code !== null ? task.exit_code : 'N/A' }}
          </p>
        </div>
      </div>

      <!-- Task Details -->
      <div class="bento-card mb-8">
        <h3 class="text-lg font-semibold text-gray-900 mb-4">Details</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <p class="text-sm font-medium text-gray-600">Created</p>
            <p class="mt-1 text-sm text-gray-900">{{ formatDate(task.created_at) }}</p>
          </div>
          <div>
            <p class="text-sm font-medium text-gray-600">Started</p>
            <p class="mt-1 text-sm text-gray-900">{{ formatDate(task.started_at) }}</p>
          </div>
          <div>
            <p class="text-sm font-medium text-gray-600">Finished</p>
            <p class="mt-1 text-sm text-gray-900">{{ formatDate(task.finished_at) }}</p>
          </div>
          <div v-if="task.error_message">
            <p class="text-sm font-medium text-gray-600">Error</p>
            <p class="mt-1 text-sm text-red-600">{{ task.error_message }}</p>
          </div>
        </div>
      </div>

      <!-- Log Viewer -->
      <LogViewer :task-id="taskId" :auto-scroll="task.status === 'running'" />
    </div>
  </div>
</template>
