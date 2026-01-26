<script setup>
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import api from '../services/api'

const props = defineProps({
  taskId: [String, Number],
  autoScroll: {
    type: Boolean,
    default: true
  }
})

const activeTab = ref('stdout')
const logs = ref({
  stdout: '',
  stderr: '',
  miri_report: ''
})
const loading = ref({
  stdout: false,
  stderr: false,
  miri_report: false
})
const error = ref(null)
const logContainer = ref(null)
let refreshInterval = null

const tabs = [
  { id: 'stdout', label: 'Standard Output' },
  { id: 'stderr', label: 'Standard Error' },
  { id: 'miri_report', label: 'Miri Report' }
]

const activeTabLabel = computed(() => {
  return tabs.find(tab => tab.id === activeTab.value)?.label || 'Log'
})

async function loadLog(logType, isRefresh = false) {
  // Skip if already loading (prevent duplicate requests)
  if (!isRefresh && loading.value[logType]) {
    return
  }

  // Only show loading spinner for initial load, not for auto-refresh
  if (!isRefresh) {
    loading.value[logType] = true
  }
  error.value = null

  try {
    const data = await api.getLog(props.taskId, logType, 1000)

    // Check if user is at bottom before updating (for auto-refresh)
    let wasAtBottom = false
    if (isRefresh && logContainer.value) {
      const threshold = 50 // pixels from bottom
      wasAtBottom = logContainer.value.scrollHeight - logContainer.value.scrollTop - logContainer.value.clientHeight < threshold
    }

    // Backend returns { lines: [...] } - join array into string
    if (data.lines && Array.isArray(data.lines)) {
      logs.value[logType] = data.lines.join('\n') || 'No content available'
    } else {
      logs.value[logType] = data.content || 'No content available'
    }

    // Auto-scroll only if:
    // 1. It's a manual load (not refresh) and autoScroll is enabled, OR
    // 2. It's an auto-refresh and user was already at the bottom
    if ((!isRefresh && props.autoScroll) || (isRefresh && wasAtBottom)) {
      scrollToBottom()
    }
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
    logs.value[logType] = `Error loading log: ${error.value}`
  } finally {
    if (!isRefresh) {
      loading.value[logType] = false
    }
  }
}

function startAutoRefresh() {
  // Refresh current tab every 5 seconds
  refreshInterval = setInterval(() => {
    loadLog(activeTab.value, true)
  }, 5000)
}

function stopAutoRefresh() {
  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
}

async function downloadLog() {
  try {
    const blob = await api.downloadLog(props.taskId, activeTab.value)
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url

    // Set filename based on log type
    let filename
    if (activeTab.value === 'miri_report') {
      filename = `task-${props.taskId}-miri_report.txt`
    } else {
      filename = `task-${props.taskId}-${activeTab.value}.log`
    }
    a.download = filename

    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
  } catch (err) {
    console.error('Failed to download log:', err)
    alert(`Failed to download log: ${err.response?.data?.detail || err.message}`)
  }
}

function scrollToBottom() {
  setTimeout(() => {
    if (logContainer.value) {
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  }, 100)
}

watch(activeTab, (newTab) => {
  if (!logs.value[newTab]) {
    loadLog(newTab)
  } else if (props.autoScroll) {
    scrollToBottom()
  }
})

onMounted(() => {
  loadLog(activeTab.value)
  startAutoRefresh()
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<template>
  <div class="bento-card">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text-lg font-semibold text-gray-900">Logs</h3>
      <button
        @click="downloadLog"
        class="px-3 py-1.5 text-sm font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
        :title="`Download complete ${activeTabLabel}`"
      >
        Download {{ activeTabLabel }}
      </button>
    </div>

    <!-- Tabs -->
    <div class="border-b border-gray-200 mb-4">
      <nav class="-mb-px flex space-x-8">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="activeTab = tab.id"
          :class="[
            'py-2 px-1 border-b-2 font-medium text-sm transition-colors',
            activeTab === tab.id
              ? 'border-blue-500 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
          ]"
        >
          {{ tab.label }}
        </button>
      </nav>
    </div>

    <!-- Log Content -->
    <div
      ref="logContainer"
      class="log-viewer"
      style="max-height: 500px; overflow-y: auto;"
    >
      <div v-if="loading[activeTab]" class="flex justify-center py-8">
        <div class="spinner border-white"></div>
      </div>
      <pre v-else class="text-sm">{{ logs[activeTab] || 'No content available' }}</pre>
    </div>
  </div>
</template>
