<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import websocket from '../services/websocket'

const router = useRouter()
const runningTasks = ref([])
const pendingTasks = ref([])
const selectedRunningIds = ref(new Set())
const selectedPendingIds = ref(new Set())
const loading = ref(true)
const error = ref(null)

const runningSelectedCount = computed(() => selectedRunningIds.value.size)
const pendingSelectedCount = computed(() => selectedPendingIds.value.size)

const allRunningSelected = computed(() => {
  return runningTasks.value.length > 0 && runningTasks.value.every(t => selectedRunningIds.value.has(t.id))
})

const someRunningSelected = computed(() => {
  return selectedRunningIds.value.size > 0 && !allRunningSelected.value
})

const allPendingSelected = computed(() => {
  return pendingTasks.value.length > 0 && pendingTasks.value.every(t => selectedPendingIds.value.has(t.id))
})

const somePendingSelected = computed(() => {
  return selectedPendingIds.value.size > 0 && !allPendingSelected.value
})

function toggleSelectAllRunning() {
  if (allRunningSelected.value) {
    selectedRunningIds.value = new Set()
  } else {
    selectedRunningIds.value = new Set(runningTasks.value.map(t => t.id))
  }
}

function toggleSelectAllPending() {
  if (allPendingSelected.value) {
    selectedPendingIds.value = new Set()
  } else {
    selectedPendingIds.value = new Set(pendingTasks.value.map(t => t.id))
  }
}

function toggleRunningSelect(taskId) {
  const next = new Set(selectedRunningIds.value)
  if (next.has(taskId)) {
    next.delete(taskId)
  } else {
    next.add(taskId)
  }
  selectedRunningIds.value = next
}

function togglePendingSelect(taskId) {
  const next = new Set(selectedPendingIds.value)
  if (next.has(taskId)) {
    next.delete(taskId)
  } else {
    next.add(taskId)
  }
  selectedPendingIds.value = next
}

async function fetchQueue() {
  try {
    const data = await api.getQueue()
    runningTasks.value = data.running
    pendingTasks.value = data.pending
    loading.value = false
    const runningIds = new Set(runningTasks.value.map(t => t.id))
    const pendingIds = new Set(pendingTasks.value.map(t => t.id))
    selectedRunningIds.value = new Set([...selectedRunningIds.value].filter(id => runningIds.has(id)))
    selectedPendingIds.value = new Set([...selectedPendingIds.value].filter(id => pendingIds.has(id)))
  } catch (err) {
    error.value = err.message
    loading.value = false
  }
}

async function handlePinSelected() {
  const ids = [...selectedPendingIds.value]
  if (ids.length === 0) return
  try {
    const result = await api.batchSetPriority(ids, 100)
    selectedPendingIds.value = new Set()
    await fetchQueue()
    if (result.skipped?.length > 0) {
      alert(`Pinned ${result.updated.length} task(s). Skipped ${result.skipped.length} non-pending task(s).`)
    }
  } catch (err) {
    alert(`Failed to pin tasks: ${err.message}`)
  }
}

async function handleCancelSelected() {
  const ids = [...selectedRunningIds.value]
  if (ids.length === 0) return
  if (!confirm(`Cancel ${ids.length} running task(s)?`)) return
  try {
    const result = await api.batchCancel(ids)
    selectedRunningIds.value = new Set()
    await fetchQueue()
    if (result.skipped?.length > 0) {
      alert(`Cancelled ${result.cancelled.length} task(s). Skipped ${result.skipped.length} non-running task(s).`)
    }
  } catch (err) {
    alert(`Failed to cancel tasks: ${err.message}`)
  }
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

function getOrdinalSuffix(n) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return s[(v - 20) % 10] || s[v] || s[0]
}

function getQueuePosition(task, index) {
  if (task.priority > 0) return '⭐ Pinned'
  return `${index + 1}${getOrdinalSuffix(index + 1)}`
}

function viewTask(taskId) {
  router.push(`/tasks/${taskId}`)
}

