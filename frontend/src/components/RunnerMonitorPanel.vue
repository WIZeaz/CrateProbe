<script setup>
defineProps({
  runners: {
    type: Array,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
})

function formatNumber(value) {
  if (value === null || value === undefined) return '--'
  return Number(value).toFixed(1)
}

function statusClass(status) {
  if (status === 'online') return 'status-running'
  if (status === 'offline') return 'status-failed'
  if (status === 'disabled') return 'status-cancelled'
  return 'status-pending'
}
</script>

<template>
  <div class="bento-card">
    <h3 class="text-lg font-semibold text-gray-900 mb-4">Runner Monitor</h3>

    <div v-if="loading" class="flex justify-center py-8">
      <div class="spinner border-blue-500"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 border border-red-200 text-red-800 px-3 py-2 rounded-lg text-sm">
      {{ error }}
    </div>

    <div v-else-if="runners.length === 0" class="text-sm text-gray-500 py-6 text-center">
      No runners available.
    </div>

    <div v-else class="grid grid-cols-1 gap-3">
      <div
        v-for="runner in runners"
        :key="runner.runner_id"
        class="rounded-lg border border-gray-200 p-3"
      >
        <div class="flex items-center justify-between mb-2">
          <p class="text-sm font-semibold text-gray-900">{{ runner.runner_id }}</p>
          <span :class="['status-badge', statusClass(runner.health_status)]">{{ runner.health_status }}</span>
        </div>
        <div class="grid grid-cols-2 gap-2 text-xs text-gray-700">
          <div>CPU: {{ formatNumber(runner.latest_metrics?.cpu_percent) }}%</div>
          <div>MEM: {{ formatNumber(runner.latest_metrics?.memory_percent) }}%</div>
          <div>DISK: {{ formatNumber(runner.latest_metrics?.disk_percent) }}%</div>
          <div>ACTIVE: {{ runner.latest_metrics?.active_tasks ?? '--' }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
