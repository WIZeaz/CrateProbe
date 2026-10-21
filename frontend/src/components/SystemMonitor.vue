<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import api from '../services/api'
import websocket from '../services/websocket'
import MeterBar from './ui/MeterBar.vue'

const stats = ref({
  cpu_percent: 0,
  memory_percent: 0,
  disk_percent: 0,
  memory_used_gb: 0,
  memory_total_gb: 0,
  disk_used_gb: 0,
  disk_total_gb: 0,
})
const loading = ref(true)
const error = ref(null)
let refreshInterval = null

async function fetchStats(isRefresh = false) {
  try {
    const data = await api.getSystemStats()
    stats.value = data
    if (!isRefresh) {
      loading.value = false
    }
  } catch (err) {
    error.value = err.message
    if (!isRefresh) {
      loading.value = false
    }
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

  refreshInterval = setInterval(() => {
    fetchStats(true)
  }, 5000)
})

onUnmounted(() => {
  websocket.off('system_stats', handleSystemUpdate)

  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
})
</script>

<template>
  <section class="instrument-panel" aria-label="System resources">
    <div class="panel-header">
      <h3 class="panel-title">System resources</h3>
    </div>

    <div
      v-if="loading"
      class="mt-4 space-y-3"
      aria-busy="true"
      aria-label="Loading system resources"
    >
      <div class="skeleton h-4 w-full"></div>
      <div class="skeleton h-4 w-full"></div>
      <div class="skeleton h-4 w-full"></div>
    </div>

    <div v-else-if="error" class="notice notice--error mt-4" role="alert">
      Couldn't load system stats. {{ error }}
    </div>

    <div v-else class="mt-4 space-y-3">
      <MeterBar label="CPU" :value="stats.cpu_percent" />

      <div>
        <MeterBar label="Memory" :value="stats.memory_percent" />
        <p class="mt-1 font-mono text-[11px] text-muted">
          {{ stats.memory_used_gb.toFixed(1) }} GB /
          {{ stats.memory_total_gb.toFixed(1) }} GB
        </p>
      </div>

      <div>
        <MeterBar label="Disk" :value="stats.disk_percent" />
        <p class="mt-1 font-mono text-[11px] text-muted">
          {{ stats.disk_used_gb.toFixed(1) }} GB /
          {{ stats.disk_total_gb.toFixed(1) }} GB
        </p>
      </div>
    </div>
  </section>
</template>