onMounted(() => {
  fetchQueue()
  websocket.connect('/ws/dashboard')
  websocket.on('task_update', fetchQueue)
  websocket.on('task_created', fetchQueue)
  websocket.on('task_completed', fetchQueue)
})

onUnmounted(() => {
  websocket.off('task_update', fetchQueue)
  websocket.off('task_created', fetchQueue)
  websocket.off('task_completed', fetchQueue)
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Task Queue</h1>
      <div class="flex gap-3">
        <button
          v-if="pendingSelectedCount > 0"
          @click="handlePinSelected"
          class="px-4 py-2 text-sm font-medium text-white bg-orange-500 rounded-lg hover:bg-orange-600 transition-colors"
        >
          Pin Selected ({{ pendingSelectedCount }})
        </button>
        <button
          v-if="runningSelectedCount > 0"
          @click="handleCancelSelected"
          class="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors"
        >
          Cancel Selected ({{ runningSelectedCount }})
        </button>
      </div>
    </div>

    <div class="mb-4 flex items-center justify-between text-sm text-gray-600">
      <span>{{ runningTasks.length }} Running | {{ pendingTasks.length }} Pending</span>
    </div>

    <div v-if="loading" class="flex justify-center py-12">
      <div class="spinner border-blue-500"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg">
      Failed to load queue: {{ error }}
    </div>

    <div v-else-if="runningTasks.length === 0 && pendingTasks.length === 0" class="bento-card text-center py-12">
      <p class="text-gray-500">No tasks in queue.</p>
    </div>

    <div v-else class="bento-card overflow-x-auto">
      <table class="min-w-full divide-y divide-gray-200">
        <thead>
          <tr>
            <th class="px-4 py-3 text-left">
              <input
                v-if="runningTasks.length > 0"
                type="checkbox"
                :checked="allRunningSelected"
                :indeterminate="someRunningSelected"
                @change="toggleSelectAllRunning"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">ID</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Crate</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Version</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Runner ID</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Queue Position</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Runtime</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-200">
          <tr v-if="runningTasks.length > 0" class="bg-green-50">
            <td colspan="8" class="px-4 py-2 text-sm font-semibold text-green-800">
              ▶ Running Tasks ({{ runningTasks.length }})
            </td>
          </tr>
          <tr v-for="task in runningTasks" :key="task.id" class="hover:bg-gray-50 transition-colors cursor-pointer" @click="viewTask(task.id)">
            <td class="px-4 py-3 whitespace-nowrap" @click.stop>
              <input
                type="checkbox"
                :checked="selectedRunningIds.has(task.id)"
                @change="toggleRunningSelect(task.id)"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">#{{ task.id }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">{{ task.crate_name }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ task.version }}</td>
            <td class="px-4 py-3 whitespace-nowrap">
              <span class="status-badge status-running">{{ task.status }}</span>
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ task.runner_id || '-' }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">-</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ formatDuration(task.started_at, task.finished_at) }}</td>
          </tr>

          <tr v-if="pendingTasks.length > 0" class="bg-orange-50">
            <td colspan="8" class="px-4 py-2 text-sm font-semibold text-orange-800">
              ⏳ Pending Queue ({{ pendingTasks.length }})
            </td>
          </tr>
          <tr v-for="(task, idx) in pendingTasks" :key="task.id" class="hover:bg-gray-50 transition-colors cursor-pointer" :class="{ 'bg-yellow-50': task.priority > 0 }" @click="viewTask(task.id)">
            <td class="px-4 py-3 whitespace-nowrap" @click.stop>
              <input
                type="checkbox"
                :checked="selectedPendingIds.has(task.id)"
                @change="togglePendingSelect(task.id)"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">#{{ task.id }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">{{ task.crate_name }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ task.version }}</td>
            <td class="px-4 py-3 whitespace-nowrap">
              <span class="status-badge status-pending">{{ task.status }}</span>
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ task.runner_id || '-' }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium" :class="task.priority > 0 ? 'text-orange-600' : 'text-gray-500'">
              {{ getQueuePosition(task, idx) }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">-</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
