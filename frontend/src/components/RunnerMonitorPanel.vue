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

function barColorClass(value) {
  const num = Number(value)
  if (Number.isNaN(num)) return 'bg-gray-300'
  if (num >= 80) return 'bg-red-500'
  if (num >= 50) return 'bg-yellow-500'
  return 'bg-emerald-500'
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
        <div class="space-y-2 text-xs text-gray-700">
          <div>
            <div class="flex justify-between mb-0.5">
              <span>CPU</span>
              <span>{{ formatNumber(runner.latest_metrics?.cpu_percent) }}%</span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-1.5">
              <div
                class="h-1.5 rounded-full transition-all duration-500 ease-out"
                :class="barColorClass(runner.latest_metrics?.cpu_percent)"
                :style="{ width: Math.min(Number(runner.latest_metrics?.cpu_percent) || 0, 100) + '%' }"
              ></div>
            </div>
          </div>
          <div>
            <div class="flex justify-between mb-0.5">
              <span>MEM</span>
              <span>{{ formatNumber(runner.latest_metrics?.memory_percent) }}%</span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-1.5">
              <div
                class="h-1.5 rounded-full transition-all duration-500 ease-out"
                :class="barColorClass(runner.latest_metrics?.memory_percent)"
                :style="{ width: Math.min(Number(runner.latest_metrics?.memory_percent) || 0, 100) + '%' }"
              ></div>
            </div>
          </div>
          <div>
            <div class="flex justify-between mb-0.5">
              <span>DISK</span>
              <span>{{ formatNumber(runner.latest_metrics?.disk_percent) }}%</span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-1.5">
              <div
                class="h-1.5 rounded-full transition-all duration-500 ease-out"
                :class="barColorClass(runner.latest_metrics?.disk_percent)"
                :style="{ width: Math.min(Number(runner.latest_metrics?.disk_percent) || 0, 100) + '%' }"
              ></div>
            </div>
          </div>
          <div class="flex justify-between pt-0.5">
            <span>ACTIVE</span>
            <span class="font-medium">{{ runner.latest_metrics?.active_tasks ?? '--' }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
