<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import api from '../services/api'
import websocket from '../services/websocket'

const stats = ref({
  cpu_percent: 0,
  memory_percent: 0,
  disk_percent: 0,
  memory_used_gb: 0,
  memory_total_gb: 0,
  disk_used_gb: 0,
  disk_total_gb: 0
})
const loading = ref(true)
const error = ref(null)
let refreshInterval = null

async function fetchStats() {
  try {
    const data = await api.getSystemStats()
    stats.value = data
    loading.value = false
  } catch (err) {
    error.value = err.message
    loading.value = false
  }
}

function handleSystemUpdate(data) {
  if (data.stats) {
    stats.value = data.stats
  }
}

onMounted(() => {
  fetchStats()
  websocket.on('system_stats', handleSystemUpdate)

  // Auto-refresh every 5 seconds
  refreshInterval = setInterval(() => {
    fetchStats()
  }, 5000)
})

onUnmounted(() => {
  websocket.off('system_stats', handleSystemUpdate)

  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
})

function getProgressColor(percent) {
  if (percent >= 90) return 'bg-red-500'
  if (percent >= 75) return 'bg-yellow-500'
  return 'bg-green-500'
}
</script>

<template>
  <div class="bento-card">
    <h3 class="text-lg font-semibold text-gray-900 mb-4">System Resources</h3>

    <div v-if="loading" class="flex justify-center py-8">
      <div class="spinner"></div>
    </div>

    <div v-else-if="error" class="text-red-600 text-sm">
      Failed to load system stats: {{ error }}
    </div>

    <div v-else class="space-y-4">
      <!-- CPU Usage -->
      <div>
        <div class="flex justify-between items-center mb-1">
          <span class="text-sm font-medium text-gray-700">CPU</span>
          <span class="text-sm text-gray-600">{{ stats.cpu_percent.toFixed(1) }}%</span>
        </div>
        <div class="w-full bg-gray-200 rounded-full h-2">
          <div
            :class="['h-2 rounded-full transition-all', getProgressColor(stats.cpu_percent)]"
            :style="{ width: `${Math.min(stats.cpu_percent, 100)}%` }"
          ></div>
        </div>
      </div>

      <!-- Memory Usage -->
      <div>
        <div class="flex justify-between items-center mb-1">
          <span class="text-sm font-medium text-gray-700">Memory</span>
          <span class="text-sm text-gray-600">
            {{ stats.memory_used_gb.toFixed(1) }}GB / {{ stats.memory_total_gb.toFixed(1) }}GB
            ({{ stats.memory_percent.toFixed(1) }}%)
          </span>
        </div>
        <div class="w-full bg-gray-200 rounded-full h-2">
          <div
            :class="['h-2 rounded-full transition-all', getProgressColor(stats.memory_percent)]"
            :style="{ width: `${Math.min(stats.memory_percent, 100)}%` }"
          ></div>
        </div>
      </div>

      <!-- Disk Usage -->
      <div>
        <div class="flex justify-between items-center mb-1">
          <span class="text-sm font-medium text-gray-700">Disk</span>
          <span class="text-sm text-gray-600">
            {{ stats.disk_used_gb.toFixed(1) }}GB / {{ stats.disk_total_gb.toFixed(1) }}GB
            ({{ stats.disk_percent.toFixed(1) }}%)
          </span>
        </div>
        <div class="w-full bg-gray-200 rounded-full h-2">
          <div
            :class="['h-2 rounded-full transition-all', getProgressColor(stats.disk_percent)]"
            :style="{ width: `${Math.min(stats.disk_percent, 100)}%` }"
          ></div>
        </div>
      </div>
    </div>
  </div>
</template>
